from __future__ import annotations
import csv
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get('SEARCHTOOLS_CONFIG', Path.home()/'.config/searchtools/config.json'))
PAPERQA_KINDS = ('ask', 'evidence', 'index', 'coverage', 'verify')


def config():
    return json.loads(CONFIG.read_text())


def catalog_for(cfg):
    from .models import Catalog
    return Catalog(cfg.get('models_file') or CONFIG.parent/'models.json', cfg.get('paperqa_settings'))


class _NoObjects(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError('unexpected object in index file list')


def index_files(index_dir):
    """PaperQA2's files.zip is a zlib-compressed pickle of {file_location: hash|'ERROR'}.
    Only plain dict/str opcodes are accepted, never arbitrary classes."""
    path = Path(index_dir)/'files.zip'
    if not path.exists():
        return {}
    import io
    try:
        data = _NoObjects(io.BytesIO(zlib.decompress(path.read_bytes()))).load()
    except Exception:
        return {}
    return {Path(str(k)).name: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def gpu_busy():
    """Fail closed if GPU occupancy cannot be determined; never kill GPU jobs."""
    try:
        p = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader'], capture_output=True, text=True, timeout=8)
        if p.returncode:
            return True, '暂时无法确认 GPU 状态，任务等待中'
        others = [line for line in p.stdout.splitlines() if line.strip() and 'ollama' not in line.lower()]
        return bool(others), '其他程序正在使用 GPU，本地任务等待中' if others else 'GPU 可用于本地任务'
    except (OSError, subprocess.TimeoutExpired):
        return True, '无法读取 GPU 状态，任务等待中'


class Store:
    def __init__(self, data, cfg=None):
        self.cfg = cfg
        self.data = Path(data).expanduser().resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.data.chmod(0o700)
        self.dbpath = self.data/'workspace.sqlite3'
        self._zotero_actions = []
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY,project TEXT NOT NULL,name TEXT NOT NULL,sha TEXT NOT NULL,size INTEGER,UNIQUE(project,sha));
            CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,project TEXT NOT NULL,title TEXT,body TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT NOT NULL,kind TEXT NOT NULL,prompt TEXT,source TEXT,status TEXT,message TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS evidence_cards(id TEXT PRIMARY KEY,project TEXT NOT NULL,job_id TEXT,paper_id TEXT,zotero_key TEXT,citation TEXT,doi TEXT,title TEXT,page TEXT,quote TEXT,summary TEXT,claim TEXT,score REAL,pinned INTEGER DEFAULT 0,tags TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS candidate_batches(id TEXT PRIMARY KEY,scope_id TEXT,topic TEXT,query TEXT,candidates_json TEXT,status TEXT DEFAULT 'pending_confirmation',created REAL);
            CREATE TABLE IF NOT EXISTS trace_events(id TEXT PRIMARY KEY,scope_id TEXT,tool TEXT,status TEXT,summary TEXT,detail_json TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS zotero_actions(id TEXT PRIMARY KEY,payload TEXT,status TEXT,lease_until REAL,created REAL,error TEXT);
            CREATE TABLE IF NOT EXISTS kv_meta(key TEXT PRIMARY KEY, value TEXT);
            ''')
            if 'result_json' not in {row[1] for row in db.execute('PRAGMA table_info(zotero_actions)')}:
                db.execute("ALTER TABLE zotero_actions ADD COLUMN result_json TEXT DEFAULT '{}'")
            # Idempotent column migrations for databases created by v0.1 - v2.
            for table,cols in {'papers':{'zotero_item':'TEXT','attachment_key':'TEXT','citekey':'TEXT','authors':'TEXT','year':'TEXT','publication':'TEXT','title':'TEXT','doi':'TEXT','origin':"TEXT DEFAULT 'upload'",'added':'REAL'},
                               'jobs':{'options':'TEXT'},'projects':{'zotero_collection':'TEXT','item_keys_json':'TEXT'},
                               'evidence_cards':{'summary':'TEXT','claim':'TEXT','pinned':'INTEGER DEFAULT 0','tags':'TEXT','attachment_key':'TEXT','citekey':'TEXT','authors':'TEXT','year':'TEXT','zotero_link':'TEXT','used_in_json':"TEXT DEFAULT '[]'",'model':'TEXT','page_index':'INTEGER','page_label':'TEXT','rects_json':'TEXT','locate_status':'TEXT','provenance_json':'TEXT'}}.items():
                have={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                for col,kind in cols.items():
                    if col not in have:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {kind}')
            # Only clean up if explicitly requested; do not fail running jobs on every Store() instantiation


    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def rows(self, sql, params=()):
        with self.db() as db:
            return [dict(r) for r in db.execute(sql, params)]

    def project(self, pid):
        rows = self.rows('SELECT * FROM projects WHERE id=?', (pid,))
        if not rows:
            raise ValueError('项目不存在')
        return rows[0]

    def set_active_project(self, pid: str):
        if pid:
            try:
                self.project(pid)
            except Exception:
                return False
        with self.db() as db:
            db.execute('INSERT INTO kv_meta(key, value) VALUES("active_project", ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (pid,))
        return True

    def get_active_project(self) -> str:
        rows = self.rows('SELECT value FROM kv_meta WHERE key="active_project"')
        if rows and rows[0]['value']:
            try:
                self.project(rows[0]['value'])
                return rows[0]['value']
            except Exception:
                pass
        projs = self.rows('SELECT id FROM projects ORDER BY created DESC')
        return projs[0]['id'] if projs else ''

    def folder(self, pid):
        self.project(pid)
        return self.data/'projects'/pid

    def create_project(self, name):
        name = str(name).strip()[:100]
        if not name:
            raise ValueError('请输入项目名称')
        pid = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO projects(id,name,created) VALUES(?,?,?)', (pid,name,time.time()))
        for sub in ('papers','results','index','jobs'):
            (self.folder(pid)/sub).mkdir(parents=True, exist_ok=True)
        return pid

    def import_paper(self, pid, name, body, zotero_item=None, title=None, doi=None, origin='upload',
                     attachment_key=None, citekey=None, authors=None, year=None, publication=None):
        folder = self.folder(pid)/'papers'
        name = Path(name).name
        if Path(name).suffix.lower() not in ('.pdf','.txt','.md') or not body:
            raise ValueError('支持非空 PDF、TXT、Markdown 文件')
        if name.lower().endswith('.pdf') and not body.startswith(b'%PDF-'):
            raise ValueError('文件不是有效的 PDF')
        sha = hashlib.sha256(body).hexdigest()
        authors_str = json.dumps(authors, ensure_ascii=False) if isinstance(authors, list) else (str(authors or ''))
        found = self.rows('SELECT * FROM papers WHERE project=? AND sha=?',(pid,sha))
        if found:
            with self.db() as db:
                db.execute('''UPDATE papers SET
                    zotero_item=COALESCE(?,zotero_item),
                    attachment_key=COALESCE(?,attachment_key),
                    citekey=COALESCE(?,citekey),
                    authors=COALESCE(NULLIF(?,''),authors),
                    year=COALESCE(NULLIF(?,''),year),
                    publication=COALESCE(NULLIF(?,''),publication),
                    title=COALESCE(?,title),
                    doi=COALESCE(?,doi)
                    WHERE id=?''',
                    (zotero_item, attachment_key, citekey, authors_str, str(year or ''), str(publication or ''), title, doi, found[0]['id']))
            return found[0]['id']
        paperid = uuid.uuid4().hex
        safe = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', name)[:150]
        filename = sha[:12]+'_'+safe
        target = folder/filename
        target.write_bytes(body)
        with self.db() as db:
            db.execute('''INSERT INTO papers(id,project,name,sha,size,zotero_item,attachment_key,citekey,authors,year,publication,title,doi,origin,added)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (paperid,pid,filename,sha,len(body),zotero_item,attachment_key,citekey,authors_str,str(year or ''),str(publication or ''),title,doi,origin,time.time()))
        return paperid

    def log_trace(self, scope_id: str, tool: str, status: str, summary: str, detail: dict | None = None) -> str:
        tid = 'tr-' + uuid.uuid4().hex[:10]
        with self.db() as db:
            db.execute('INSERT INTO trace_events(id,scope_id,tool,status,summary,detail_json,created) VALUES(?,?,?,?,?,?,?)',
                       (tid, str(scope_id or ''), str(tool), str(status), str(summary)[:500],
                        json.dumps(detail or {}, ensure_ascii=False), time.time()))
        return tid

    def list_traces(self, scope_id: str = '', limit: int = 60) -> list[dict]:
        if scope_id:
            rows = self.rows('SELECT * FROM trace_events WHERE scope_id=? OR scope_id="" ORDER BY created DESC LIMIT ?', (scope_id, int(limit)))
        else:
            rows = self.rows('SELECT * FROM trace_events ORDER BY created DESC LIMIT ?', (int(limit),))
        for r in rows:
            try:
                r['detail'] = json.loads(r.get('detail_json') or '{}')
            except Exception:
                r['detail'] = {}
        return rows

    def create_or_update_scope(self, name: str = '', item_keys: list[str] | None = None,
                               collection: str = '', zotero_query: str = '', scope_id: str = '') -> dict:
        """Create or update a Research Scope backed by a set of Zotero item_keys with incremental indexing."""
        from .zotero import Library
        lib = Library()
        resolved_items: dict[str, dict] = {}
        if item_keys:
            for k in item_keys:
                k = str(k).strip()
                if not k:
                    continue
                try:
                    s = lib.summary(lib.raw_item(k))
                    resolved_items[s['key']] = s
                except Exception:
                    pass
        if collection:
            try:
                for s in lib.items(collection=collection, limit=200):
                    resolved_items[s['key']] = s
            except Exception:
                pass
        if zotero_query:
            try:
                for s in lib.items(q=zotero_query, mode='everything', limit=100):
                    resolved_items[s['key']] = s
            except Exception:
                pass

        projects = self.rows('SELECT * FROM projects ORDER BY created DESC')
        pid = ''
        if scope_id:
            for p in projects:
                if p['id'] == scope_id or p['name'].casefold() == scope_id.strip().casefold():
                    pid = p['id']
                    break
        if not pid and name:
            for p in projects:
                if p['name'].casefold() == name.strip().casefold():
                    pid = p['id']
                    break
        if not pid:
            scope_title = (name or (f"Zotero: {collection}" if collection else (f"检索: {zotero_query}" if zotero_query else "学术实证课题"))).strip()[:100]
            pid = self.create_project(scope_title)

        # Merge item_keys into project's item_keys_json
        proj = self.project(pid)
        existing_keys = []
        try:
            existing_keys = json.loads(proj.get('item_keys_json') or '[]')
        except Exception:
            existing_keys = []
        merged_keys = list(dict.fromkeys(existing_keys + list(resolved_items.keys())))
        with self.db() as db:
            db.execute('UPDATE projects SET zotero_collection=COALESCE(NULLIF(?,""),zotero_collection), item_keys_json=? WHERE id=?',
                       (collection, json.dumps(merged_keys, ensure_ascii=False), pid))

        # Resolve Better BibTeX citekeys in batch
        citekeys_map = lib.get_citekeys(list(resolved_items.keys())) if resolved_items else {}

        papers_status = []
        imported_new = 0
        missing_pdf = []
        for k, meta in resolved_items.items():
            ck = citekeys_map.get(k) or meta.get('citekey') or f"zot_{k.lower()}"
            pdf_path, att_key = lib.attachment_info(k)
            if pdf_path and pdf_path.is_file():
                try:
                    body = pdf_path.read_bytes()
                    paper_id = self.import_paper(
                        pid,
                        pdf_path.name,
                        body,
                        zotero_item=k,
                        title=meta.get('title'),
                        doi=meta.get('doi'),
                        origin='zotero',
                        attachment_key=att_key,
                        citekey=ck,
                        authors=meta.get('creators') or [],
                        year=meta.get('year') or '',
                        publication=meta.get('publication') or '',
                    )
                    imported_new += 1
                    papers_status.append({
                        'item_key': k,
                        'paper_id': paper_id,
                        'citekey': ck,
                        'title': meta.get('title'),
                        'year': meta.get('year'),
                        'has_pdf': True,
                        'attachment_key': att_key,
                    })
                except Exception as e:
                    missing_pdf.append({'item_key': k, 'title': meta.get('title'), 'reason': str(e)})
            else:
                missing_pdf.append({'item_key': k, 'title': meta.get('title'), 'reason': '无本地 PDF 附件'})
                papers_status.append({
                    'item_key': k,
                    'paper_id': None,
                    'citekey': ck,
                    'title': meta.get('title'),
                    'year': meta.get('year'),
                    'has_pdf': False,
                    'attachment_key': '',
                })

        status_info = self.get_scope_status(pid)
        return {
            'scope_id': pid,
            'name': self.project(pid)['name'],
            'total_items': len(merged_keys),
            'with_pdf_count': status_info['total_papers'],
            'indexed_count': status_info['indexed'],
            'pending_index_count': status_info['pending'],
            'missing_pdf': missing_pdf,
            'papers': status_info['papers'],
        }

    def get_scope_status(self, pid: str) -> dict:
        proj = self.project(pid)
        p_folder = self.folder(pid)
        cfg = config()
        cat_data = catalog_for(cfg).load()
        from .models import index_name
        idx_sub = p_folder / 'index' / index_name(cat_data)
        fallback_sub = p_folder / 'index' / ('project-' + pid)
        target_idx = idx_sub if idx_sub.exists() else fallback_sub
        fmap = index_files(target_idx)
        papers = self.rows('SELECT * FROM papers WHERE project=? ORDER BY added DESC', (pid,))
        for p in papers:
            st = fmap.get(p['name'])
            p['index_status'] = 'indexed' if (st and st != 'ERROR') else ('error' if st == 'ERROR' else 'pending')
            try:
                p['authors_list'] = json.loads(p.get('authors') or '[]') if str(p.get('authors') or '').startswith('[') else [p.get('authors')]
            except Exception:
                p['authors_list'] = []
        indexed_cnt = sum(1 for p in papers if p['index_status'] == 'indexed')
        failed_cnt = sum(1 for p in papers if p['index_status'] == 'error')
        recent_jobs = self.rows('SELECT id,kind,prompt,status,message,created FROM jobs WHERE project=? ORDER BY created DESC LIMIT 8', (pid,))
        return {
            'scope_id': pid,
            'name': proj['name'],
            'zotero_collection': proj.get('zotero_collection') or '',
            'total_papers': len(papers),
            'indexed': indexed_cnt,
            'failed': failed_cnt,
            'pending': len(papers) - indexed_cnt - failed_cnt,
            'ready': (indexed_cnt == len(papers) and len(papers) > 0),
            'papers': papers,
            'recent_jobs': recent_jobs,
        }

    def save_candidate_batch(self, scope_id: str, topic: str, query: str, candidates: list[dict]) -> dict:
        """Stage up to 20 external literature candidates for user confirmation in the Workbench UI."""
        from .zotero import Library
        lib = Library()
        capped = list(candidates or [])[:20]
        enriched = []
        for idx, c in enumerate(capped):
            existing = None
            try:
                existing = lib.find_existing_item(
                    doi=c.get('doi') or '',
                    arxiv_id=c.get('arxiv_id') or '',
                    title=c.get('title') or ''
                )
            except Exception:
                existing = None
            item = {
                'index': idx,
                'identifier': c.get('identifier') or c.get('doi') or c.get('arxiv_id') or c.get('url') or '',
                'title': c.get('title') or '未命名候选文献',
                'authors': c.get('authors') or [],
                'year': str(c.get('year') or ''),
                'doi': c.get('doi') or '',
                'arxiv_id': c.get('arxiv_id') or '',
                'publication': c.get('publication') or c.get('venue') or '',
                'pdf_url': c.get('pdf_url') or '',
                'abstract': str(c.get('abstract') or '')[:600],
                'in_zotero': bool(existing),
                'existing_key': existing['key'] if existing else '',
                'has_oa_pdf': bool(c.get('pdf_url') or c.get('arxiv_id') or 'arxiv' in str(c.get('doi') or '').lower()),
                'selected': not bool(existing),
            }
            enriched.append(item)

        bid = 'cb-' + uuid.uuid4().hex[:10]
        with self.db() as db:
            db.execute('INSERT INTO candidate_batches(id,scope_id,topic,query,candidates_json,status,created) VALUES(?,?,?,?,?,?,?)',
                       (bid, str(scope_id or ''), str(topic or '补充文献'), str(query or ''),
                        json.dumps(enriched, ensure_ascii=False), 'pending_confirmation', time.time()))
        return {
            'batch_id': bid,
            'scope_id': scope_id,
            'topic': topic,
            'query': query,
            'total_staged': len(enriched),
            'max_limit': 20,
            'status': 'pending_confirmation',
            'candidates': enriched,
        }

    def list_candidate_batches(self, scope_id: str = '') -> list[dict]:
        if scope_id:
            rows = self.rows('SELECT * FROM candidate_batches WHERE scope_id=? ORDER BY created DESC LIMIT 20', (scope_id,))
        else:
            rows = self.rows('SELECT * FROM candidate_batches ORDER BY created DESC LIMIT 20')
        for r in rows:
            try:
                r['candidates'] = json.loads(r.get('candidates_json') or '[]')
            except Exception:
                r['candidates'] = []
        return rows

    def remove_paper(self, pid, paperid):
        """Remove a paper from the project; existing task snapshots keep their own copies."""
        rows = self.rows('SELECT * FROM papers WHERE id=? AND project=?',(paperid,pid))
        if not rows:
            raise ValueError('文献不存在')
        (self.folder(pid)/'papers'/rows[0]['name']).unlink(missing_ok=True)
        with self.db() as db:
            db.execute('DELETE FROM papers WHERE id=?',(paperid,))

    def remove_job(self, pid, jid):
        """Delete a completed, failed or cancelled job record and its workspace files."""
        rows = self.rows('SELECT * FROM jobs WHERE id=? AND project=?', (jid, pid))
        if not rows:
            raise ValueError('任务不存在')
        job = rows[0]
        if job['status'] in ('queued', 'waiting', 'running'):
            raise ValueError('任务正在排队或执行中，请先取消任务再删除')
        work = self.folder(pid)/'jobs'/jid
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        (self.folder(pid)/'results'/f'{jid}.md').unlink(missing_ok=True)
        with self.db() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (jid,))
            db.execute('DELETE FROM evidence_cards WHERE project=? AND job_id=? AND pinned=0', (pid, jid))

    def save_evidence_card(self, pid, card):
        self.project(pid)
        cid = str(card.get('id') or card.get('card_id') or ('ev_' + uuid.uuid4().hex[:10]))
        now = time.time()
        used_in = card.get('used_in')
        used_in_str = json.dumps(used_in, ensure_ascii=False) if isinstance(used_in, list) else (card.get('used_in_json') or '[]')
        authors = card.get('authors')
        authors_str = json.dumps(authors, ensure_ascii=False) if isinstance(authors, list) else (str(authors or ''))
        with self.db() as db:
            found = [dict(r) for r in db.execute('SELECT id FROM evidence_cards WHERE project=? AND id=?', (pid, cid))]
            if found:
                db.execute('''
                UPDATE evidence_cards SET
                    job_id=COALESCE(?,job_id),
                    paper_id=COALESCE(?,paper_id),
                    zotero_key=COALESCE(?,zotero_key),
                    attachment_key=COALESCE(?,attachment_key),
                    citekey=COALESCE(?,citekey),
                    authors=COALESCE(NULLIF(?,''),authors),
                    year=COALESCE(NULLIF(?,''),year),
                    zotero_link=COALESCE(?,zotero_link),
                    citation=COALESCE(?,citation),
                    doi=COALESCE(?,doi),
                    title=COALESCE(?,title),
                    page=COALESCE(?,page),
                    quote=COALESCE(?,quote),
                    summary=COALESCE(?,summary),
                    claim=COALESCE(?,claim),
                    score=COALESCE(?,score),
                    pinned=COALESCE(?,pinned),
                    tags=COALESCE(?,tags),
                    model=COALESCE(?,model),
                    page_index=COALESCE(?,page_index),
                    page_label=COALESCE(?,page_label),
                    rects_json=COALESCE(?,rects_json),
                    locate_status=COALESCE(?,locate_status),
                    used_in_json=COALESCE(NULLIF(?,'[]'),used_in_json)
                WHERE project=? AND id=?
                ''', (card.get('job_id'), card.get('paper_id'), card.get('zotero_key'), card.get('attachment_key'),
                      card.get('citekey'), authors_str, str(card.get('year') or ''), card.get('zotero_link'),
                      card.get('citation'), card.get('doi'), card.get('title'), str(card.get('page') or ''),
                      card.get('quote'), card.get('summary'), card.get('claim'), card.get('score'),
                      card.get('pinned'), card.get('tags'), card.get('model'),
                      card.get('page_index'), str(card.get('page_label') or '') if card.get('page_label') else None,
                      json.dumps(card['rects']) if isinstance(card.get('rects'), list) else card.get('rects_json'),
                      card.get('locate_status'), used_in_str, pid, cid))
            else:
                rects_json_val = json.dumps(card['rects']) if isinstance(card.get('rects'), list) else (card.get('rects_json') or '[]')
                db.execute('''
                INSERT INTO evidence_cards(id,project,job_id,paper_id,zotero_key,attachment_key,citekey,authors,year,zotero_link,citation,doi,title,page,quote,summary,claim,score,pinned,tags,model,page_index,page_label,rects_json,locate_status,used_in_json,created)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (cid, pid, card.get('job_id'), card.get('paper_id'), card.get('zotero_key'),
                      card.get('attachment_key'), card.get('citekey'), authors_str, str(card.get('year') or ''),
                      card.get('zotero_link'), card.get('citation'), card.get('doi'), card.get('title'),
                      str(card.get('page') or ''), card.get('quote'), card.get('summary'), card.get('claim'),
                      float(card.get('score') or 0), int(card.get('pinned') or 0), str(card.get('tags') or ''),
                      str(card.get('model') or ''), card.get('page_index'), str(card.get('page_label') or ''),
                      rects_json_val, card.get('locate_status') or 'unlocated', used_in_str, now))
        return cid

    def _enrich_card_v2(self, row: dict) -> dict:
        c = dict(row)
        c['card_id'] = c['id']
        try:
            authors_list = json.loads(c.get('authors') or '[]') if str(c.get('authors') or '').startswith('[') else ([c['authors']] if c.get('authors') else [])
        except Exception:
            authors_list = []
        try:
            used_in = json.loads(c.get('used_in_json') or '[]')
        except Exception:
            used_in = []

        raw_title = str(c.get('title') or '').strip()
        raw_cit = str(c.get('citation') or '').strip()
        zot_key = str(c.get('zotero_key') or '').strip()
        att_key = str(c.get('attachment_key') or '').strip()
        year_val = str(c.get('year') or '').strip()
        doi_val = str(c.get('doi') or '').strip()
        citekey_val = str(c.get('citekey') or '').strip()

        # Legacy card enrichment: resolve metadata from the linked paper row (no hardcoded values)
        if not raw_title or re.match(r'^citation\d*', raw_title, re.I) or not zot_key:
            paper = None
            try:
                if c.get('paper_id'):
                    rows_p = self.rows('SELECT * FROM papers WHERE id=?', (c['paper_id'],))
                    paper = rows_p[0] if rows_p else None
                if paper is None and c.get('project'):
                    rows_p = self.rows('SELECT * FROM papers WHERE project=?', (c['project'],))
                    if len(rows_p) == 1:
                        paper = rows_p[0]
            except Exception:
                paper = None
            if paper and paper.get('zotero_item'):
                zot_key = zot_key or str(paper['zotero_item'])
                att_key = att_key or str(paper.get('attachment_key') or '')
                if not raw_title or re.match(r'^citation\d*', raw_title, re.I):
                    raw_title = str(paper.get('title') or raw_title)
                year_val = year_val or str(paper.get('year') or '')
                doi_val = doi_val or str(paper.get('doi') or '')
                c['zotero_key'], c['title'], c['year'] = zot_key, raw_title, year_val

        page_val = str(c.get('page') or '').strip()
        if not page_val and raw_cit:
            m_pg = re.search(r'pages?\s*(\d+(?:-\d+)?)', raw_cit, re.I)
            if m_pg:
                page_val = m_pg.group(1)
                c['page'] = page_val

        page_first = page_val.split('-')[0].strip()
        computed_link = (
            f"zotero://open-pdf/library/items/{att_key}?page={page_first}" if (att_key and page_first)
            else (f"zotero://open-pdf/library/items/{att_key}" if att_key
                  else (f"zotero://select/library/items/{zot_key}" if zot_key else ""))
        )
        # Prefer deep open-pdf links with page over shallow select links
        if computed_link and ('open-pdf' in computed_link or not c.get('zotero_link')):
            zot_link = computed_link
        else:
            zot_link = c.get('zotero_link') or computed_link
        c['zotero_link'] = zot_link
        c['used_in'] = used_in
        c['source'] = {
            'item_key': zot_key,
            'attachment_key': att_key,
            'citekey': citekey_val or f"zot_{(zot_key or c['id'][:6]).lower()}",
            'title': raw_title or raw_cit or '',
            'citation': raw_cit or raw_title or '',
            'authors': authors_list,
            'year': year_val,
            'doi': doi_val,
            'pages': page_val,
            'zotero_link': zot_link,
            'zotero_evidence_link': f"zotero://evidence/open?card={c['id']}",
        }
        rects_raw = c.get('rects_json')
        try:
            rects_list = json.loads(rects_raw) if rects_raw else []
        except Exception:
            rects_list = []
        c['locate'] = {
            'status': c.get('locate_status') or 'unlocated',
            'page_index': c.get('page_index'),
            'page_label': c.get('page_label') or page_val,
            'rects': rects_list
        }
        stored = json.loads(c.get('provenance_json') or '{}')
        location = stored.get('location') or {}
        c['locate'] = dict(location, card_id=c.get('id'), attachment_key=c.get('attachment_key'), zotero_key=zot_key) if location.get('version') == 3 else dict(c['locate'], status='needs_revalidation', rects=[])
        c['provenance'] = {
            **stored,
            'job_id': c.get('job_id') or '',
            'scope_id': c.get('project') or '',
            'model': c.get('model') or '',
            'created': c.get('created') or 0,
        }
        return c

    def list_evidence_cards(self, pid=None, q=None, tag=None, min_score=None, pinned_only=False):
        sql = 'SELECT * FROM evidence_cards WHERE 1=1'
        params = []
        if pid:
            self.project(pid)
            sql += ' AND project=?'
            params.append(pid)
        if min_score is not None:
            sql += ' AND score >= ?'
            params.append(float(min_score))
        if pinned_only:
            sql += ' AND pinned = 1'
        if tag:
            sql += ' AND tags LIKE ?'
            params.append(f'%{tag}%')
        if q:
            stop_words = {'how', 'what', 'why', 'when', 'where', 'which', 'who', 'whom', 'this', 'that',
                          'these', 'those', 'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
                          'have', 'has', 'had', 'having', 'do', 'does', 'did', 'doing', 'a', 'an', 'the',
                          'and', 'but', 'if', 'or', 'because', 'as', 'until', 'while', 'of', 'at', 'by',
                          'for', 'with', 'about', 'against', 'between', 'into', 'through', 'during',
                          'before', 'after', 'to', 'from', 'in', 'out', 'on', 'off', 'over', 'under',
                          'can', 'will', 'should', 'based', 'using', 'via', '基于', '的', '在', '与', '和', '及', '中', '对', '等'}
            raw_tokens = [t.strip() for t in re.split(r'[\s,，、;；|/]+', str(q).strip()) if t.strip()]
            filtered_tokens = [t for t in raw_tokens if t.lower() not in stop_words and len(t) > 1]
            tokens = filtered_tokens or raw_tokens
            token_clauses = []
            for tok in tokens[:8]:
                token_clauses.append('(title LIKE ? OR summary LIKE ? OR quote LIKE ? OR claim LIKE ? OR citation LIKE ? OR citekey LIKE ?)')
                term = f'%{tok}%'
                params.extend([term, term, term, term, term, term])
            # If multiple meaningful terms, require all of them (AND) to prevent loose false cache hits
            join_op = ' AND ' if len(tokens) <= 4 else ' OR '
            sql += ' AND (' + join_op.join(token_clauses) + ')'
        sql += ' ORDER BY pinned DESC, score DESC, created DESC'
        return [self._enrich_card_v2(r) for r in self.rows(sql, params)]

    def get_evidence_card(self, cid: str) -> dict | None:
        """Fetch a single enriched evidence card by ID."""
        rows = self.rows('SELECT * FROM evidence_cards WHERE id=?', (cid,))
        return self._enrich_card_v2(rows[0]) if rows else None

    def capture_reader_selection(self, data):
        from .reader_capture import validate_position, verify_selection_location
        from .locate import resolve_attachment_pdf, locate_quote_in_pdf
        from .zotero import Library, check_key
        quote = str(data.get('quote') or '').strip()
        if not quote:
            raise ValueError('划选内容为空')
        att_key = check_key(data.get('attachment_key'))
        zot_key = check_key(data.get('zotero_key'))
        selection = data.get('reader_selection') or {}
        position = selection.get('position')
        validate_position(position)
        lib = Library()
        attachment = lib.raw_item(att_key).get('data', {})
        if attachment.get('itemType') != 'attachment' or attachment.get('parentItem') != zot_key:
            raise ValueError('附件与文献归属不匹配')
        meta = lib.summary(lib.raw_item(zot_key))
        path = resolve_attachment_pdf(att_key, zot_key)
        if path is None:
            raise ValueError('无法读取划选所对应的 PDF')
        location = locate_quote_in_pdf(path, quote)
        verify_selection_location(position, location)
        pid = str(data.get('project') or '').strip()
        if not pid:
            # Do not silently file reader captures into the last unrelated research scope.
            projects = self.rows('SELECT id FROM projects WHERE name=? ORDER BY created LIMIT 1', ('Zotero 阅读器摘录',))
            pid = projects[0]['id'] if projects else self.create_project('Zotero 阅读器摘录')
        self.project(pid)
        request_id = str(data.get('request_id') or uuid.uuid4().hex)
        cid = 'ev_reader_' + hashlib.sha256((pid + ':' + request_id).encode()).hexdigest()[:16]
        existing = self.get_evidence_card(cid)
        if existing:
            if existing.get('quote') != quote or existing.get('attachment_key') != att_key:
                raise ValueError('重复请求编号对应不同的选文')
            return existing
        claim = str(data.get('claim') or '').strip()
        card = dict(id=cid, job_id='zotero_reader_selection', zotero_key=zot_key,
                    attachment_key=att_key, title=meta.get('title'), authors=meta.get('creators'),
                    citekey=meta.get('citekey'), year=meta.get('year'), doi=meta.get('doi'),
                    quote=quote, claim=claim or '待整理摘录：尚未填写论点', summary=quote,
                    score=0, pinned=1, tags='zotero_reader,manual',
                    page=','.join(dict.fromkeys(span['page_label'] for span in location['spans'])),
                    page_index=location['page_index'], page_label=location['page_label'],
                    rects=location['rects'], locate_status='exact')
        self.save_evidence_card(pid, card)
        provenance = {'version': 3, 'origin': 'reader_selection', 'reader_selection': selection,
                      'request_id': request_id, 'claim_review': 'human_provided' if claim else 'needs_claim',
                      'location': location}
        with self.db() as db:
            db.execute('UPDATE evidence_cards SET provenance_json=? WHERE id=?',
                       (json.dumps(provenance, ensure_ascii=False), cid))
        self.log_trace(pid, 'reader_capture', 'done', '阅读器原文与选区已回验',
                       {'card_id': cid, 'attachment_key': att_key, 'request_id': request_id})
        return self.get_evidence_card(cid)

    def locate_card(self, cid: str, force: bool = False) -> dict:
        """Run PDF precise quote location for an evidence card and persist rects & page_index."""
        card = self.get_evidence_card(cid)
        if not card:
            return {'status': 'not_found', 'error': f'Card {cid} not found'}
        from .locate import resolve_attachment_pdf, locate_quote_in_pdf
        att_key = card.get('attachment_key') or card.get('source', {}).get('attachment_key') or ''
        zot_key = card.get('zotero_key') or card.get('source', {}).get('item_key') or ''
        quote = card.get('quote') or ''
        page_hint = card.get('page') or card.get('source', {}).get('pages') or ''

        pdf_path = resolve_attachment_pdf(att_key, zot_key)
        if not pdf_path and not att_key and not zot_key and card.get('paper_id'):
            rows = self.rows('SELECT * FROM papers WHERE id=? AND project=?', (card['paper_id'], card['project']))
            if rows:
                path = self.folder(card['project'])/'papers'/rows[0]['name']
                if path.suffix.lower() == '.pdf' and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == rows[0]['sha']:
                    pdf_path = path

        if not pdf_path:
            res = {'status': 'pdf_missing', 'page_index': None, 'page_label': None, 'rects': [], 'error': 'Attachment PDF not found in Zotero storage'}
        else:
            res = locate_quote_in_pdf(pdf_path, quote, page_hint)

        provenance = json.loads(card.get('provenance_json') or '{}')
        if provenance.get('reader_selection') and res.get('status') == 'exact':
            from .reader_capture import verify_selection_location
            try:
                verify_selection_location(provenance['reader_selection']['position'], res)
            except ValueError as exc:
                res = {'version': 3, 'status': 'selection_mismatch', 'rects': [], 'spans': [], 'error': str(exc)}
        provenance['location'] = res
        with self.db() as db:
            db.execute('''
            UPDATE evidence_cards SET
                page_index=?,
                page_label=?,
                rects_json=?,
                locate_status=?, provenance_json=?
            WHERE id=?
            ''', (res.get('page_index', 0), str(res.get('page_label', '1')), json.dumps(res.get('rects', [])), res.get('status', 'failed'), json.dumps(provenance, ensure_ascii=False), cid))

        if res.get('status') == 'exact':
            labels = list(dict.fromkeys(span['page_label'] for span in res.get('spans', [])))
            physical_page = res['page_index'] + 1
            with self.db() as db:
                db.execute('UPDATE evidence_cards SET page=?,zotero_link=? WHERE id=?',
                           (','.join(labels), f'zotero://open-pdf/library/items/{att_key}?page={physical_page}', cid))
        res['card_id'] = cid
        res['attachment_key'] = att_key
        res['zotero_key'] = zot_key
        res['quote'] = quote
        res['summary'] = card.get('summary') or ''
        res['score'] = card.get('score') or 0
        return res

    def enqueue_zotero_action(self, action: dict):
        import uuid
        aid = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO zotero_actions(id,payload,status,lease_until,created,error) VALUES(?,?,?,?,?,?)',
                       (aid, json.dumps(action), 'pending', 0, time.time(), ''))
        return aid

    def zotero_action_status(self, aid):
        rows = self.rows('SELECT id,status,error,result_json FROM zotero_actions WHERE id=?', (aid,))
        if not rows:
            return {'status': 'not_found'}
        row = rows[0]
        row['result'] = json.loads(row.pop('result_json') or '{}')
        return row

    def ack_zotero_action(self, aid, error='', result=None):
        with self.db() as db:
            db.execute("UPDATE zotero_actions SET status=?,error=?,result_json=? WHERE id=? AND status='delivered'",
                       ('failed' if error else 'done', str(error)[:2000], json.dumps(result or {}, ensure_ascii=False), aid))
        return self.zotero_action_status(aid)

    def pop_zotero_actions(self) -> list[dict]:
        self._last_zotero_poll = time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT id,payload FROM zotero_actions WHERE status IN ('pending','delivered') AND lease_until<? AND created>? ORDER BY created LIMIT 10",
                              (time.time(), time.time()-300)).fetchall()
            actions = []
            for aid, payload in rows:
                db.execute("UPDATE zotero_actions SET status='delivered',lease_until=? WHERE id=?", (time.time()+60, aid))
                actions.append(dict(json.loads(payload), action_id=aid))
        return actions

    def record_card_usage(self, card_ids: list[str], doc_ref: str = 'deeptutor:draft', anchor: str = '') -> list[dict]:
        """Record used_in provenance for cards used by the LLM and returned enriched cards."""
        if not card_ids:
            return []
        q_marks = ','.join('?' for _ in card_ids)
        rows = self.rows(f'SELECT * FROM evidence_cards WHERE id IN ({q_marks})', card_ids)
        updated = []
        with self.db() as db:
            for r in rows:
                try:
                    used = json.loads(r.get('used_in_json') or '[]')
                except Exception:
                    used = []
                entry = {'doc': doc_ref, 'anchor': anchor or '正文引用', 'ts': time.time()}
                if not any(u.get('doc') == doc_ref and u.get('anchor') == entry['anchor'] for u in used):
                    used.append(entry)
                db.execute('UPDATE evidence_cards SET used_in_json=? WHERE id=?',
                           (json.dumps(used, ensure_ascii=False), r['id']))
                r['used_in_json'] = json.dumps(used, ensure_ascii=False)
                updated.append(self._enrich_card_v2(r))
        return updated

    def remove_evidence_card(self, pid, cid):
        self.project(pid)
        with self.db() as db:
            db.execute('DELETE FROM evidence_cards WHERE project=? AND id=?', (pid, cid))
        return True

    def toggle_pin_evidence_card(self, pid, cid):
        self.project(pid)
        with self.db() as db:
            db.execute('UPDATE evidence_cards SET pinned = CASE WHEN pinned=1 THEN 0 ELSE 1 END WHERE project=? AND id=?', (pid, cid))
        cards = self.rows('SELECT pinned FROM evidence_cards WHERE project=? AND id=?', (pid, cid))
        return bool(cards[0]['pinned']) if cards else False

    def extract_and_save_evidence_from_result(self, pid, job_id, result_data, claim=None):
        self.project(pid)
        contexts = result_data.get('contexts') or []
        if not contexts:
            return []
        papers = self.rows('SELECT * FROM papers WHERE project=?', (pid,))
        from .zotero import Library, generate_fallback_citekey
        lib = Library()
        saved = []
        def _clean_str(s):
            return re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '', str(s or '')).lower()

        for ctx in contexts:
            if ctx.get('atomic_schema_version') != 3 or not ctx.get('claim') or not ctx.get('quote'):
                continue
            from .atomic import validate_records
            verified, _ = validate_records([ctx], {'text': ctx.get('source_context') or ''})
            if not verified:
                continue
            docname = ctx.get('docname') or ctx.get('name') or ''
            c_doc = _clean_str(docname)
            candidates = []
            core_name = re.sub(r'pages?\d+.*$', '', c_doc)
            for paper in papers:
                identities = [_clean_str(paper.get(field)) for field in ('title', 'citekey')]
                identities.append(_clean_str(Path(paper['name']).stem))
                if any(len(identity) >= 8 and identity in core_name for identity in identities):
                    candidates.append(paper)
            if ctx.get('source_paper_id'):
                candidates = [paper for paper in papers if paper['id'] == ctx['source_paper_id']
                              and paper['sha'] == ctx.get('source_sha256')]
            if len(candidates) != 1:
                continue
            matched = candidates[0]

            zot_key = (matched.get('zotero_item') if matched else None) or ''
            att_key = (matched.get('attachment_key') if matched else None) or ''
            citekey = (matched.get('citekey') if matched else None) or ''
            authors_val = (matched.get('authors') if matched else None) or '[]'
            year_val = (matched.get('year') if matched else None) or ''
            title_val = (matched.get('title') if matched else None) or ctx.get('title') or docname
            doi_val = (matched.get('doi') if matched else None) or ''

            # Enrich from Zotero live if zotero_key is known but attachment_key or citekey is missing
            if zot_key and (not att_key or not citekey):
                try:
                    raw = lib.raw_item(zot_key)
                    meta = lib.summary(raw)
                    _, resolved_att = lib.attachment_info(zot_key)
                    att_key = att_key or resolved_att or meta.get('attachment_key') or ''
                    citekey = citekey or lib.get_citekeys([zot_key]).get(zot_key) or meta.get('citekey') or ''
                    authors_val = json.dumps(meta.get('creators') or [], ensure_ascii=False)
                    year_val = year_val or meta.get('year') or ''
                    title_val = meta.get('title') or title_val
                    doi_val = doi_val or meta.get('doi') or ''
                except Exception:
                    pass

            page_val = str(ctx.get('page') or '')
            if not page_val:
                m_pg = re.search(r'pages?\s*(\d+(?:-\d+)?)', str(ctx.get('name') or ''), re.I)
                if m_pg:
                    page_val = m_pg.group(1)
            page_first = page_val.split('-')[0].strip()
            zot_link = (
                f"zotero://open-pdf/library/items/{att_key}?page={page_first}" if (att_key and page_first)
                else (f"zotero://open-pdf/library/items/{att_key}" if att_key
                      else (f"zotero://select/library/items/{zot_key}" if zot_key else ""))
            )
            score_val = float(ctx.get('score') or 0)
            summary_txt = (ctx.get('summary') or '').strip()
            quote_txt = (ctx.get('text') or ctx.get('quote') or '').strip()

            # Filter out bibliography/references and irrelevant negative chunks
            low_sum = summary_txt.lower()
            if score_val < 5.0 or not summary_txt or not quote_txt:
                continue
            if any(k in low_sum for k in ('bibliography', 'is a list of references')):
                continue

            # Align Claim & Quote:
            # 1. If explicit claim was passed from outside (e.g. verify_claim), use it.
            # 2. If the context carries a model-extracted atomic claim, use it.
            # 3. Otherwise, extract the atomic proposition from the first sentence of summary.
            # 4. Fallback to concise prompt question, avoiding long compound multi-aspect questions.
            card_claim = ''
            if ctx.get('claim'):
                card_claim = str(ctx['claim']).strip()
            elif claim and len(str(claim).strip()) > 0:
                card_claim = str(claim).strip()
            elif ctx.get('claim') and len(str(ctx['claim']).strip()) > 0:
                card_claim = str(ctx['claim']).strip()
            else:
                first_sent = re.split(r'[。！？.!?\n]', summary_txt)[0].strip()
                if len(first_sent) >= 8 and len(first_sent) <= 160:
                    card_claim = first_sent
                else:
                    q_raw = str(result_data.get('question') or '').strip()
                    card_claim = q_raw[:120] if q_raw else '学术实证断言'

            citation = ctx.get('citation') or title_val
            card = {
                'job_id': job_id,
                'paper_id': matched['id'] if matched else None,
                'zotero_key': zot_key,
                'attachment_key': att_key,
                'citekey': citekey or generate_fallback_citekey({'title': title_val, 'year': year_val}),
                'authors': authors_val,
                'year': year_val,
                'zotero_link': zot_link,
                'citation': citation,
                'doi': doi_val,
                'title': title_val,
                'page': page_val,
                'quote': quote_txt,
                'summary': summary_txt,
                'claim': card_claim,
                'score': score_val,
                'pinned': 0,
                'tags': ''
            }
            cid = self.save_evidence_card(pid, card)
            provenance = {'version': 3, 'relation': ctx.get('relation'), 'limitation': ctx.get('limitation'),
                          'source_context': ctx.get('source_context'), 'validation': 'context_verbatim',
                          'claim_review': 'model_extracted_unreviewed',
                          'source_paper_id': matched['id'], 'source_sha256': matched['sha']}
            with self.db() as db:
                db.execute('UPDATE evidence_cards SET provenance_json=? WHERE id=?', (json.dumps(provenance, ensure_ascii=False), cid))
            self.locate_card(cid)
            rows = self.rows('SELECT * FROM evidence_cards WHERE id=?', (cid,))
            if rows:
                saved.append(self._enrich_card_v2(rows[0]))
        return saved

    def format_evidence_cards_citation(self, card_ids, style='gbt7714', auto_writeback=False, doc_ref='deeptutor:draft', anchor=''):
        if not card_ids:
            return ''
        # Preserve caller order while fetching cards
        q_marks = ','.join('?' for _ in card_ids)
        raw_rows = {r['id']: self._enrich_card_v2(r) for r in self.rows(f'SELECT * FROM evidence_cards WHERE id IN ({q_marks})', card_ids)}
        cards = [raw_rows[cid] for cid in card_ids if cid in raw_rows]
        if not cards:
            return ''

        # Research scopes require a current caller review before citation.
        from .research import Research
        research = Research(self)
        runs = self.rows("SELECT id,payload FROM research_records WHERE kind='run'")
        for card in cards:
            matching = [r for r in runs if json.loads(r['payload'])['scope_id'] == card['project']]
            if matching:
                parts = doc_ref.split(':')
                if len(parts) != 3 or parts[0] != 'research':
                    raise ValueError('研究引用须指定 research:run_id:atom_id，以校验具体论点绑定')
                allowed = {b['card_id'] for run in matching if run['id'] == parts[1]
                           for b in research.accepted(run['id']) if b['atom_id'] == parts[2]}
                if card['id'] not in allowed:
                    raise ValueError('此论点与证据的绑定尚未通过当前版本的上层支持关系二审: ' + card['id'])

        # Record usage provenance
        self.record_card_usage([c['id'] for c in cards], doc_ref=doc_ref, anchor=anchor)

        # Auto writeback to Zotero child notes if requested
        if auto_writeback:
            try:
                self.writeback_cards_to_zotero([c['id'] for c in cards])
            except Exception as exc:
                raise RuntimeError('Zotero note writeback failed') from exc

        from .zotero import format_gbt7714_entry, Library
        lib = Library()

        # Deduplicate bibliography sources in order of appearance
        bib_entries = []
        bib_index_map = {}
        bibtex_entries = []

        for c in cards:
            src = c['source']
            source_uid = src.get('item_key') or src.get('doi') or src.get('title') or c['id']
            if source_uid not in bib_index_map:
                idx = len(bib_entries) + 1
                bib_index_map[source_uid] = idx
                meta = {
                    'key': src.get('item_key'),
                    'title': src.get('title') or c.get('citation'),
                    'creators': src.get('authors') or [],
                    'year': src.get('year') or '',
                    'doi': src.get('doi') or '',
                    'publication': '',
                    'type': 'journalArticle',
                }
                if src.get('item_key'):
                    try:
                        live_meta = lib.summary(lib.raw_item(src['item_key']))
                        meta.update({k: v for k, v in live_meta.items() if v})
                    except Exception:
                        pass
                bib_entries.append((idx, meta, src.get('citekey') or f"ref{idx}"))
                ck = src.get('citekey') or f"ref{idx}"
                authors_bib = ' and '.join(meta.get('creators') or ['Anonymous'])
                bibtex_entries.append(
                    f"@article{{{ck},\n"
                    f"  author = {{{authors_bib}}},\n"
                    f"  title = {{{meta.get('title')}}},\n"
                    f"  journal = {{{meta.get('publication') or 'Preprint'}}},\n"
                    f"  year = {{{meta.get('year') or '2025'}}},\n"
                    f"  doi = {{{meta.get('doi') or ''}}}\n"
                    f"}}"
                )

        style_low = (style or 'gbt7714').lower()
        if style_low in ('gbt7714', 'numeric', 'default'):
            inline_markers = []
            for c in cards:
                src = c['source']
                uid = src.get('item_key') or src.get('doi') or src.get('title') or c['id']
                idx = bib_index_map[uid]
                pg = f", p.{src['pages']}" if src.get('pages') else ""
                inline_markers.append(f"[{idx}{pg}]")
            bib_lines = [format_gbt7714_entry(m, index=idx) for idx, m, _ in bib_entries]
            return json.dumps({
                'style': 'gbt7714',
                'inline': ''.join(inline_markers),
                'inline_list': inline_markers,
                'bibliography': '\n'.join(bib_lines),
                'bibtex': '\n\n'.join(bibtex_entries),
                'cards': [{'card_id': c['id'], 'ref_index': bib_index_map[c['source'].get('item_key') or c['source'].get('doi') or c['source'].get('title') or c['id']], 'zotero_link': c['source'].get('zotero_link')} for c in cards],
            }, ensure_ascii=False, indent=2)
        elif style_low == 'pandoc':
            inline_markers = [f"[@{c['source']['citekey']}" + (f", p. {c['source']['pages']}]" if c['source'].get('pages') else "]") for c in cards]
            bib_lines = [format_gbt7714_entry(m, index=idx) for idx, m, _ in bib_entries]
            return json.dumps({
                'style': 'pandoc',
                'inline': '; '.join(inline_markers),
                'bibliography': '\n'.join(bib_lines),
                'bibtex': '\n\n'.join(bibtex_entries),
            }, ensure_ascii=False, indent=2)
        elif style_low == 'apa':
            inline_markers = []
            for c in cards:
                src = c['source']
                first_auth = (src['authors'][0].split()[-1] if src.get('authors') else 'Anon')
                et = ' et al.' if len(src.get('authors') or []) > 1 else ''
                yr = src.get('year') or 'n.d.'
                pg = f", p. {src['pages']}" if src.get('pages') else ""
                inline_markers.append(f"({first_auth}{et}, {yr}{pg})")
            bib_lines = [format_gbt7714_entry(m, index=None) for _, m, _ in bib_entries]
            return json.dumps({
                'style': 'apa',
                'inline': '; '.join(inline_markers),
                'bibliography': '\n'.join(bib_lines),
                'bibtex': '\n\n'.join(bibtex_entries),
            }, ensure_ascii=False, indent=2)
        elif style_low == 'prompt':
            blocks = []
            for c in cards:
                src = c['source']
                uid = src.get('item_key') or src.get('doi') or src.get('title') or c['id']
                idx = bib_index_map[uid]
                page_str = f" | p.{src['pages']}" if src.get('pages') else ""
                cit_label = src.get('citation') if (src.get('citation') and src.get('citation') != src.get('title')) else f"{src['title']} ({src.get('year') or 'n.d.'})"
                blocks.append(
                    f"> [{c['id']}] 参考文献[{idx}] {cit_label}{page_str} (打分: {c['score']}/10)\n"
                    f"> 核心论据: {c['summary']}\n"
                    f"> 原文引文: “{c['quote'].strip()}”\n"
                    f"> 溯源链接: {src.get('zotero_link') or '无'}"
                )
            bib_lines = [format_gbt7714_entry(m, index=idx) for idx, m, _ in bib_entries]
            blocks.append("\n### 参考文献 (GB/T 7714):\n" + "\n".join(bib_lines))
            return "\n\n".join(blocks)
        elif style_low == 'inline':
            refs = [f"[{bib_index_map[c['source'].get('item_key') or c['source'].get('doi') or c['source'].get('title') or c['id']]}]" for c in cards]
            return ''.join(refs)
        elif style_low == 'bibtex':
            return '\n\n'.join(bibtex_entries)
        return json.dumps(cards, ensure_ascii=False, indent=2)

    def writeback_cards_to_zotero(self, card_ids: list[str]) -> list[dict]:
        """Group evidence cards by their Zotero item_key and write/update child notes in Zotero."""
        if not card_ids:
            return []
        q_marks = ','.join('?' for _ in card_ids)
        rows = [self._enrich_card_v2(r) for r in self.rows(f'SELECT * FROM evidence_cards WHERE id IN ({q_marks})', card_ids)]
        by_item: dict[str, list[dict]] = {}
        for c in rows:
            ik = c.get('zotero_key') or c['source'].get('item_key') or ''
            if ik:
                by_item.setdefault(ik, []).append(c)
        from .zotero import Library
        lib = Library()
        results = []
        for item_key, group in by_item.items():
            try:
                # Include all previously used/pinned cards for this item so the note stays cumulative
                all_item_cards = [self._enrich_card_v2(r) for r in self.rows(
                    "SELECT * FROM evidence_cards WHERE zotero_key=? AND (pinned=1 OR used_in_json!='[]' OR id IN (" + ','.join('?' for _ in group) + ")) ORDER BY score DESC",
                    [item_key] + [g['id'] for g in group]
                )]
                res = lib.write_evidence_note(item_key, all_item_cards or group)
                results.append(res)
            except Exception as e:
                results.append({'parent_key': item_key, 'error': str(e)})
        return results

    def enqueue(self,pid,kind,prompt,source='web',options=None):
        self.project(pid)
        if kind not in ('ask','evidence','index','coverage','verify','research') or source not in ('local','web'):
            raise ValueError('无效任务类型')
        if kind != 'index' and not str(prompt).strip():
            raise ValueError('请输入研究问题')
        if kind in ('ask','evidence','index','coverage','verify') or source == 'local':
            if not self.rows('SELECT id FROM papers WHERE project=?',(pid,)):
                raise ValueError('请先导入项目文献')
        options = dict(options or {})
        if len(json.dumps(options)) > 20000:
            raise ValueError('任务参数过大')
        frozen_settings = None
        if self.cfg and kind in PAPERQA_KINDS:
            from .models import build_paperqa_settings
            from .model_events import read_selection
            catalog = catalog_for(self.cfg).load()
            follows_dt = options.get('use_deeptutor_model') or options.get('mode', catalog.get('mode', 'deeptutor')) == 'deeptutor'
            if follows_dt:
                state = read_selection(options.get('model_client_id'), options.get('model_sync_revision'))
                if state:
                    options['deeptutor_selection'] = state['selection']
                    options['model_sync_revision'] = state['revision']
                    options['model_client_id'] = state['client_id']
                    options['model_session_id'] = state.get('session_id', '')
            frozen_settings = build_paperqa_settings(catalog, options=options)
            options['model_snapshot'] = {'llm': frozen_settings['llm'], 'summary_llm': frozen_settings['summary_llm'],
                                         'embedding': frozen_settings['embedding']}
        manifest=self.rows('SELECT * FROM papers WHERE project=?',(pid,))
        if options.get('papers'):
            wanted=set(options['papers'])
            subset=[p['name'] for p in manifest if p['id'] in wanted]
            if not subset:
                raise ValueError('所选文献不在当前项目中')
            options['subset']=subset
        jid=uuid.uuid4().hex
        work=self.folder(pid)/'jobs'/jid
        papers=work/'papers'
        papers.mkdir(parents=True)
        if frozen_settings is not None:
            settings_file = work/'paperqa_settings.json'
            settings_file.write_text(json.dumps(frozen_settings, ensure_ascii=False))
            os.chmod(settings_file, 0o600)
        # The snapshot always holds every project paper so the shared project index stays
        # complete; a paper subset only narrows which indexed documents are consulted.
        for paper in manifest:
            src=self.folder(pid)/'papers'/paper['name']
            try:
                os.link(src,papers/paper['name'])
            except OSError:
                shutil.copy2(src,papers/paper['name'])
        (work/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False))
        # Give PaperQA2 the curated Zotero metadata instead of letting it guess titles.
        known=[p for p in manifest if p.get('title')]
        if known:
            with (work/'paperqa_manifest.csv').open('w',newline='') as f:
                w=csv.writer(f)
                w.writerow(['file_location','title'])
                for p in known:
                    w.writerow([p['name'],p['title']])
        with self.db() as db:
            db.execute('INSERT INTO jobs(id,project,kind,prompt,source,status,message,created,options) VALUES(?,?,?,?,?,?,?,?,?)',
                       (jid,pid,kind,str(prompt)[:20000],source,'queued','等待执行',time.time(),json.dumps(options,ensure_ascii=False)))
        try:
            prompt_str = str(prompt or '').strip()
            summary = f"启动检索任务 [{jid[:8]}]: {prompt_str[:50]}" if prompt_str else f"启动任务 [{jid[:8]}]: {kind}"
            self.log_trace(
                pid,
                f"job_{kind}",
                'running',
                summary,
                {'job_id': jid, 'kind': kind, 'prompt': prompt_str[:120]}
            )
        except Exception:
            pass
        return jid

    def update(self,jid,status,message):
        with self.db() as db:
            db.execute('UPDATE jobs SET status=?,message=? WHERE id=?',(status,message,jid))


class Worker:
    def __init__(self,store,cfg,guard=gpu_busy):
        self.store,self.cfg,self.guard=store,cfg,guard
        self.lock=threading.Lock()
        self.process=None
        self.current=None
        self.stopped=threading.Event()

    def cancel(self,jid):
        with self.lock:
            if self.current == jid and self.process and self.process.poll() is None:
                os.killpg(self.process.pid,signal.SIGTERM)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='cancelled',message='已取消' WHERE id=? AND status IN ('queued','waiting','running')",(jid,))

    def run(self):
        while not self.stopped.wait(3):
            jobs=self.store.rows("SELECT * FROM jobs WHERE status IN ('queued','waiting') ORDER BY created LIMIT 1")
            if not jobs:
                continue
            job=jobs[0]
            busy,reason=self.guard()
            if busy:
                with self.lock:
                    if self.store.rows('SELECT status FROM jobs WHERE id=?',(job['id'],))[0]['status'] != 'cancelled':
                        self.store.update(job['id'],'waiting',reason)
                continue
            self.execute(job)

    def execute(self,job):
        jid,pid=job['id'],job['project']
        folder=self.store.folder(pid)
        work=folder/'jobs'/jid
        work.mkdir(exist_ok=True)
        # Freeze input files and record hashes so results retain their exact source scope.
        papers=work/'papers'
        papers.mkdir(exist_ok=True)
        manifest=json.loads((work/'manifest.json').read_text())
        engine='gpt' if job['kind']=='research' else 'paperqa'
        settings_path = self.cfg.get('paperqa_settings')
        if engine == 'paperqa':
            try:
                settings_file = work/'paperqa_settings.json'
                if not settings_file.exists():
                    from .models import build_paperqa_settings
                    opts = json.loads(job.get('options') or '{}') if isinstance(job.get('options'), str) else (job.get('options') or {})
                    s_dict = build_paperqa_settings(catalog_for(self.cfg).load(), options=opts)
                    settings_file.write_text(json.dumps(s_dict, ensure_ascii=False, indent=2))
                    os.chmod(settings_file, 0o600)
                settings_path = str(settings_file)
            except Exception as exc:
                self.store.update(jid, 'failed', '模型配置同步失败：' + str(exc))
                return
        payload={**job,'papers':str(papers),'output':str(work/'result.json'),'manifest':manifest,
                 'index':str(folder/'index'),'settings':settings_path}
        (work/'input.json').write_text(json.dumps(payload,ensure_ascii=False))
        try:
            with self.lock:
                if self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']=='cancelled':
                    return
                self.current=jid
                self.store.update(jid,'running','正在启动执行引擎...')
                log=(work/'run.log').open('w', buffering=1)
                worker_env={**os.environ,'PYTHONUNBUFFERED':'1','LITELLM_LOCAL_MODEL_COST_MAP':'True'}
                self.process=subprocess.Popen(
                    [self.cfg[engine+'_python'],'-u',str(ROOT/'searchtools/engine_worker.py'),str(work/'input.json')],
                    cwd=self.cfg[engine+'_root'],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=worker_env,
                    start_new_session=True,
                )
            try:
                deadline=time.monotonic()+7200
                cancelled_at=None
                last_msg='正在启动执行引擎...'
                log_path=work/'run.log'
                while self.process.poll() is None:
                    if self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']=='cancelled':
                        cancelled_at=cancelled_at or time.monotonic()
                        if time.monotonic()-cancelled_at>10:
                            try:os.killpg(self.process.pid,signal.SIGKILL)
                            except ProcessLookupError:pass
                    else:
                        try:
                            if log_path.exists():
                                tail=log_path.read_text(errors='replace')[-4000:]
                                stage_msg=None
                                for line in reversed(tail.splitlines()):
                                    s=line.strip()
                                    if not s:
                                        continue
                                    if '[STAGE]' in s:
                                        stage_msg=s.split('[STAGE]',1)[1].strip()
                                        break
                                    elif any(k in s for k in ('paper_Search','paper_search','gather_evidence','gen_answer','Indexing','Building index',' Conduct ','Writing ')):
                                        stage_msg=f'执行中: {s[:90]}'
                                        break
                                if stage_msg and stage_msg!=last_msg:
                                    last_msg=stage_msg
                                    self.store.update(jid,'running',stage_msg)
                        except Exception:
                            pass
                    if time.monotonic()>deadline:
                        raise subprocess.TimeoutExpired(self.process.args,7200)
                    time.sleep(0.5)
                code=self.process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid,signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid,signal.SIGKILL)
                    self.process.wait()
                raise RuntimeError('任务超过两小时，请缩小范围后重试')
            finally:
                log.close()
            with self.lock:
                state=self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']
                if state!='cancelled':
                    if code != 0 or not (work/'result.json').exists():
                        err_detail = ''
                        if (work/'run.log').exists():
                            tail_lines = [l.strip() for l in (work/'run.log').read_text(errors='replace').splitlines() if l.strip()]
                            for l in reversed(tail_lines):
                                if 'RuntimeError:' in l:
                                    err_detail = l.split('RuntimeError:', 1)[1].strip()
                                    break
                        raise RuntimeError(err_detail or '引擎执行失败，请查看任务日志')
                    result=json.loads((work/'result.json').read_text())
                    (folder/'results'/f'{jid}.md').write_text(result.get('answer','任务完成'))
                    saved_cards = self.store.extract_and_save_evidence_from_result(pid, jid, result)
                    if result.get('contexts') and not saved_cards:
                        raise RuntimeError('模型已返回片段，但来源验证或证据卡持久化未通过；请检查任务日志')
                    self.store.update(jid,'done','已完成')
                    try:
                        job_rows = self.store.rows('SELECT kind, prompt FROM jobs WHERE id=?', (jid,))
                        j_kind = job_rows[0]['kind'] if job_rows else 'task'
                        j_prompt = (job_rows[0]['prompt'] or '').strip() if job_rows else ''
                        card_cnt = len(saved_cards) if saved_cards else 0
                        summary = f"检索完成 [{jid[:8]}]: 提取 {card_cnt} 张证据卡" if card_cnt > 0 else f"任务完成 [{jid[:8]}]: {j_kind} ({j_prompt[:40]})"
                        self.store.log_trace(
                            pid,
                            f"job_{j_kind}",
                            'done',
                            summary,
                            {'job_id': jid, 'kind': j_kind, 'cards_count': card_cnt, 'answer': (result.get('answer') or '')[:300]}
                        )
                    except Exception:
                        pass
        except Exception as e:
            if self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']!='cancelled':
                self.store.update(jid,'failed',str(e))
                try:
                    job_rows = self.store.rows('SELECT kind FROM jobs WHERE id=?', (jid,))
                    j_kind = job_rows[0]['kind'] if job_rows else 'task'
                    self.store.log_trace(
                        pid,
                        f"job_{j_kind}",
                        'failed',
                        f"任务失败 [{jid[:8]}]: {str(e)[:100]}",
                        {'job_id': jid, 'kind': j_kind, 'error': str(e)[:200]}
                    )
                except Exception:
                    pass
        finally:
            with self.lock:
                self.process=None
                self.current=None
