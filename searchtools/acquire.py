"""Academic Literature Acquisition Engine for SearchTools v3.

Supports candidate staging (max 20 per batch, requiring user confirmation in UI before Zotero import),
metadata enrichment via OpenAlex / Crossref / arXiv APIs, Open Access PDF download via Unpaywall / arXiv,
and deduplicated Zotero library import into `AI 补充文献/<课题>`.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .zotero import Library

UNPAYWALL_EMAIL = "research-workspace@localhost.org"
MAX_ACQUIRE_BATCH = 20


def _http_json(url: str, timeout: float = 10.0) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': f'SearchTools-LiteratureAcquire/3.0 (mailto:{UNPAYWALL_EMAIL})',
            'Accept': 'application/json',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8', errors='replace'))
    except Exception:
        return None


def normalize_identifier(raw_id: str) -> tuple[str, str]:
    """Classify an identifier into ('doi', clean_doi), ('arxiv', clean_arxiv), or ('url', url)."""
    s = str(raw_id or '').strip()
    if not s:
        return 'unknown', ''
    # DOI pattern
    doi_m = re.search(r'(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)', s)
    if doi_m:
        clean_doi = doi_m.group(1).rstrip('.')
        # Check if it's an arXiv DOI like 10.48550/arXiv.2301.12345
        arxiv_in_doi = re.search(r'10\.48550/arxiv\.(\d{4}\.\d{4,5})', clean_doi, re.I)
        if arxiv_in_doi:
            return 'arxiv', arxiv_in_doi.group(1)
        return 'doi', clean_doi

    # arXiv pattern
    arxiv_m = re.search(r'(?:arxiv[:/\s]+|arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}(?:v\d+)?)', s, re.I)
    if arxiv_m:
        return 'arxiv', re.sub(r'v\d+$', '', arxiv_m.group(1))

    if s.startswith(('http://', 'https://')):
        return 'url', s
    return 'query', s


def resolve_arxiv_metadata(arxiv_id: str) -> dict | None:
    """Resolve metadata and direct PDF URL from arXiv API."""
    clean_id = re.sub(r'v\d+$', '', str(arxiv_id).strip())
    url = f"http://export.arxiv.org/api/query?id_list={urllib.parse.quote(clean_id)}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'SearchTools/3.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_text = resp.read().decode('utf-8', errors='replace')
        root = ET.fromstring(xml_text)
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        entry = root.find('a:entry', ns)
        if entry is None:
            return None
        title = re.sub(r'\s+', ' ', (entry.findtext('a:title', '', ns) or '').strip())
        if not title or title.lower() == 'error':
            return None
        summary = re.sub(r'\s+', ' ', (entry.findtext('a:summary', '', ns) or '').strip())
        published = (entry.findtext('a:published', '', ns) or '')[:4]
        authors = []
        for a in entry.findall('a:author', ns):
            name = (a.findtext('a:name', '', ns) or '').strip()
            if name:
                authors.append(name)
        doi = f"10.48550/arXiv.{clean_id}"
        pdf_url = f"https://arxiv.org/pdf/{clean_id}.pdf"
        return {
            'title': title,
            'authors': authors,
            'year': published,
            'doi': doi,
            'arxiv_id': clean_id,
            'publication': 'arXiv preprint',
            'abstract': summary[:1200],
            'url': f"https://arxiv.org/abs/{clean_id}",
            'pdf_url': pdf_url,
            'oa_status': 'arxiv_oa',
            'item_type': 'preprint',
        }
    except Exception:
        return None


def resolve_doi_metadata(doi: str) -> dict | None:
    """Resolve DOI metadata and OA PDF URL using OpenAlex, Crossref, and Unpaywall."""
    clean_doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', str(doi).strip())
    # 1. Try OpenAlex first (gives both metadata + best_oa_location PDF)
    oa_data = _http_json(f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(clean_doi)}")
    meta = {
        'title': '',
        'authors': [],
        'year': '',
        'doi': clean_doi,
        'arxiv_id': '',
        'publication': '',
        'volume': '',
        'issue': '',
        'pages': '',
        'abstract': '',
        'url': f"https://doi.org/{clean_doi}",
        'pdf_url': '',
        'oa_status': 'closed',
        'item_type': 'journalArticle',
    }
    if isinstance(oa_data, dict) and oa_data.get('title'):
        meta['title'] = str(oa_data.get('title') or '').strip()
        meta['year'] = str(oa_data.get('publication_year') or '')
        for auth in (oa_data.get('authorships') or []):
            dn = ((auth.get('author') or {}).get('display_name') or '').strip()
            if dn:
                meta['authors'].append(dn)
        loc = oa_data.get('primary_location') or {}
        src = loc.get('source') or {}
        meta['publication'] = str(src.get('display_name') or '')
        biblio = oa_data.get('biblio') or {}
        meta['volume'] = str(biblio.get('volume') or '')
        meta['issue'] = str(biblio.get('issue') or '')
        fp, lp = biblio.get('first_page'), biblio.get('last_page')
        if fp and lp and fp != lp:
            meta['pages'] = f"{fp}-{lp}"
        elif fp:
            meta['pages'] = str(fp)
        best_oa = oa_data.get('best_oa_location') or {}
        oa_info = oa_data.get('open_access') or {}
        pdf_u = best_oa.get('pdf_url') or oa_info.get('oa_url') or ''
        if pdf_u:
            meta['pdf_url'] = str(pdf_u)
            meta['oa_status'] = 'open_access'
        ids = oa_data.get('ids') or {}
        if ids.get('arxiv'):
            m_arx = re.search(r'(\d{4}\.\d{4,5})', str(ids['arxiv']))
            if m_arx:
                meta['arxiv_id'] = m_arx.group(1)
                if not meta['pdf_url']:
                    meta['pdf_url'] = f"https://arxiv.org/pdf/{meta['arxiv_id']}.pdf"
                    meta['oa_status'] = 'arxiv_oa'

    # 2. Fallback or supplement via Crossref
    if not meta['title']:
        cr = _http_json(f"https://api.crossref.org/works/{urllib.parse.quote(clean_doi)}")
        msg = (cr or {}).get('message') if isinstance(cr, dict) else None
        if isinstance(msg, dict):
            titles = msg.get('title') or []
            meta['title'] = str(titles[0] if titles else '').strip()
            for a in (msg.get('author') or []):
                given = (a.get('given') or '').strip()
                family = (a.get('family') or '').strip()
                full = f"{given} {family}".strip() or (a.get('name') or '').strip()
                if full:
                    meta['authors'].append(full)
            issued = ((msg.get('issued') or {}).get('date-parts') or [[None]])[0]
            if issued and issued[0]:
                meta['year'] = str(issued[0])
            ct = msg.get('container-title') or []
            if ct:
                meta['publication'] = str(ct[0])
            meta['volume'] = str(msg.get('volume') or '')
            meta['issue'] = str(msg.get('issue') or '')
            meta['pages'] = str(msg.get('page') or '')

    # 3. Try Unpaywall if no PDF URL found yet
    if not meta['pdf_url']:
        upw = _http_json(f"https://api.unpaywall.org/v2/{urllib.parse.quote(clean_doi)}?email={UNPAYWALL_EMAIL}")
        if isinstance(upw, dict):
            best = upw.get('best_oa_location') or {}
            pdf_u = best.get('url_for_pdf') or ''
            if pdf_u:
                meta['pdf_url'] = str(pdf_u)
                meta['oa_status'] = 'unpaywall_oa'

    return meta if meta['title'] else None


def resolve_identifier(raw_id: str, hint: dict | None = None) -> dict:
    """Resolve any identifier (DOI, arXiv ID, or candidate dict) into rich bibliographic metadata."""
    hint = dict(hint or {})
    kind, clean = normalize_identifier(raw_id or hint.get('doi') or hint.get('arxiv_id') or hint.get('url') or '')
    resolved = None
    if kind == 'arxiv':
        resolved = resolve_arxiv_metadata(clean)
    elif kind == 'doi':
        resolved = resolve_doi_metadata(clean)

    if not resolved:
        resolved = {
            'title': hint.get('title') or clean or '未命名文献',
            'authors': hint.get('authors') or [],
            'year': str(hint.get('year') or ''),
            'doi': clean if kind == 'doi' else (hint.get('doi') or ''),
            'arxiv_id': clean if kind == 'arxiv' else (hint.get('arxiv_id') or ''),
            'publication': hint.get('publication') or '',
            'abstract': hint.get('abstract') or '',
            'url': hint.get('url') or (clean if kind == 'url' else ''),
            'pdf_url': hint.get('pdf_url') or '',
            'oa_status': 'open_access' if hint.get('pdf_url') else 'metadata_only',
            'item_type': hint.get('item_type') or 'journalArticle',
        }
    else:
        for k in ('title', 'abstract', 'pdf_url', 'publication'):
            if not resolved.get(k) and hint.get(k):
                resolved[k] = hint[k]
    return resolved


def download_pdf_bytes(pdf_url: str, timeout: float = 25.0) -> bytes | None:
    """Download a PDF binary from an Open Access or arXiv URL and validate %PDF- header."""
    if not pdf_url or not pdf_url.startswith(('http://', 'https://')):
        return None
    req = urllib.request.Request(
        pdf_url,
        headers={
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 SearchTools/3.0',
            'Accept': 'application/pdf,*/*',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(60 * 1024 * 1024)
            if data.startswith(b'%PDF-'):
                return data
    except Exception:
        return None
    return None


def _to_zotero_creators(authors: list[str] | str) -> list[dict]:
    if isinstance(authors, str):
        authors = [a.strip() for a in re.split(r'[,;，；]', authors) if a.strip()]
    out = []
    for name in (authors or []):
        name = str(name).strip()
        if not name:
            continue
        if re.search(r'[\u4e00-\u9fff]', name) or ' ' not in name:
            out.append({'creatorType': 'author', 'name': name})
        else:
            parts = name.rsplit(' ', 1)
            out.append({'creatorType': 'author', 'firstName': parts[0], 'lastName': parts[1]})
    return out


def execute_zotero_import_for_candidates(candidates: list[dict], target_collection_path: str, fetch_pdf: bool = True) -> dict:
    """Import confirmed candidates (max 20) into Zotero under target_collection_path and attach OA PDFs."""
    if len(candidates) > MAX_ACQUIRE_BATCH:
        candidates = candidates[:MAX_ACQUIRE_BATCH]

    lib = Library()
    col_key = lib.ensure_collection_path(target_collection_path)

    imported = []
    deduped = []
    manual_needed = []

    for c in candidates:
        meta = resolve_identifier(c.get('identifier') or c.get('doi') or c.get('arxiv_id') or '', hint=c)
        existing = lib.find_existing_item(doi=meta.get('doi', ''), arxiv_id=meta.get('arxiv_id', ''), title=meta.get('title', ''))
        if existing:
            item_key = existing['key']
            has_pdf = bool(existing.get('has_pdf'))
            deduped.append({'item_key': item_key, 'title': existing.get('title'), 'has_pdf': has_pdf})
        else:
            zot_item = {
                'itemType': meta.get('item_type') or 'journalArticle',
                'title': meta.get('title') or '未命名文献',
                'creators': _to_zotero_creators(meta.get('authors') or []),
                'date': str(meta.get('year') or ''),
                'DOI': meta.get('doi') or '',
                'url': meta.get('url') or '',
                'abstractNote': meta.get('abstract') or '',
                'tags': [{'tag': 'AI补充文献'}, {'tag': 'DeepTutor'}],
            }
            if zot_item['itemType'] == 'journalArticle':
                zot_item['publicationTitle'] = meta.get('publication') or ''
                zot_item['volume'] = meta.get('volume') or ''
                zot_item['issue'] = meta.get('issue') or ''
                zot_item['pages'] = meta.get('pages') or ''
            elif zot_item['itemType'] == 'preprint' and meta.get('arxiv_id'):
                zot_item['repository'] = 'arXiv'
                zot_item['archiveID'] = f"arXiv:{meta['arxiv_id']}"

            item_key = lib.create_bibliographic_item(zot_item, collection_keys=[col_key])
            has_pdf = False

        pdf_attached = has_pdf
        if fetch_pdf and not has_pdf and meta.get('pdf_url'):
            pdf_bytes = download_pdf_bytes(meta['pdf_url'])
            if pdf_bytes:
                safe_name = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', (meta.get('title') or item_key)[:60]) + '.pdf'
                with tempfile.TemporaryDirectory() as tmpd:
                    p = Path(tmpd) / safe_name
                    p.write_bytes(pdf_bytes)
                    try:
                        lib.attach_pdf_file(item_key, p, title=safe_name, mode='imported')
                        pdf_attached = True
                    except Exception:
                        pdf_attached = False

        entry_summary = {
            'item_key': item_key,
            'title': meta.get('title'),
            'doi': meta.get('doi'),
            'arxiv_id': meta.get('arxiv_id'),
            'pdf_attached': pdf_attached,
            'pdf_url': meta.get('pdf_url'),
            'collection_path': target_collection_path,
            'collection_key': col_key,
        }
        imported.append(entry_summary)
        if not pdf_attached:
            manual_needed.append({
                'item_key': item_key,
                'title': meta.get('title'),
                'doi': meta.get('doi'),
                'reason': '未找到开放获取 PDF，请在 Zotero 中右键「查找可用 PDF」或通过浏览器 Zotero Connector 补充全文',
            })

    return {
        'collection_path': target_collection_path,
        'collection_key': col_key,
        'imported': imported,
        'deduped': deduped,
        'manual_pdf_needed': manual_needed,
    }
