from __future__ import annotations
import json
import mimetypes
import os
import secrets
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen
import time
import uuid

from .core import Store, Worker, config, gpu_busy, ROOT, catalog_for, index_files
from .models import test_payload, sync_deeptutor_live_selection
from .model_events import ModelEventHub
from .zotero import Library, ZoteroError, render_markdown, render_freemind
from .deeptutor_bridge import (
    get_deeptutor_status, register_mcp_server, list_knowledge_bases,
    link_folder_to_kb, push_evidence_to_document, push_evidence_to_notebook
)


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg['data'], cfg=cfg)
        self.model_events = ModelEventHub(cfg)
        self.worker = Worker(self.store, cfg)
        self.catalog = catalog_for(cfg)
        self.library = Library()
        self.token = secrets.token_urlsafe(32)
        self.gpu = (0, (True, '等待检查'))

    def status(self):
        if time.time() - self.gpu[0] > 10:
            self.gpu = (time.time(), gpu_busy())
        services = {}
        for name, url in [('deeptutor', self.cfg.get('deeptutor_url', 'http://127.0.0.1:3782/chat')),
                          ('gpt', self.cfg.get('gpt_url', 'http://127.0.0.1:8002')),
                          ('ollama', 'http://127.0.0.1:11434/api/version')]:
            try:
                with urlopen(url, timeout=1) as r:
                    services[name] = r.status == 200
            except Exception:
                services[name] = False
        return {'services': services, 'gpu_busy': self.gpu[1][0], 'gpu_message': self.gpu[1][1]}

    def test_connection(self, conn_body, service):
        data = self.catalog.load()
        payload = test_payload(data, conn_body, service)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            infile = tmp / 'test_in.json'
            outfile = tmp / 'test_out.json'
            payload['output'] = str(outfile)
            infile.write_text(json.dumps(payload, ensure_ascii=False))
            python_bin = self.cfg.get('paperqa_python', 'python3')
            worker_script = str(ROOT / 'searchtools/engine_worker.py')
            try:
                proc = subprocess.run([python_bin, worker_script, str(infile)],
                                      capture_output=True, text=True, timeout=25)
                if outfile.exists():
                    return json.loads(outfile.read_text())
                err = proc.stderr.strip() or proc.stdout.strip() or f'Process exited with code {proc.returncode}'
                return {'ok': False, 'error': err}
            except subprocess.TimeoutExpired:
                return {'ok': False, 'error': '连接测试超时（超过 25 秒），上游模型服务响应过慢'}
            except Exception as e:
                return {'ok': False, 'error': str(e)}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, ctype='application/json; charset=utf-8', download=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                             "default-src 'self' 'unsafe-inline' vscode-webview:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src *; frame-ancestors *; base-uri 'none'")
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With, X-SearchTools-Token, X-Project, X-Filename')
            if download:
                from urllib.parse import quote
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(download))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With, X-SearchTools-Token, X-Project, X-Filename')
            self.end_headers()

        def trusted(self, write=False):
            expected = f'127.0.0.1:{app.cfg["port"]}'
            host = self.headers.get('Host', '')
            if host != expected and host != f'localhost:{app.cfg["port"]}':
                return False
            origin = self.headers.get('Origin')
            is_webview = origin and (origin.startswith('vscode-webview://') or origin == 'null')
            if origin:
                allowed_origins = {
                    f'http://127.0.0.1:{app.cfg["port"]}',
                    f'http://localhost:{app.cfg["port"]}',
                    'http://127.0.0.1:3782',
                    'http://localhost:3782',
                    'http://127.0.0.1:8001',
                    'http://localhost:8001',
                }
                if origin not in allowed_origins and not is_webview:
                    return False
            if not write:
                return True
            token_valid = secrets.compare_digest(self.headers.get('X-SearchTools-Token', ''), app.token)
            local_mcp = self.headers.get('X-SearchTools-Token', '') == 'local-mcp' and self.client_address[0] in ('127.0.0.1', '::1')
            origin_trusted = (origin in ('http://127.0.0.1:3782', 'http://localhost:3782', f'http://127.0.0.1:{app.cfg["port"]}', f'http://localhost:{app.cfg["port"]}') or is_webview)
            if (token_valid or local_mcp or origin_trusted) and self.client_address[0] in ('127.0.0.1', '::1'):
                return True
            return False

        def do_GET(self):
            if not self.trusted():
                return self.send(403, {'error': '请求来源不受信任'})
            try:
                u = urlparse(self.path)
                q = parse_qs(u.query)
                path = u.path

                if path == '/api/active-project':
                    return self.send(200, {'project': app.store.get_active_project()})

                if path == '/api/history':
                    pid = q.get('project', [''])[0] or app.store.get_active_project()
                    if not pid:
                        return self.send(200, {'jobs': [], 'total': 0})
                    jobs = app.store.rows(
                        "SELECT id, project, kind, prompt, source, status, message, created FROM jobs WHERE project=? ORDER BY created DESC",
                        (pid,)
                    )
                    for j in jobs:
                        c_cnt = app.store.rows('SELECT COUNT(*) as cnt FROM evidence_cards WHERE job_id=?', (j['id'],))
                        j['cards_count'] = c_cnt[0]['cnt'] if c_cnt else 0
                        res_file = app.store.folder(j['project']) / 'jobs' / j['id'] / 'result.json'
                        if res_file.exists():
                            try:
                                r_data = json.loads(res_file.read_text(encoding='utf-8'))
                                j['answer_preview'] = (r_data.get('answer') or '')[:300]
                                j['has_result'] = True
                            except Exception:
                                j['answer_preview'] = ''
                                j['has_result'] = False
                        else:
                            j['answer_preview'] = ''
                            j['has_result'] = False
                    return self.send(200, {'jobs': jobs, 'total': len(jobs)})

                # State and project
                if path == '/api/state':
                    return self.send(200, {
                        'projects': app.store.rows('SELECT * FROM projects ORDER BY created'),
                        **app.status()
                    })
                if path == '/api/project':
                    pid = q['id'][0]
                    project = app.store.project(pid)
                    return self.send(200, {
                        'project': project,
                        'folder': str(app.store.folder(pid) / 'papers'),
                        'papers': app.store.rows('SELECT * FROM papers WHERE project=? ORDER BY added DESC', (pid,)),
                        'notes': app.store.rows('SELECT * FROM notes WHERE project=? ORDER BY created DESC', (pid,)),
                        'jobs': app.store.rows('SELECT * FROM jobs WHERE project=? ORDER BY created DESC', (pid,)),
                    })
                if path == '/api/job':
                    rows = app.store.rows('SELECT * FROM jobs WHERE id=?', (q['id'][0],))
                    if not rows:
                        raise ValueError('任务不存在')
                    job = rows[0]
                    folder = app.store.folder(job['project']) / 'jobs' / job['id']
                    job['log'] = (folder / 'run.log').read_text(errors='replace')[-30000:] if (folder / 'run.log').exists() else ''
                    job['result'] = json.loads((folder / 'result.json').read_text()) if (folder / 'result.json').exists() else None
                    return self.send(200, job)
                if path == '/api/file':
                    rows = app.store.rows('SELECT * FROM papers WHERE id=?', (q['id'][0],))
                    if not rows:
                        raise ValueError('文献不存在')
                    paper = rows[0]
                    file = app.store.folder(paper['project']) / 'papers' / paper['name']
                    return self.send(200, file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream', paper['name'])
                if path == '/api/export':
                    rows = app.store.rows('SELECT * FROM jobs WHERE id=?', (q['id'][0],))
                    if not rows or rows[0]['status'] != 'done':
                        raise ValueError('结果尚未生成')
                    job = rows[0]
                    file = app.store.folder(job['project']) / 'results' / (job['id'] + '.md')
                    return self.send(200, file.read_bytes(), 'text/markdown; charset=utf-8', 'research-result.md')

                # Models catalog
                if path == '/api/models':
                    return self.send(200, app.catalog.public())

                # Index status
                if path == '/api/index/status':
                    pid = q['project'][0]
                    p_folder = app.store.folder(pid)
                    # Check paperqa index subdirectories
                    cat_data = app.catalog.load()
                    from .models import index_name
                    idx_sub = p_folder / 'index' / index_name(cat_data)
                    fallback_sub = p_folder / 'index' / ('project-' + pid)
                    target_idx = idx_sub if idx_sub.exists() else fallback_sub
                    fmap = index_files(target_idx)
                    papers = app.store.rows('SELECT * FROM papers WHERE project=?', (pid,))
                    indexed_cnt = sum(1 for p in papers if p['name'] in fmap and fmap[p['name']] != 'ERROR')
                    failed_cnt = sum(1 for p in papers if fmap.get(p['name']) == 'ERROR')
                    return self.send(200, {
                        'total': len(papers),
                        'indexed': indexed_cnt,
                        'failed': failed_cnt,
                        'pending': len(papers) - indexed_cnt - failed_cnt,
                        'index_name': target_idx.name,
                        'ready': (indexed_cnt == len(papers) and len(papers) > 0)
                    })

                # Zotero / Library endpoints
                if path == '/api/library/status':
                    return self.send(200, app.library.status())
                if path == '/api/library/collections':
                    return self.send(200, app.library.collections())
                if path == '/api/library/tags':
                    return self.send(200, app.library.tags())
                if path == '/api/library/searches':
                    return self.send(200, app.library.searches())
                if path == '/api/library/items':
                    col = q.get('collection', [''])[0]
                    kw = q.get('q', [''])[0]
                    mode = q.get('mode', ['titleCreatorYear'])[0]
                    tag = q.get('tag', [''])[0]
                    search = q.get('search', [''])[0]
                    limit = int(q.get('limit', [200])[0])
                    return self.send(200, app.library.items(collection=col, q=kw, mode=mode, tag=tag, search=search, limit=limit))
                if path == '/api/library/item':
                    key = q['id'][0]
                    return self.send(200, app.library.item(key))
                if path == '/api/mindmap':
                    col = q.get('collection', [''])[0]
                    items_str = q.get('items', [''])[0]
                    items_list = [k.strip() for k in items_str.split(',') if k.strip()]
                    group = q.get('group_by', ['paper'])[0]
                    fmt = q.get('format', ['json'])[0]
                    tree = app.library.mindmap(collection=col, items=items_list, group_by=group)
                    if fmt == 'md':
                        return self.send(200, render_markdown(tree).encode('utf-8'), 'text/markdown; charset=utf-8', 'mindmap.md')
                    if fmt == 'mm':
                        return self.send(200, render_freemind(tree).encode('utf-8'), 'application/x-freemind', 'mindmap.mm')
                    return self.send(200, tree)

                # Evidence cards
                if path == '/api/evidence-cards':
                    pid = q.get('project', [''])[0] or None
                    term = q.get('q', [''])[0]
                    tag = q.get('tag', [''])[0]
                    min_score = float(q['min_score'][0]) if q.get('min_score') else None
                    pinned_only = q.get('pinned_only', ['0'])[0] in ('1', 'true', 'True')
                    cards = app.store.list_evidence_cards(pid, q=term or None, tag=tag or None, min_score=min_score, pinned_only=pinned_only)
                    return self.send(200, {'cards': cards, 'total': len(cards)})

                if path == '/api/evidence-cards/locate':
                    cid = q.get('id', [''])[0]
                    force = q.get('force', ['0'])[0] in ('1', 'true', 'True')
                    if not cid:
                        return self.send(400, {'ok': False, 'error': 'Missing card id'})
                    res = app.store.locate_card(cid, force=force)
                    return self.send(200, res)

                if path == '/api/evidence-cards/by-item':
                    item_key = q.get('item', [''])[0]
                    cards = [app.store._enrich_card_v2(r) for r in app.store.rows('SELECT * FROM evidence_cards WHERE zotero_key=? ORDER BY score DESC', (item_key,))]
                    return self.send(200, {'cards': cards, 'total': len(cards)})

                if path == '/api/deeptutor/model-state':
                    return self.send(200, app.model_events.current(q.get('client_id', [None])[0]))
                if path == '/api/deeptutor/model-events':
                    client_id = q.get('client_id', [None])[0]
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    try:
                        generation = -1
                        while True:
                            with app.model_events.condition:
                                if generation == app.model_events.generation:
                                    app.model_events.condition.wait(timeout=15)
                                changed = generation != app.model_events.generation
                                generation = app.model_events.generation
                                if changed:
                                    try:
                                        state = app.model_events.current(client_id)
                                    except ValueError:
                                        state = {'status': 'unbound', 'model': {}}
                                    payload = ('data: ' + json.dumps(state, ensure_ascii=False) + '\n\n').encode()
                                else:
                                    payload = b': keepalive\n\n'
                            self.wfile.write(payload)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return

                if path == '/api/zotero/action-status':
                    return self.send(200, app.store.zotero_action_status(q.get('id', [''])[0]))

                if path == '/api/zotero/actions':
                    status_only = q.get('status', ['0'])[0] == '1'
                    if status_only:
                        last = getattr(app.store, '_last_zotero_poll', 0)
                        return self.send(200, {
                            'connected': (time.time() - last < 5.0) if last else False,
                            'last_poll': last,
                            'pending_actions': app.store.rows("SELECT COUNT(*) AS n FROM zotero_actions WHERE status IN ('pending','delivered') AND created>?", (time.time()-300,))[0]['n']
                        })
                    actions = app.store.pop_zotero_actions()
                    return self.send(200, {'actions': actions})

                if path == '/api/zotero/logs':
                    return self.send(200, {'logs': getattr(app.store, '_zotero_logs', [])})

                if path == '/api/scope/status':
                    pid = q.get('scope_id', q.get('id', ['']))[0]
                    if not pid:
                        projs = app.store.rows('SELECT id FROM projects ORDER BY created DESC LIMIT 1')
                        pid = projs[0]['id'] if projs else app.store.create_project('学术实证课题')
                    return self.send(200, app.store.get_scope_status(pid))

                if path == '/api/candidates':
                    sid = q.get('scope_id', [''])[0]
                    return self.send(200, {'batches': app.store.list_candidate_batches(sid)})

                if path == '/api/traces':
                    sid = q.get('scope_id', [''])[0]
                    active_jobs = app.store.rows(
                        "SELECT id, project, kind, prompt, status, message, created FROM jobs WHERE status IN ('queued','waiting','running') ORDER BY created DESC"
                    )
                    return self.send(200, {'traces': app.store.list_traces(sid, limit=60), 'active_jobs': active_jobs})

                if path == '/api/events':
                    sid = q.get('scope_id', [''])[0]
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'keep-alive')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    last_sig = ''
                    for _ in range(180):
                        try:
                            traces = app.store.list_traces(sid, limit=25)
                            active_jobs = app.store.rows(
                                "SELECT id, project, kind, prompt, status, message, created FROM jobs WHERE status IN ('queued','waiting','running') ORDER BY created DESC"
                            )
                            batches = app.store.list_candidate_batches(sid)
                            payload = {'traces': traces, 'active_jobs': active_jobs, 'pending_batches': [b for b in batches if b.get('status') == 'pending_confirmation']}
                            sig = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                            if sig != last_sig:
                                last_sig = sig
                                self.wfile.write(f"data: {sig}\n\n".encode('utf-8'))
                                self.wfile.flush()
                            else:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                            time.sleep(1.0)
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break
                    return

                # DeepTutor status & knowledge bases
                if path == '/api/deeptutor/status':
                    return self.send(200, get_deeptutor_status())
                if path == '/api/deeptutor/kbs':
                    return self.send(200, {'kbs': list_knowledge_bases()})

                # Static files
                static = {
                    '/': 'index.html',
                    '/app.js': 'app.js',
                    '/style.css': 'style.css',
                    '/ask.js': 'ask.js',
                    '/library.js': 'library.js',
                    '/models.js': 'models.js',
                    '/workbench': 'workbench.html',
                    '/workbench.html': 'workbench.html',
                    '/workbench.js': 'workbench.js',
                    '/workbench.css': 'workbench.css',
                    '/deeptutor_companion.js': 'deeptutor_companion.js',
                    '/static/deeptutor_companion.js': 'deeptutor_companion.js',
                    '/api/deeptutor/companion.js': 'deeptutor_companion.js',
                }
                if path in static:
                    file = ROOT / 'searchtools/static' / static[path]
                    if not file.exists():
                        return self.send(404, {'error': f'文件未找到: {static[path]}'})
                    body = file.read_bytes().replace(b'__TOKEN__', app.token.encode())
                    return self.send(200, body, mimetypes.guess_type(file)[0] + '; charset=utf-8')

                return self.send(404, {'error': '未找到页面'})
            except (ValueError, KeyError, FileNotFoundError, ZoteroError) as e:
                self.send(400, {'error': str(e)})

        def do_POST(self):
            if not self.trusted(write=True):
                return self.send(403, {'error': '请求来源不受信任，请重新打开页面'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                limit = 100 * 1024 * 1024 if self.path == '/api/upload' else 1024 * 1024
                if not 0 < length <= limit:
                    raise ValueError('文件过大或请求为空（单文件最大 100 MB）')
                raw = self.rfile.read(length)

                if self.path == '/api/upload':
                    pid = self.headers.get('X-Project', '')
                    name = unquote(self.headers.get('X-Filename', ''))
                    return self.send(200, {'id': app.store.import_paper(pid, name, raw)})

                data = json.loads(raw)
                if self.path == '/api/projects':
                    return self.send(200, {'id': app.store.create_project(data['name'])})

                if self.path == '/api/active-project':
                    pid = data.get('project', '')
                    app.store.set_active_project(pid)
                    return self.send(200, {'ok': True, 'project': pid})

                if self.path == '/api/jobs':
                    return self.send(200, {
                        'id': app.store.enqueue(
                            data['project'],
                            data['kind'],
                            data.get('prompt', ''),
                            data.get('source', 'local'),
                            options=data.get('options')
                        )
                    })

                if self.path == '/api/cancel':
                    app.worker.cancel(data['id'])
                    return self.send(200, {'ok': True})

                if self.path == '/api/notes':
                    app.store.project(data['project'])
                    with app.store.db() as db:
                        db.execute('INSERT INTO notes(id,project,title,body,created) VALUES(?,?,?,?,?)',
                                   (uuid.uuid4().hex, data['project'], str(data['title'])[:200], str(data['body'])[:200000], time.time()))
                    return self.send(200, {'ok': True})

                if self.path == '/api/paper/remove':
                    app.store.remove_paper(data['project'], data['id'])
                    return self.send(200, {'ok': True})

                if self.path == '/api/job/remove':
                    app.store.remove_job(data['project'], data['id'])
                    return self.send(200, {'ok': True})

                # Models update and test
                if self.path == '/api/models':
                    return self.send(200, app.catalog.update(data))
                if self.path == '/api/models/mode':
                    mode = data.get('mode', 'deeptutor')
                    return self.send(200, app.catalog.update({'mode': mode}))
                if self.path == '/api/models/test':
                    conn = data.get('connection') or {}
                    svc = data.get('service', 'llm')
                    res = app.test_connection(conn, svc)
                    return self.send(200, res)

                # Zotero imports
                if self.path == '/api/library/import':
                    pid = data['project']
                    keys = data.get('keys') or ([data['key']] if 'key' in data else [])
                    imported = []
                    for k in keys:
                        p = app.library.attachment_path(k)
                        if p and p.is_file():
                            body = p.read_bytes()
                            meta = app.library.summary(app.library.raw_item(k))
                            paperid = app.store.import_paper(
                                pid, p.name, body,
                                zotero_item=k,
                                title=meta.get('title'),
                                doi=meta.get('doi'),
                                origin='zotero'
                            )
                            imported.append({'key': k, 'id': paperid, 'title': meta.get('title')})
                    return self.send(200, {'ok': True, 'imported': imported})

                if self.path == '/api/library/notes':
                    pid = data['project']
                    key = data['key']
                    md_text = app.library.reading_notes(key)
                    if not md_text:
                        raise ValueError('该条目在 Zotero 中没有标注或笔记')
                    meta = app.library.summary(app.library.raw_item(key))
                    safe_title = meta.get('title', '文献')[:30].replace('/', '_')
                    fname = f"阅读笔记_{safe_title}.md"
                    paperid = app.store.import_paper(
                        pid, fname, md_text.encode('utf-8'),
                        zotero_item=key,
                        title=f"阅读笔记：{meta.get('title')}",
                        origin='zotero_note'
                    )
                    return self.send(200, {'ok': True, 'id': paperid, 'title': fname})

                # Evidence Cards CRUD and Citation Formatting
                if self.path == '/api/evidence-cards/save':
                    pid = data.get('project')
                    if not pid:
                        projs = app.store.rows('SELECT id FROM projects ORDER BY created DESC LIMIT 1')
                        pid = projs[0]['id'] if projs else app.store.create_project('学术实证课题')
                    cid = app.store.save_evidence_card(pid, data.get('card', {}))
                    return self.send(200, {'ok': True, 'id': cid})
                if self.path == '/api/evidence-cards/remove':
                    cid = str(data.get('id') or '').strip()
                    pid = data.get('project')
                    if not pid and cid:
                        r = app.store.rows('SELECT project FROM evidence_cards WHERE id=?', (cid,))
                        if r:
                            pid = r[0]['project']
                    if not pid or not cid:
                        return self.send(400, {'ok': False, 'error': 'Missing project or card id'})
                    app.store.remove_evidence_card(pid, cid)
                    return self.send(200, {'ok': True})
                if self.path == '/api/evidence-cards/pin':
                    cid = str(data.get('id') or '').strip()
                    pid = data.get('project')
                    if not pid and cid:
                        r = app.store.rows('SELECT project FROM evidence_cards WHERE id=?', (cid,))
                        if r:
                            pid = r[0]['project']
                    if not pid or not cid:
                        return self.send(400, {'ok': False, 'error': 'Missing project or card id'})
                    pinned = app.store.toggle_pin_evidence_card(pid, cid)
                    return self.send(200, {'ok': True, 'pinned': pinned})
                if self.path == '/api/evidence-cards/format':
                    formatted = app.store.format_evidence_cards_citation(
                        data.get('card_ids', []),
                        style=data.get('style', 'gbt7714'),
                        auto_writeback=bool(data.get('auto_writeback', True)),
                        doc_ref=data.get('doc_ref', 'deeptutor:draft'),
                        anchor=data.get('anchor', '')
                    )
                    return self.send(200, {'formatted': formatted})
                if self.path == '/api/evidence-cards/writeback':
                    res = app.store.writeback_cards_to_zotero(data.get('card_ids', []))
                    app.store.log_trace(data.get('scope_id', ''), 'cards_writeback_zotero', 'done',
                                        f"已将 {len(data.get('card_ids', []))} 张证据卡写回 Zotero 子笔记", {'results': res})
                    return self.send(200, {'ok': True, 'results': res})
                if self.path == '/api/open-zotero':
                    action = str(data.get('action') or '').strip()
                    if action == 'cleanup':
                        app.store.enqueue_zotero_action({'action': 'cleanup'})
                        return self.send(200, {'ok': True, 'action': 'cleanup'})
                    uri = str(data.get('uri') or '').strip()
                    cid = str(data.get('card_id') or '').strip()
                    locate_info = None
                    if cid:
                        locate_info = app.store.locate_card(cid)
                        # If locate succeeded with page_label, ensure open-pdf link has exact page
                        if locate_info and locate_info.get('status') in ('exact', 'fuzzy', 'page_only'):
                            att = locate_info.get('attachment_key')
                            pg = locate_info.get('page_label')
                            if att and pg:
                                uri = f"zotero://open-pdf/library/items/{att}?page={pg}"
                        elif not uri:
                            card = app.store.get_evidence_card(cid)
                            if card:
                                uri = card.get('zotero_link') or ''
                        # Enqueue for active Zotero Evidence Bridge plugin
                        if locate_info.get('status') != 'exact':
                            return self.send(422, {'ok': False, 'error': '完整引文尚未唯一定位，不能生成高亮', 'locate': locate_info})
                        action_id = app.store.enqueue_zotero_action({
                            'action': 'open_card',
                            'card_id': cid,
                            'card': app.store.get_evidence_card(cid),
                            'locate': locate_info
                        })
                        return self.send(202, {'ok': True, 'status': 'queued', 'action_id': action_id, 'locate': locate_info})
                    if uri and uri.startswith('zotero://'):
                        import subprocess
                        try:
                            subprocess.Popen(['xdg-open', uri])
                            return self.send(200, {'ok': True, 'uri': uri, 'locate': locate_info})
                        except Exception as ex:
                            return self.send(500, {'ok': False, 'error': str(ex)})
                    return self.send(400, {'ok': False, 'error': '无效或缺失的 Zotero 协议链接'})

                if self.path == '/api/zotero/action-ack':
                    return self.send(200, app.store.ack_zotero_action(data.get('action_id', ''), data.get('error', ''), data.get('result')))

                if self.path == '/api/zotero/log':
                    msg = str(data.get('msg') or '').strip()
                    if not hasattr(app.store, '_zotero_logs'):
                        app.store._zotero_logs = []
                    app.store._zotero_logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
                    if len(app.store._zotero_logs) > 100:
                        app.store._zotero_logs = app.store._zotero_logs[-100:]
                    print(f"[Zotero Plugin] {msg}", flush=True)
                    return self.send(200, {'ok': True})

                if self.path == '/api/evidence-cards/create-from-zotero':
                    try:
                        card = app.store.capture_reader_selection(data)
                    except ValueError as exc:
                        return self.send(422, {'ok': False, 'error': str(exc)})
                    return self.send(200, {'ok': True, 'card': card})

                # v3 Scope & Candidate Acquisition endpoints
                if self.path == '/api/scope/create':
                    res = app.store.create_or_update_scope(
                        name=data.get('name', ''),
                        item_keys=data.get('item_keys') or [],
                        collection=data.get('collection', ''),
                        zotero_query=data.get('zotero_query', ''),
                        scope_id=data.get('scope_id', ''),
                    )
                    app.store.log_trace(res['scope_id'], 'scope_create', 'done',
                                        f"研究范围「{res['name']}」就绪 ({res['with_pdf_count']} 篇有 PDF, {res['indexed_count']} 篇已索引)", res)
                    return self.send(200, res)

                if self.path == '/api/scope/add-items':
                    sid = data.get('scope_id', '')
                    keys = data.get('item_keys') or []
                    res = app.store.create_or_update_scope(scope_id=sid, item_keys=keys)
                    jid = ''
                    if res['with_pdf_count'] > 0 and res['pending_index_count'] > 0:
                        jid = app.store.enqueue(res['scope_id'], 'index', '增量构建索引', 'local', options={'use_deeptutor_model': True})
                    app.store.log_trace(res['scope_id'], 'scope_add_items', 'done',
                                        f"向研究范围新增 {len(keys)} 篇文献" + (f" (索引任务: {jid[:8]})" if jid else ""), {'job_id': jid, **res})
                    return self.send(200, {'ok': True, 'job_id': jid, **res})

                if self.path == '/api/candidates/stage':
                    res = app.store.save_candidate_batch(
                        scope_id=data.get('scope_id', ''),
                        topic=data.get('topic', '补充文献'),
                        query=data.get('query', ''),
                        candidates=data.get('candidates') or [],
                    )
                    app.store.log_trace(data.get('scope_id', ''), 'literature_stage', 'waiting',
                                        f"待确认补充文献: {res['total_staged']} 篇候选 (上限 20 篇)，请在工作台「补充文献」标签页勾选入库", res)
                    return self.send(200, res)

                if self.path == '/api/candidates/confirm':
                    from .acquire import execute_zotero_import_for_candidates
                    batch_id = data.get('batch_id', '')
                    selected_indices = set(data.get('selected_indices') or [])
                    direct_candidates = data.get('candidates') or []
                    scope_id = data.get('scope_id', '')
                    topic = data.get('topic', '')

                    if batch_id:
                        rows = app.store.rows('SELECT * FROM candidate_batches WHERE id=?', (batch_id,))
                        if not rows:
                            raise ValueError('候选批次不存在')
                        batch = rows[0]
                        scope_id = scope_id or batch.get('scope_id') or ''
                        topic = topic or batch.get('topic') or '补充文献'
                        all_cands = json.loads(batch.get('candidates_json') or '[]')
                        if selected_indices:
                            cands_to_import = [c for c in all_cands if c.get('index') in selected_indices]
                        else:
                            cands_to_import = [c for c in all_cands if c.get('selected', True)]
                    else:
                        cands_to_import = direct_candidates[:20]

                    if not topic and scope_id:
                        try:
                            topic = app.store.project(scope_id)['name']
                        except Exception:
                            topic = '默认课题'
                    target_col = data.get('target_collection') or f"AI 补充文献/{topic or '默认课题'}"
                    acq_res = execute_zotero_import_for_candidates(cands_to_import[:20], target_col, fetch_pdf=bool(data.get('fetch_pdf', True)))

                    if batch_id:
                        with app.store.db() as db:
                            db.execute("UPDATE candidate_batches SET status='imported' WHERE id=?", (batch_id,))

                    new_keys = [it['item_key'] for it in acq_res.get('imported', []) if it.get('item_key')]
                    scope_res = {}
                    index_jid = ''
                    if scope_id and new_keys:
                        scope_res = app.store.create_or_update_scope(scope_id=scope_id, item_keys=new_keys)
                        if scope_res.get('pending_index_count', 0) > 0:
                            index_jid = app.store.enqueue(scope_id, 'index', '补充文献增量索引', 'local', options={'use_deeptutor_model': True})

                    app.store.log_trace(
                        scope_id,
                        'literature_acquire',
                        'done',
                        f"已入库 Zotero「{target_col}」共 {len(new_keys)} 篇文献" + (f"，增量索引任务已启动 ({index_jid[:8]})" if index_jid else ""),
                        {**acq_res, 'index_job_id': index_jid}
                    )
                    return self.send(200, {'ok': True, **acq_res, 'scope': scope_res, 'index_job_id': index_jid})

                if self.path == '/api/traces/log':
                    tid = app.store.log_trace(
                        data.get('scope_id', ''),
                        data.get('tool', 'mcp'),
                        data.get('status', 'info'),
                        data.get('summary', ''),
                        data.get('detail') or {}
                    )
                    return self.send(200, {'ok': True, 'id': tid})

                # DeepTutor Bridge Actions
                if self.path == '/api/deeptutor/link-kb':
                    pid = data['project']
                    kb_name = data['kb_name']
                    folder = app.store.folder(pid) / 'papers'
                    res = link_folder_to_kb(kb_name, str(folder))
                    return self.send(200, {'ok': True, 'result': res})
                if self.path == '/api/deeptutor/push-writing':
                    target = data.get('target', 'document')
                    title = data.get('title', '')
                    content = data.get('content', '')
                    card_ids = data.get('card_ids')
                    if card_ids and not content:
                        content = app.store.format_evidence_cards_citation(card_ids, style='prompt')
                    if target == 'document':
                        res = push_evidence_to_document(title, content)
                    else:
                        res = push_evidence_to_notebook(title, data.get('query', ''), content)
                    return self.send(200, {'ok': True, 'result': res})
                if self.path == '/api/deeptutor/register-mcp':
                    res = register_mcp_server()
                    return self.send(200, {'ok': True, **res})
                if self.path == '/api/deeptutor/sync-selection':
                    state = app.model_events.select(data)
                    return self.send(200, {'ok': True, **state})

                return self.send(404, {'error': '未知操作'})
            except (ValueError, KeyError, ZoteroError) as e:
                self.send(400, {'error': str(e)})

    return Handler


def main():
    cfg = config()
    app = App(cfg)
    threading.Thread(target=app.worker.run, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', cfg['port']), handler(app))
    print(f'SearchTools http://127.0.0.1:{cfg["port"]}', flush=True)
    try:
        server.serve_forever()
    finally:
        app.worker.stopped.set()
        if app.worker.current:
            app.worker.cancel(app.worker.current)
        server.server_close()


if __name__ == '__main__':
    main()
