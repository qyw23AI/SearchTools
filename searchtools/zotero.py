"""Zotero integration through the Zotero local API (http://127.0.0.1:23119/api).

Supports both read and authenticated write operations (Zotero 10+), Better BibTeX citekey
resolution, and GB/T 7714 / Pandoc / BibTeX citation formatting.
Never touches zotero.sqlite directly.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse, urlencode
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('searchtools_zotero_mindmap', ROOT/'scripts/zotero_mindmap.py')
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)

ZoteroError = mm.ZoteroError
KEY = re.compile(r'^[A-Z0-9]{8}$')
PAPER_TYPES = {'.pdf', '.txt', '.md'}
BBT_JSONRPC_URL = 'http://127.0.0.1:23119/better-bibtex/json-rpc'


def check_key(key):
    key = str(key or '').strip()
    if not KEY.match(key):
        raise ValueError(f'Zotero 条目标识无效: {key}')
    return key


def _discover_local_api_key(server_id: str | None = None) -> str:
    """Discover a persistent Zotero local API key from zotero-native-mcp keystore or Zotero profile."""
    # 1. Check ~/.config/zotero-native-mcp/keys.json
    ks_path = Path.home() / '.config/zotero-native-mcp/keys.json'
    if ks_path.exists():
        try:
            ks = json.loads(ks_path.read_text(encoding='utf-8'))
            keys_map = ks.get('keys') or {}
            if server_id and keys_map.get(server_id):
                return str(keys_map[server_id])
            if keys_map:
                return str(next(iter(keys_map.values())))
        except Exception:
            pass
    # 2. Check ~/.zotero/zotero/*/localAPIKeys.json
    zot_root = Path.home() / '.zotero/zotero'
    if zot_root.is_dir():
        for p in zot_root.glob('*/localAPIKeys.json'):
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                keys_list = data.get('keys') or []
                for k in reversed(keys_list):
                    if k.get('remember') and k.get('key'):
                        return str(k['key'])
                if keys_list and keys_list[-1].get('key'):
                    return str(keys_list[-1]['key'])
            except Exception:
                pass
    return ''


def generate_fallback_citekey(meta: dict) -> str:
    """Generate a deterministic citekey (e.g. zhang2025spiking) when Better BibTeX is not yet loaded."""
    creators = meta.get('creators') or []
    author_part = 'anon'
    if creators:
        first_c = str(creators[0]).strip()
        # Extract last word or surname
        tokens = re.findall(r'[A-Za-z\u4e00-\u9fff]+', first_c)
        if tokens:
            author_part = tokens[-1].lower()
    year_part = str(meta.get('year') or 'nd')
    title_words = re.findall(r'[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}', str(meta.get('title') or ''))
    stop = {'with', 'from', 'that', 'this', 'using', 'based', 'study', 'research', 'review', 'survey'}
    title_part = ''
    for w in title_words:
        if w.lower() not in stop:
            title_part = w.lower()[:12]
            break
    raw = f"{author_part}{year_part}{title_part}"
    clean = re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', raw)
    return clean or f"zot{(meta.get('key') or 'item').lower()}"


def _normalize_creator_name(name: str) -> str:
    """Normalize 'FirstName LastName' when both parts are Chinese characters into 'LastNameFirstName'."""
    s = str(name or '').strip()
    m = re.match(r'^([\u4e00-\u9fff]{1,3})\s+([\u4e00-\u9fff]{1,2})$', s)
    if m:
        return f"{m.group(2)}{m.group(1)}"
    return s


def format_gbt7714_entry(meta: dict, index: int | None = None) -> str:
    """Format a bibliographic item according to GB/T 7714-2015 standard."""
    raw_creators = meta.get('creators') or []
    creators = [_normalize_creator_name(c) for c in raw_creators if str(c or '').strip()]
    if not creators:
        authors_str = '佚名'
    elif len(creators) <= 3:
        authors_str = ', '.join(creators)
    else:
        has_chinese = any(re.search(r'[\u4e00-\u9fff]', c) for c in creators[:3])
        et_al = ', 等' if has_chinese else ', et al'
        authors_str = ', '.join(creators[:3]) + et_al

    title = (meta.get('title') or '未命名文献').rstrip('.')
    itype = meta.get('type') or 'journalArticle'
    type_code_map = {
        'journalArticle': 'J',
        'conferencePaper': 'C',
        'book': 'M',
        'bookSection': 'M',
        'thesis': 'D',
        'report': 'R',
        'preprint': 'EB/OL',
        'webpage': 'EB/OL',
    }
    tcode = type_code_map.get(itype, 'J')
    pub = (meta.get('publication') or '').strip()
    year = (meta.get('year') or '').strip()
    vol = str(meta.get('volume') or '').strip()
    issue = str(meta.get('issue') or '').strip()
    pages = str(meta.get('pages') or '').strip()
    doi = str(meta.get('doi') or '').strip()

    if not pub and ('arxiv' in doi.lower() or 'arxiv' in str(meta.get('url') or '').lower()):
        pub = 'arXiv preprint'
        tcode = 'EB/OL'

    pub_parts = []
    if pub:
        pub_parts.append(pub)
    if year:
        pub_parts.append(year)
    vol_issue = ''
    if vol and issue:
        vol_issue = f"{vol}({issue})"
    elif vol:
        vol_issue = vol
    elif issue:
        vol_issue = f"({issue})"
    if vol_issue:
        if pages:
            pub_parts.append(f"{vol_issue}: {pages}")
        else:
            pub_parts.append(vol_issue)
    elif pages:
        pub_parts.append(pages)

    tail = ', '.join(pub_parts)
    entry = f"{authors_str}. {title}[{tcode}]. {tail}." if tail else f"{authors_str}. {title}[{tcode}]."
    if doi:
        entry += f" DOI:{doi}."
    if index is not None:
        return f"[{index}] {entry}"
    return entry


class Library:
    def __init__(self, base_url=mm.DEFAULT_BASE_URL, timeout=10.0):
        self.client = mm.ZoteroClient(base_url=base_url, timeout=timeout)
        self._server_id: str | None = None
        self._api_key: str | None = None

    def _get(self, path, raw=False, **query):
        q = '&'.join(f'{k}={quote(str(v))}' for k, v in query.items() if v not in (None, ''))
        data, headers = self.client._request(self.client._url(path) + ('?' + q if q else ''), raw=raw)
        if headers.get('Zotero-Server-ID'):
            self._server_id = headers['Zotero-Server-ID']
        return data, headers

    def _write_request(self, method: str, path: str, body=None, form: dict | None = None,
                       raw_bytes: bytes | None = None, headers_extra: dict | None = None,
                       expect_text: bool = False):
        """Send an authenticated write request (POST, PATCH, DELETE) to Zotero local API."""
        if not self._server_id:
            try:
                h = self.client.ping()
                self._server_id = h.get('Zotero-Server-ID')
            except Exception:
                pass
        if not self._api_key:
            self._api_key = _discover_local_api_key(self._server_id)

        url = self.client._url(path) if not path.startswith('http') else path
        headers = {
            'Zotero-API-Version': '3',
            'User-Agent': 'SearchTools-Zotero-v3/1.0',
        }
        if self._server_id:
            headers['Zotero-Server-ID'] = self._server_id
        if self._api_key:
            headers['Zotero-API-Key'] = self._api_key
        if headers_extra:
            headers.update(headers_extra)

        payload = None
        if raw_bytes is not None:
            payload = raw_bytes
            headers.setdefault('Content-Type', 'application/octet-stream')
        elif form is not None:
            payload = urlencode({k: str(v) for k, v in form.items()}).encode('utf-8')
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'

        req = Request(url, data=payload, method=method, headers=headers)
        try:
            with urlopen(req, timeout=self.client.timeout) as resp:
                resp_headers = dict(resp.headers)
                if resp_headers.get('Zotero-Server-ID'):
                    self._server_id = resp_headers['Zotero-Server-ID']
                raw_out = resp.read().decode('utf-8', errors='replace')
                if expect_text or not raw_out.strip():
                    return raw_out, resp_headers
                return json.loads(raw_out), resp_headers
        except HTTPError as err:
            raise self.client._friendly_error(err, url) from err
        except URLError as err:
            raise ZoteroError('无法连接 Zotero 本地 API，请确认 Zotero 正在运行。') from err

    def status(self):
        try:
            headers = self.client.ping()
            self._server_id = headers.get('Zotero-Server-ID', '')
        except ZoteroError as e:
            return {'connected': False, 'error': str(e)}
        try:
            _, h = self._get('items/top', raw=True, limit=1, format='keys')
            total = h.get('Total-Results')
        except Exception:
            total = None
        bbt_alive = False
        try:
            req = Request(BBT_JSONRPC_URL, data=json.dumps({'jsonrpc': '2.0', 'method': 'api.ready', 'id': 1}).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
            with urlopen(req, timeout=1.5) as r:
                bbt_alive = r.status == 200
        except Exception:
            bbt_alive = False
        return {
            'connected': True,
            'version': headers.get('X-Zotero-Version', ''),
            'server_id': self._server_id,
            'total': int(total) if total and str(total).isdigit() else None,
            'better_bibtex': bbt_alive,
            'write_authorized': bool(_discover_local_api_key(self._server_id)),
        }

    def get_citekeys(self, item_keys: list[str]) -> dict[str, str]:
        """Resolve Better BibTeX citekeys for item keys, falling back to deterministic citekeys."""
        if not item_keys:
            return {}
        result: dict[str, str] = {}
        try:
            req = Request(
                BBT_JSONRPC_URL,
                data=json.dumps({
                    'jsonrpc': '2.0',
                    'method': 'item.citationkey',
                    'params': [item_keys],
                    'id': 1
                }).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urlopen(req, timeout=2.5) as resp:
                body = json.loads(resp.read().decode('utf-8'))
                if isinstance(body.get('result'), dict):
                    for k, v in body['result'].items():
                        if v:
                            result[k] = str(v)
        except Exception:
            pass

        for k in item_keys:
            if k not in result:
                try:
                    raw = self.raw_item(k)
                    extra = str((raw.get('data') or {}).get('extra') or '')
                    m = re.search(r'Citation Key:\s*(\S+)', extra, re.IGNORECASE)
                    if m:
                        result[k] = m.group(1).strip()
                    else:
                        result[k] = generate_fallback_citekey(self.summary(raw))
                except Exception:
                    result[k] = f"zot_{k.lower()}"
        return result

    def collections(self):
        data, _ = self._get('collections', limit=500)
        rows = [{'key': c['key'], 'name': c['data'].get('name', ''), 'parent': c['data'].get('parentCollection') or '',
                 'count': (c.get('meta') or {}).get('numItems', 0), 'version': c.get('version', 0)} for c in data]
        return sorted(rows, key=lambda r: r['name'].casefold())

    def ensure_collection_path(self, path_str: str) -> str:
        """Find or create a nested collection path like 'AI 补充文献/脉冲强化学习综述' and return its key."""
        parts = [p.strip() for p in str(path_str or '').split('/') if p.strip()]
        if not parts:
            raise ValueError('分类名称不能为空')
        colls = self.collections()
        parent_key = ''
        for part in parts:
            found = next((c for c in colls if c['name'].casefold() == part.casefold() and (c['parent'] or '') == parent_key), None)
            if found:
                parent_key = found['key']
            else:
                body = [{'name': part, 'parentCollection': parent_key or False}]
                res, _ = self._write_request('POST', 'collections', body=body)
                created = next(iter((res.get('successful') or {}).values()), None)
                if not created or not created.get('key'):
                    raise ZoteroError(f'创建 Zotero 分类「{part}」失败: {res}')
                parent_key = created['key']
                colls.append({'key': parent_key, 'name': part, 'parent': '', 'count': 0})
        return parent_key

    def tags(self):
        data, _ = self._get('tags', limit=300)
        return sorted({t['tag'] for t in data if t.get('tag')}, key=str.casefold)

    def searches(self):
        data, _ = self._get('searches')
        return [{'key': s['key'], 'name': s['data'].get('name', '')} for s in data]

    @staticmethod
    def summary(obj):
        meta = mm.item_meta(obj)
        data = obj.get('data') or {}
        att = (obj.get('links') or {}).get('attachment') or {}
        att_href = att.get('href') or ''
        att_key = att_href.rstrip('/').split('/')[-1] if '/items/' in att_href else ''
        summary_dict = {
            **{k: meta[k] for k in ('key', 'title', 'year', 'creators', 'doi', 'url', 'tags', 'collections')},
            'abstract': meta['abstract'][:600],
            'type': data.get('itemType', ''),
            'publication': data.get('publicationTitle') or data.get('university') or data.get('proceedingsTitle') or data.get('publisher') or '',
            'volume': data.get('volume', ''),
            'issue': data.get('issue', ''),
            'pages': data.get('pages', ''),
            'has_pdf': att.get('attachmentType') == 'application/pdf',
            'attachment_key': att_key if KEY.match(att_key) else '',
            'children': (obj.get('meta') or {}).get('numChildren', 0),
        }
        extra = str(data.get('extra') or '')
        m = re.search(r'Citation Key:\s*(\S+)', extra, re.IGNORECASE)
        summary_dict['citekey'] = m.group(1).strip() if m else generate_fallback_citekey(summary_dict)
        return summary_dict

    def items(self, collection='', q='', mode='titleCreatorYear', tag='', search='', limit=200):
        mode = 'everything' if mode == 'everything' else 'titleCreatorYear'
        query = {'q': q, 'qmode': mode if q else '', 'tag': tag, 'limit': min(int(limit), 500), 'sort': 'dateModified', 'direction': 'desc'}
        if search:
            data, _ = self._get(f'searches/{check_key(search)}/items', **query)
            data = [d for d in data if not (d.get('data') or {}).get('parentItem')
                    and (d.get('data') or {}).get('itemType') not in ('attachment', 'note', 'annotation')]
        elif collection:
            # Allow passing either collection 8-char key or collection name
            col_key = collection.strip()
            if not KEY.match(col_key):
                colls = self.collections()
                matched = next((c['key'] for c in colls if c['name'].casefold() == col_key.casefold() or col_key.casefold() in c['name'].casefold()), None)
                if matched:
                    col_key = matched
            data, _ = self._get(f'collections/{check_key(col_key)}/items/top', **query)
        else:
            data, _ = self._get('items/top', **query)
        return [self.summary(o) for o in data if (o.get('data') or {}).get('itemType') not in ('attachment', 'note', 'annotation')]

    def find_existing_item(self, doi: str = '', arxiv_id: str = '', title: str = '') -> dict | None:
        """Check whether an item with the same DOI, arXiv ID, or exact title already exists in Zotero."""
        clean_doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', str(doi or '').strip()).lower()
        clean_arxiv = re.sub(r'^arxiv:', '', str(arxiv_id or '').strip(), flags=re.I).split('v')[0].lower()
        norm_title = re.sub(r'\W+', '', str(title or '').lower())

        all_top = self.items(limit=500)
        for it in all_top:
            it_doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', str(it.get('doi') or '').strip()).lower()
            if clean_doi and it_doi and clean_doi == it_doi:
                return it
            if clean_arxiv and (clean_arxiv in it_doi or clean_arxiv in str(it.get('url') or '').lower()):
                return it
            if norm_title and len(norm_title) >= 10:
                it_norm = re.sub(r'\W+', '', str(it.get('title') or '').lower())
                if it_norm == norm_title:
                    return it
        return None

    def raw_item(self, key):
        data, _ = self._get(f'items/{check_key(key)}')
        return data

    def item(self, key):
        obj = self.raw_item(key)
        detail = self.summary(obj)
        detail['abstract'] = mm.item_meta(obj)['abstract']
        children = self.client.children(key)
        detail['attachments'] = [{'key': c['key'], 'title': c['data'].get('title', ''), 'type': c['data'].get('contentType', ''),
                                  'filename': c['data'].get('filename', '')}
                                 for c in children if c['data'].get('itemType') == 'attachment']
        notes, annotations = mm.collect_annotations(self.client.children, key)
        detail['notes'] = [{'key': n['key'], 'text': mm.strip_html(n['data'].get('note', ''))[:4000]} for n in notes]
        detail['annotations'] = [self._annotation(a) for a in sorted(annotations, key=lambda a: mm.annotation_sort(mm.data_of(a)))]
        return detail

    @staticmethod
    def _annotation(a):
        d = mm.data_of(a)
        return {'key': a.get('key', ''), 'type': d.get('annotationType', ''), 'text': mm.normalize_space(d.get('annotationText', '')),
                'comment': mm.normalize_space(d.get('annotationComment', '')), 'color': d.get('annotationColor', ''),
                'label': mm.color_label(d.get('annotationColor')), 'page': mm.annotation_page(d)}

    def attachment_info(self, key) -> tuple[Path | None, str]:
        """Return (local_path, attachment_key) for the first readable PDF/TXT/MD attachment of an item."""
        children = self.client.children(check_key(key))
        atts = [c for c in children if c['data'].get('itemType') == 'attachment' and c['data'].get('linkMode') != 'linked_url']
        atts.sort(key=lambda c: c['data'].get('contentType') != 'application/pdf')
        for att in atts:
            att_key = att.get('key', '')
            try:
                url = self.client.file_url(att_key)
            except ZoteroError:
                continue
            u = urlparse(url)
            if u.scheme != 'file':
                continue
            path = Path(unquote(u.path))
            if path.suffix.lower() in PAPER_TYPES and path.is_file():
                return path, att_key
        return None, ''

    def attachment_path(self, key):
        """Resolve the first readable PDF/TXT/MD attachment of an item to a local path."""
        path, _ = self.attachment_info(key)
        return path

    def create_bibliographic_item(self, item_data: dict, collection_keys: list[str] | None = None) -> str:
        """Create a bibliographic item in Zotero and return its 8-char item_key."""
        payload = dict(item_data)
        payload.setdefault('itemType', 'journalArticle')
        if collection_keys:
            payload['collections'] = [check_key(k) for k in collection_keys if k]
        res, _ = self._write_request('POST', 'items', body=[payload])
        created = next(iter((res.get('successful') or {}).values()), None)
        if not created or not created.get('key'):
            failed = next(iter((res.get('failed') or {}).values()), {})
            raise ZoteroError(f"创建 Zotero 文献条目失败: {failed.get('message') or res}")
        return str(created['key'])

    def attach_pdf_file(self, parent_key: str, file_path: Path, title: str | None = None, mode: str = 'imported') -> str:
        """Attach a downloaded PDF file to an existing Zotero item using Zotero's 3-phase upload or linked mode."""
        parent_key = check_key(parent_key)
        file_path = Path(file_path).resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f'PDF 文件不存在: {file_path}')

        filename = file_path.name
        att_item = {
            'itemType': 'attachment',
            'parentItem': parent_key,
            'linkMode': 'linked_file' if mode == 'linked' else 'imported_file',
            'title': title or filename,
            'contentType': 'application/pdf',
        }
        if mode == 'linked':
            att_item['path'] = str(file_path)
        else:
            att_item['filename'] = filename

        res, _ = self._write_request('POST', 'items', body=[att_item])
        created = next(iter((res.get('successful') or {}).values()), None)
        if not created or not created.get('key'):
            raise ZoteroError(f'创建 PDF 附件条目失败: {res}')
        att_key = str(created['key'])
        if mode == 'linked':
            return att_key

        raw_bytes = file_path.read_bytes()
        md5_hex = hashlib.md5(raw_bytes).hexdigest()
        stat = file_path.stat()
        auth_res, _ = self._write_request(
            'POST',
            f'items/{att_key}/file',
            form={
                'md5': md5_hex,
                'filename': filename,
                'filesize': stat.st_size,
                'mtime': round(stat.st_mtime * 1000),
                'contentType': 'application/pdf',
            },
            headers_extra={'If-None-Match': '*'},
        )
        if isinstance(auth_res, dict) and auth_res.get('exists') == 1:
            return att_key
        upload_url = (auth_res or {}).get('url')
        upload_key = (auth_res or {}).get('uploadKey')
        if not upload_url or not upload_key:
            return att_key

        self._write_request(
            'POST',
            upload_url,
            raw_bytes=raw_bytes,
            headers_extra={'Content-Type': 'application/pdf'},
            expect_text=True,
        )
        self._write_request(
            'POST',
            f'items/{att_key}/file',
            form={'upload': upload_key},
            headers_extra={'If-None-Match': '*'},
            expect_text=True,
        )
        return att_key

    def write_evidence_note(self, parent_key: str, cards: list[dict]) -> dict:
        """Create or update a dedicated '[PaperQA2 实证卡片]' child note under a Zotero item."""
        parent_key = check_key(parent_key)
        children = self.client.children(parent_key)
        existing_note = None
        for ch in children:
            d = ch.get('data') or {}
            if d.get('itemType') == 'note' and 'PaperQA2 实证卡片' in (d.get('note') or ''):
                existing_note = ch
                break

        blocks = [
            '<h2>🗂️ PaperQA2 实证卡片与引用溯源</h2>',
            f'<p><em>更新时间：{time.strftime("%Y-%m-%d %H:%M:%S")} · 由 DeepTutor &amp; PaperQA2 自动核验写回</em></p>',
            '<hr/>'
        ]
        for c in cards:
            cid = escape(str(c.get('id') or ''))
            claim = escape(str(c.get('claim') or c.get('question') or '学术论断'))
            summary = escape(str(c.get('summary') or ''))
            quote_txt = escape(str(c.get('quote') or ''))
            page = str(c.get('page') or '')
            score = c.get('score') or 0
            att_key = str(c.get('attachment_key') or '')
            zot_link = c.get('zotero_link') or (
                f"zotero://open-pdf/library/items/{att_key}?page={page}" if att_key and page
                else f"zotero://select/library/items/{parent_key}"
            )
            page_badge = f' · <a href="{escape(zot_link)}">打开 PDF 第 {escape(page)} 页</a>' if page else f' · <a href="{escape(zot_link)}">在 Zotero 中定位</a>'
            blocks.append(
                f'<div>'
                f'<p><strong>[{cid}] 核验问题/论断：</strong>{claim} '
                f'<span>(相关度: {score}/10{page_badge})</span></p>'
                f'<p><strong>核心结论：</strong>{summary}</p>'
                f'<blockquote>{quote_txt}</blockquote>'
                f'</div><hr/>'
            )
        html_content = '\n'.join(blocks)

        if existing_note:
            note_key = existing_note['key']
            version = existing_note.get('version', 0)
            self._write_request(
                'PATCH',
                f'items/{note_key}',
                body={'version': version, 'note': html_content, 'tags': [{'tag': 'PaperQA2-Evidence'}, {'tag': 'DeepTutor'}]}
            )
            return {'note_key': note_key, 'action': 'updated', 'parent_key': parent_key, 'cards_count': len(cards)}
        else:
            note_item = {
                'itemType': 'note',
                'parentItem': parent_key,
                'note': html_content,
                'tags': [{'tag': 'PaperQA2-Evidence'}, {'tag': 'DeepTutor'}],
            }
            res, _ = self._write_request('POST', 'items', body=[note_item])
            created = next(iter((res.get('successful') or {}).values()), None)
            note_key = created.get('key', '') if created else ''
            return {'note_key': note_key, 'action': 'created', 'parent_key': parent_key, 'cards_count': len(cards)}

    def collection_items(self, key, recursive=True):
        key = check_key(key)
        keys = [key]
        if recursive:
            cols = self.collections()
            frontier = [key]
            while frontier:
                parent = frontier.pop()
                kids = [c['key'] for c in cols if c['parent'] == parent]
                keys += kids
                frontier += kids
        seen, out = set(), []
        for k in keys:
            data, _ = self._get(f'collections/{k}/items/top', limit=500)
            for o in data:
                if o['key'] not in seen:
                    seen.add(o['key'])
                    out.append(o)
        return out

    def reading_notes(self, key):
        """Markdown of the user's own annotations and notes, clearly marked as not paper text."""
        detail = self.item(key)
        if not detail['annotations'] and not detail['notes']:
            return None
        lines = [f"# 阅读笔记：{detail['title']}", '',
                 '> 以下为我在 Zotero 中的标注与批注，代表个人理解，不是论文原文。引用论文结论时请以原文为准。', '',
                 f"- 文献：{detail['title']}（{detail['year'] or '年份未知'}）", f"- Zotero 条目：{detail['key']}", '']
        groups = {}
        for a in detail['annotations']:
            groups.setdefault(a['label'], []).append(a)
        for label, rows in groups.items():
            lines += [f'## {label}', '']
            for a in rows:
                page = f"（p.{a['page']}）" if a['page'] else ''
                if a['text']:
                    lines.append(f"- 原文摘录{page}：“{a['text']}”")
                if a['comment']:
                    lines.append(f"  - 我的批注：{a['comment']}" if a['text'] else f"- 我的批注{page}：{a['comment']}")
            lines.append('')
        if detail['notes']:
            lines += ['## 笔记', '']
            lines += [n['text'] + '\n' for n in detail['notes'] if n['text']]
        return '\n'.join(lines)

    notes_markdown = reading_notes

    def mindmap(self, collection='', items=(), group_by='paper', include_abstract=False):
        group_by = group_by if group_by in ('paper', 'color', 'year') else 'paper'
        if items:
            objs = [self.raw_item(k) for k in items]
            client = _Subset(self.client, objs)
            return mm.build_tree(client, None, group_by, include_abstract)
        return mm.build_tree(self.client, check_key(collection) if collection else None, group_by, include_abstract)


class _Subset:
    """Adapter so the prototype's build_tree can render a hand-picked set of items."""
    def __init__(self, client, objs):
        self.client, self.objs = client, objs

    def items(self, collection_key=None):
        return self.objs

    def children(self, key):
        return self.client.children(key)

    def collection(self, key):
        return {'data': {'name': '选中文献'}}


render_markdown = mm.render_markdown
render_freemind = mm.render_freemind
