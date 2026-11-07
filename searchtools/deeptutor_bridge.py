"""DeepTutor Bridge for SearchTools.

Connects SearchTools (Zotero & PaperQA2) to DeepTutor:
1. Model sync: inherits DeepTutor active LLM and embedding configurations.
2. MCP server registration: registers SearchTools MCP server into DeepTutor's mcp.json.
3. Knowledge Base syncing: links project papers folder into DeepTutor RAG knowledge bases.
4. Co-Writer / Notebook pushing: pushes verified evidence cards and literature notes directly
   into DeepTutor documents and notebooks.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import uuid

DEEPTUTOR_DIR = Path(os.environ.get('DEEPTUTOR_DIR', '/home/qyw/DeepTutor')).expanduser()
DEEPTUTOR_BACKEND = os.environ.get('DEEPTUTOR_BACKEND', 'http://127.0.0.1:8001')
DEEPTUTOR_FRONTEND = os.environ.get('DEEPTUTOR_FRONTEND', 'http://127.0.0.1:3782')


def _http_request(url, method='GET', payload=None, timeout=6):
    req = urllib.request.Request(url, method=method)
    req.add_header('Accept', 'application/json')
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
        body = resp.read().decode('utf-8')
        return json.loads(body) if body.strip() else {}


def get_deeptutor_status(backend_url=DEEPTUTOR_BACKEND, dt_dir=DEEPTUTOR_DIR):
    """Check whether DeepTutor backend is alive and get active model information."""
    alive = False
    try:
        res = _http_request(f"{backend_url.rstrip('/')}/health/live", timeout=2)
        alive = res.get('status') == 'alive'
    except Exception:
        alive = False

    model_sync_info = {}
    try:
        from .models import sync_model_from_deeptutor
        dt_cat = sync_model_from_deeptutor(dt_dir)
        model_sync_info = dt_cat.get('deeptutor_info', {})
    except Exception as e:
        model_sync_info = {'error': str(e)}

    mcp_registered = False
    mcp_file = Path(dt_dir) / 'data/user/settings/mcp.json'
    if mcp_file.exists():
        try:
            mcp_data = json.loads(mcp_file.read_text(encoding='utf-8'))
            mcp_registered = 'paperqa2-zotero' in mcp_data.get('servers', {})
        except Exception:
            pass

    return {
        'alive': alive,
        'backend': backend_url,
        'frontend': DEEPTUTOR_FRONTEND,
        'model_sync': model_sync_info,
        'mcp_registered': mcp_registered,
    }


def register_mcp_server(dt_dir=DEEPTUTOR_DIR, backend_url=DEEPTUTOR_BACKEND):
    """Register PaperQA2 + Zotero MCP server into DeepTutor settings/mcp.json and notify backend."""
    dt_path = Path(dt_dir).expanduser()
    settings_dir = dt_path / 'data/user/settings'
    settings_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = settings_dir / 'mcp.json'

    current_data = {'servers': {}}
    if mcp_file.exists():
        try:
            current_data = json.loads(mcp_file.read_text(encoding='utf-8'))
            current_data.setdefault('servers', {})
        except Exception:
            current_data = {'servers': {}}

    python_bin = str(dt_path / '.venv/bin/python')
    if not Path(python_bin).exists():
        import sys
        python_bin = sys.executable

    cwd_path = str(Path(__file__).resolve().parents[1])

    server_entry = {
        'type': 'stdio',
        'command': python_bin,
        'args': ['-m', 'searchtools.mcp_server'],
        'cwd': cwd_path,
        'env': {},
        'url': '',
        'headers': {},
        'tool_timeout': 120,
        'enabled_tools': ['*'],
        'disabled_tools': [],
        'enabled': True,
        'allow_private_network': False,
        'auth': '',
        'catalog_entry': ''
    }

    current_data['servers']['paperqa2-zotero'] = server_entry
    tmp = mcp_file.with_suffix('.tmp')
    tmp.write_text(json.dumps(current_data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(mcp_file)

    # If backend is running, notify it to reload
    notified = False
    try:
        _http_request(f"{backend_url.rstrip('/')}/api/settings/mcp", method='GET', timeout=3)
        notified = True
    except Exception:
        pass

    return {'status': 'registered', 'path': str(mcp_file), 'notified': notified}


def list_knowledge_bases(backend_url=DEEPTUTOR_BACKEND):
    """Retrieve list of existing knowledge bases from DeepTutor."""
    try:
        res = _http_request(f"{backend_url.rstrip('/')}/api/knowledge-bases", timeout=4)
        if isinstance(res, list):
            return res
        return res.get('knowledge_bases') or res.get('items') or []
    except Exception as e:
        return []


def link_folder_to_kb(kb_name, folder_path, backend_url=DEEPTUTOR_BACKEND):
    """Link a local folder (such as SearchTools project papers) to a DeepTutor Knowledge Base."""
    folder = str(Path(folder_path).resolve())
    url = f"{backend_url.rstrip('/')}/api/knowledge-bases/{urllib.parse.quote(kb_name)}/link-folder"
    return _http_request(url, method='POST', payload={'folder_path': folder})


def push_evidence_to_document(doc_title, content, backend_url=DEEPTUTOR_BACKEND):
    """Create a draft document in DeepTutor Co-Writer containing formatted evidence cards."""
    url = f"{backend_url.rstrip('/')}/api/documents"
    payload = {
        'title': doc_title or '学术论文证据与素材草稿',
        'content': content
    }
    return _http_request(url, method='POST', payload=payload)


def push_evidence_to_notebook(title, query, output, notebook_ids=None, backend_url=DEEPTUTOR_BACKEND):
    """Add an evidence record to DeepTutor notebooks."""
    url = f"{backend_url.rstrip('/')}/api/notebooks/actions/add-record"
    payload = {
        'notebook_ids': notebook_ids or ['default'],
        'record_type': 'co_writer',
        'title': title or 'PaperQA2 查证记录',
        'user_query': query or '文献证据检索',
        'output': output,
        'summary': 'PaperQA2 核验证据卡片'
    }
    return _http_request(url, method='POST', payload=payload)
