"""Model catalog modelled on DeepTutor: connections -> models -> one active model per service.

The catalog lives in ``~/.config/searchtools/models.json`` (mode 0600). PaperQA2 settings are
generated from it for every task, so users never hand-edit LiteLLM blocks. API keys are never
returned to the browser; only a masked hint is.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

LIVE_SYNC_FILE = Path('~/.config/searchtools/deeptutor_live.json').expanduser()


def sync_deeptutor_live_selection(payload, deeptutor_dir=None):
    """Receive live model selection from DeepTutor frontend (via deeptutor_companion.js or desktop bridge),
    match it against DeepTutor's model_catalog.json profiles, update active_profile_id/active_model_id,
    and persist a live timestamped selection file."""
    dt_dir = Path(deeptutor_dir or DEEPTUTOR_DIR).expanduser()
    p = dt_dir / 'data/user/settings/model_catalog.json'
    if not p.exists():
        raise FileNotFoundError(f'未找到 DeepTutor 模型配置文件: {p}')

    data = json.loads(p.read_text(encoding='utf-8'))
    s_llm = data.get('services', {}).get('llm', {})
    profiles = s_llm.get('profiles', [])

    req_pid = str(payload.get('profile_id') or '').strip()
    req_mid = str(payload.get('model_id') or '').strip()
    req_model = str(payload.get('model') or '').strip()
    if req_model.startswith('models/'):
        req_model = req_model[len('models/'):]
    req_prof_name = str(payload.get('profile_name') or '').strip().lower()
    req_provider = str(payload.get('provider') or '').strip().lower()

    matched_prof = None
    matched_mod = None

    # 1. Exact ID match if provided
    if req_pid and req_mid:
        for prof in profiles:
            if prof.get('id') == req_pid:
                for m in prof.get('models', []):
                    if m.get('id') == req_mid:
                        matched_prof, matched_mod = prof, m
                        break

    # 2. Match by model name + profile_name/provider
    if not matched_mod and req_model:
        req_model_low = req_model.lower()
        candidates = []
        for prof in profiles:
            p_name = str(prof.get('name') or '').strip().lower()
            p_bind = str(prof.get('binding') or '').strip().lower()
            for m in prof.get('models', []):
                m_raw = str(m.get('model') or '').strip()
                m_clean = m_raw[len('models/'):] if m_raw.startswith('models/') else m_raw
                m_low = m_clean.lower()
                score = 0
                if m_low == req_model_low or m_raw.lower() == req_model_low:
                    score += 100
                elif req_model_low in m_low or m_low in req_model_low:
                    score += 40
                if score > 0:
                    if req_prof_name and (req_prof_name == p_name or req_prof_name in p_name):
                        score += 50
                    if req_provider and (req_provider == p_bind or req_provider in p_bind or p_bind in req_provider):
                        score += 40
                    if prof.get('id') == s_llm.get('active_profile_id'):
                        score += 5
                    candidates.append((score, prof, m))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            matched_prof, matched_mod = candidates[0][1], candidates[0][2]

    # 3. Match by provider / profile_name alone if model name was generic (e.g., "deepseek")
    if not matched_mod and (req_provider or req_prof_name or req_model):
        key = (req_provider or req_prof_name or req_model).lower()
        for prof in profiles:
            p_name = str(prof.get('name') or '').strip().lower()
            p_bind = str(prof.get('binding') or '').strip().lower()
            if key in (p_name, p_bind) or key in p_name or key in p_bind:
                models = prof.get('models', [])
                if models:
                    matched_prof, matched_mod = prof, models[0]
                    break

    now_ts = time.time()
    if matched_prof and matched_mod:
        new_pid = matched_prof.get('id')
        new_mid = matched_mod.get('id')
        changed = (s_llm.get('active_profile_id') != new_pid or s_llm.get('active_model_id') != new_mid)
        s_llm['active_profile_id'] = new_pid
        s_llm['active_model_id'] = new_mid
        if changed:
            try:
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            except OSError:
                pass
        live_record = {
            'updated_at': now_ts,
            'profile_id': new_pid,
            'model_id': new_mid,
            'model': matched_mod.get('model'),
            'binding': matched_prof.get('binding'),
            'profile_name': matched_prof.get('name'),
        }
        try:
            LIVE_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
            LIVE_SYNC_FILE.write_text(json.dumps(live_record, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass

    return sync_model_from_deeptutor(deeptutor_dir=deeptutor_dir)

# LiteLLM routing prefix, default endpoint and suggested models for each provider template.
PROVIDERS = {
    'ollama': {'name': '本地 Ollama', 'prefix': 'ollama/', 'base_url': 'http://127.0.0.1:11434', 'needs_key': False,
               'llm': ['qwen3:8b', 'qwen2.5:14b-instruct'], 'embedding': ['qwen3-embedding:4b'],
               'hint': '本机运行，不产生费用；与 GPU 训练共享显存。'},
    'gemini': {'name': 'Google Gemini', 'prefix': 'gemini/', 'base_url': '', 'needs_key': True,
               'llm': ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.0-flash'],
               'embedding': ['gemini-embedding-001', 'text-embedding-004'],
               'hint': '在 Google AI Studio 创建 API Key；未绑定结算账户的项目走免费额度。'},
    'deepseek': {'name': 'DeepSeek', 'prefix': 'deepseek/', 'base_url': 'https://api.deepseek.com', 'needs_key': True,
                 'llm': ['deepseek-chat', 'deepseek-reasoner'], 'embedding': [],
                 'hint': 'DeepSeek 不提供 Embedding，请配合本地或其他服务商的向量模型。'},
    'openai': {'name': 'OpenAI', 'prefix': 'openai/', 'base_url': '', 'needs_key': True,
               'llm': ['gpt-4.1-mini', 'gpt-4.1', 'gpt-4o-mini'], 'embedding': ['text-embedding-3-small', 'text-embedding-3-large'],
               'hint': ''},
    'anthropic': {'name': 'Anthropic', 'prefix': 'anthropic/', 'base_url': '', 'needs_key': True,
                  'llm': ['claude-sonnet-4-5', 'claude-haiku-4-5'], 'embedding': [], 'hint': 'Anthropic 不提供 Embedding。'},
    'openrouter': {'name': 'OpenRouter', 'prefix': 'openrouter/', 'base_url': '', 'needs_key': True,
                   'llm': ['google/gemini-2.5-flash', 'deepseek/deepseek-chat'], 'embedding': [], 'hint': ''},
    'openai_compatible': {'name': 'OpenAI 兼容接口', 'prefix': 'openai/', 'base_url': 'https://api.siliconflow.cn/v1', 'needs_key': True,
                          'llm': ['Qwen/Qwen3-32B'], 'embedding': ['Qwen/Qwen3-Embedding-4B'],
                          'hint': '硅基流动、阿里云百炼、vLLM 等提供 /v1/chat/completions 的服务。'},
}
DEEPTUTOR_DIR = Path(os.environ.get('DEEPTUTOR_DIR', '/home/qyw/DeepTutor'))


def sync_model_from_deeptutor(deeptutor_dir=None):
    """Inspect DeepTutor's data/user/settings/model_catalog.json and chat_history.db to
    return a synthesized catalog data structure that PaperQA2 / LiteLLM immediately executes."""
    dt_dir = Path(deeptutor_dir or DEEPTUTOR_DIR).expanduser()
    p = dt_dir / 'data/user/settings/model_catalog.json'
    if not p.exists():
        raise FileNotFoundError(f'未找到 DeepTutor 模型配置文件: {p}')
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        raise ValueError(f'读取 DeepTutor 模型配置失败: {e}')

    conns_by_provider = {c.get('provider'): c for c in data.get('connections', [])}
    conns_by_id = {c.get('id'): c for c in data.get('connections', [])}

    # 1. Resolve LLM:
    # DeepTutor stores the global active model in data/user/settings/model_catalog.json (services.llm).
    # We also check all chat_history.db files (root + per-workspace) and LIVE_SYNC_FILE,
    # picking whichever model selection was updated most recently.
    s_llm = data.get('services', {}).get('llm', {})
    target_pid = s_llm.get('active_profile_id')
    target_mid = s_llm.get('active_model_id')

    latest_ts = p.stat().st_mtime if p.exists() else 0

    # Check all chat_history.db files (root data/user/chat_history.db and workspaces/ws_*/.deeptutor/data/user/chat_history.db)
    db_files = [dt_dir / 'data/user/chat_history.db']
    ws_root = dt_dir / 'data/user/workspaces'
    if ws_root.is_dir():
        db_files.extend(ws_root.glob('*/.deeptutor/data/user/chat_history.db'))

    import sqlite3
    for db_file in db_files:
        if not db_file.exists():
            continue
        try:
            c = sqlite3.connect(str(db_file), timeout=2)
            rows = c.execute(
                "SELECT updated_at, preferences_json FROM sessions WHERE deleted_at IS NULL AND preferences_json IS NOT NULL ORDER BY updated_at DESC LIMIT 5"
            ).fetchall()
            c.close()
            for r_ts, r_prefs in rows:
                session_updated = float(r_ts or 0)
                if session_updated > latest_ts and r_prefs:
                    prefs = json.loads(r_prefs)
                    llm_sel = prefs.get('llm_selection') or {}
                    if llm_sel.get('profile_id') and llm_sel.get('model_id'):
                        latest_ts = session_updated
                        target_pid = llm_sel['profile_id']
                        target_mid = llm_sel['model_id']
                        break
        except Exception:
            pass

    # Check LIVE_SYNC_FILE reported by DeepTutor UI / companion script (only when using default DEEPTUTOR_DIR)
    if deeptutor_dir is None and LIVE_SYNC_FILE.exists():
        try:
            live = json.loads(LIVE_SYNC_FILE.read_text(encoding='utf-8'))
            live_ts = float(live.get('updated_at') or 0)
            if live_ts >= latest_ts - 1.0 and live.get('profile_id') and live.get('model_id'):
                latest_ts = live_ts
                target_pid = live['profile_id']
                target_mid = live['model_id']
        except Exception:
            pass

    prof = next((x for x in s_llm.get('profiles', []) if x.get('id') == target_pid), None)
    m = next((x for x in (prof.get('models') if prof else []) if x.get('id') == target_mid), None)

    # Fallback to catalog defaults if target profile or model was not found
    if not prof or not m:
        target_pid = s_llm.get('active_profile_id')
        target_mid = s_llm.get('active_model_id')
        prof = next((x for x in s_llm.get('profiles', []) if x.get('id') == target_pid), None)
        m = next((x for x in (prof.get('models') if prof else []) if x.get('id') == target_mid), None)
    if not prof and s_llm.get('profiles'):
        prof = next((x for x in s_llm['profiles'] if x.get('models')), s_llm['profiles'][0])
    if prof and not m and prof.get('models'):
        m = prof['models'][0]

    binding = (prof.get('binding') if prof else 'ollama') or 'ollama'
    raw_model = (m.get('model') if m else 'qwen3:8b') or 'qwen3:8b'
    # Strip any internal 'models/' prefix for standard LiteLLM routing
    model_name = raw_model[len('models/'):] if raw_model.startswith('models/') else raw_model

    base_url = (prof.get('base_url') if prof else '') or ''
    api_key = (prof.get('api_key') if prof else '') or ''

    # Check provider_ref in model
    if not api_key and m and m.get('provider_ref'):
        conn_id = m['provider_ref'].get('connection_id')
        if conn_id in conns_by_id:
            api_key = conns_by_id[conn_id].get('api_key', '')
            if not base_url:
                base_url = conns_by_id[conn_id].get('base_url', '')

    if not api_key and binding in conns_by_provider:
        api_key = conns_by_provider[binding].get('api_key', '')
    if not base_url and binding in conns_by_provider:
        base_url = conns_by_provider[binding].get('base_url', '')

    provider_map = {
        'ollama': 'ollama',
        'gemini': 'gemini',
        'deepseek': 'deepseek',
        'openai': 'openai',
        'anthropic': 'anthropic',
        'openrouter': 'openrouter',
    }
    llm_provider = provider_map.get(binding, 'openai')

    # If active profile has no direct API key (e.g. openai_codex OAuth), fallback to available cloud key or local ollama
    if PROVIDERS.get(llm_provider, {}).get('needs_key') and not api_key:
        if 'gemini' in conns_by_provider and conns_by_provider['gemini'].get('api_key'):
            llm_provider = 'gemini'
            model_name = 'gemini-2.5-flash'
            api_key = conns_by_provider['gemini'].get('api_key')
            base_url = ''
            binding = 'gemini'
        elif 'deepseek' in conns_by_provider and conns_by_provider['deepseek'].get('api_key'):
            llm_provider = 'deepseek'
            model_name = 'deepseek-chat'
            api_key = conns_by_provider['deepseek'].get('api_key')
            base_url = 'https://api.deepseek.com'
            binding = 'deepseek'
        else:
            llm_provider = 'ollama'
            model_name = 'qwen3:8b'
            base_url = 'http://127.0.0.1:11434'
            api_key = ''
            binding = 'ollama'

    # Resolve Embedding
    s_emb = data.get('services', {}).get('embedding', {})
    e_pid = s_emb.get('active_profile_id')
    e_mid = s_emb.get('active_model_id')
    e_prof = next((x for x in s_emb.get('profiles', []) if x.get('id') == e_pid), None)
    e_m = next((x for x in (e_prof.get('models') if e_prof else []) if x.get('id') == e_mid), None)

    e_binding = (e_prof.get('binding') if e_prof else 'ollama') or 'ollama'
    e_raw_model = (e_m.get('model') if e_m else 'qwen3-embedding:4b') or 'qwen3-embedding:4b'
    e_model = e_raw_model[len('models/'):] if e_raw_model.startswith('models/') else e_raw_model
    e_base = (e_prof.get('base_url') if e_prof else '') or ''
    e_key = (e_prof.get('api_key') if e_prof else '') or ''

    if not e_key and e_m and e_m.get('provider_ref'):
        conn_id = e_m['provider_ref'].get('connection_id')
        if conn_id in conns_by_id:
            e_key = conns_by_id[conn_id].get('api_key', '')
            if not e_base:
                e_base = conns_by_id[conn_id].get('base_url', '')

    if not e_key and e_binding in conns_by_provider:
        e_key = conns_by_provider[e_binding].get('api_key', '')
    if not e_base and e_binding in conns_by_provider:
        e_base = conns_by_provider[e_binding].get('base_url', '')

    emb_provider = provider_map.get(e_binding, 'ollama')
    if emb_provider == 'ollama' and not e_base:
        e_base = 'http://127.0.0.1:11434'
    if e_base.endswith('/api/embed'):
        e_base = e_base[:-len('/api/embed')]

    cid_llm = 'conn-dt-synced-llm'
    cid_emb = 'conn-dt-synced-emb'
    conns = [
        {
            'id': cid_llm,
            'name': f'DeepTutor ({binding.upper()})',
            'provider': llm_provider,
            'base_url': base_url,
            'api_key': api_key,
            'models': [model_name]
        },
        {
            'id': cid_emb,
            'name': f'DeepTutor Embedding ({e_binding.upper()})',
            'provider': emb_provider,
            'base_url': e_base,
            'api_key': e_key,
            'models': [e_model]
        }
    ]
    return {
        'version': 1,
        'mode': 'deeptutor',
        'connections': conns,
        'services': {
            'llm': {'connection': cid_llm, 'model': model_name},
            'summary_llm': {'connection': cid_llm, 'model': model_name},
            'embedding': {'connection': cid_emb, 'model': e_model},
            'enrichment_llm': {'connection': '', 'model': ''}
        },
        'params': dict(DEFAULT_PARAMS),
        'index': dict(DEFAULT_INDEX),
        'deeptutor_info': {
            'llm_binding': binding,
            'llm_model': model_name,
            'llm_provider': llm_provider,
            'llm_base_url': base_url,
            'emb_binding': e_binding,
            'emb_model': e_model,
            'emb_provider': emb_provider,
            'emb_base_url': e_base,
        }
    }
SERVICES = {
    'llm': '回答生成 LLM',
    'summary_llm': '证据摘要 LLM',
    'embedding': '向量 Embedding',
    'enrichment_llm': '解析增强 LLM',
}
OPTIONAL_SERVICES = ('summary_llm', 'enrichment_llm')  # empty means "same as answer LLM"
PRESETS = {
    'fast': {'label': '快速', 'evidence_k': 5, 'answer_max_sources': 3,
             'evidence_summary_length': 'about 60 words', 'answer_length': 'about 150 words'},
    'balanced': {'label': '平衡', 'evidence_k': 10, 'answer_max_sources': 5,
                 'evidence_summary_length': 'about 100 words', 'answer_length': 'about 200 words, but can be longer'},
    'deep': {'label': '深入', 'evidence_k': 20, 'answer_max_sources': 8,
             'evidence_summary_length': 'about 120 words', 'answer_length': 'about 500 words, with a detailed comparison'},
}
DEFAULT_INDEX = {'chunk_chars': 5000, 'overlap': 250, 'multimodal': False, 'use_doc_details': False}
DEFAULT_PARAMS = {'temperature': 0.0, 'num_ctx': 8192, 'think': False, 'preset': 'balanced',
                  'evidence_relevance_score_cutoff': 1, 'answer_language': 'auto'}


def default_catalog():
    oid = 'conn-ollama'
    return {'version': 1,
            'mode': 'deeptutor',
            'connections': [{'id': oid, 'name': '本地 Ollama', 'provider': 'ollama',
                             'base_url': PROVIDERS['ollama']['base_url'], 'api_key': '',
                             'models': ['qwen3:8b', 'qwen3-embedding:4b']}],
            'services': {'llm': {'connection': oid, 'model': 'qwen3:8b'},
                         'summary_llm': {'connection': '', 'model': ''},
                         'embedding': {'connection': oid, 'model': 'qwen3-embedding:4b'},
                         'enrichment_llm': {'connection': '', 'model': ''}},
            'params': dict(DEFAULT_PARAMS), 'index': dict(DEFAULT_INDEX)}


def _split(name):
    """'ollama/qwen3:8b' -> ('ollama', 'qwen3:8b')."""
    for pid, p in PROVIDERS.items():
        if pid != 'openai_compatible' and name.startswith(p['prefix']):
            return pid, name[len(p['prefix']):]
    return 'openai', name


def migrate_paperqa(path):
    """Turn a legacy hand-written paperqa.json into a catalog so existing setups keep working."""
    cat = default_catalog()
    try:
        old = json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return cat
    conns = {}

    def conn_for(model_name, params):
        pid, model = _split(model_name)
        base = params.get('api_base') or PROVIDERS[pid]['base_url']
        key = (pid, base, params.get('api_key', ''))
        if key not in conns:
            conns[key] = {'id': 'conn-' + uuid.uuid4().hex[:8], 'name': PROVIDERS[pid]['name'], 'provider': pid,
                          'base_url': base, 'api_key': params.get('api_key', ''), 'models': []}
        c = conns[key]
        if model not in c['models']:
            c['models'].append(model)
        return {'connection': c['id'], 'model': model}

    def params_of(block):
        try:
            return block['model_list'][0]['litellm_params']
        except (KeyError, IndexError, TypeError):
            return {}

    if old.get('llm'):
        p = params_of(old.get('llm_config'))
        cat['services']['llm'] = conn_for(old['llm'], p)
        for k in ('num_ctx', 'think', 'temperature'):
            if k in p:
                cat['params'][k] = p[k]
    if old.get('summary_llm') and old.get('summary_llm') != old.get('llm'):
        cat['services']['summary_llm'] = conn_for(old['summary_llm'], params_of(old.get('summary_llm_config')))
    if old.get('embedding'):
        cat['services']['embedding'] = conn_for(old['embedding'], (old.get('embedding_config') or {}).get('kwargs', {}))
    cat['connections'] = list(conns.values()) or cat['connections']
    parsing = old.get('parsing') or {}
    cat['index']['multimodal'] = bool(parsing.get('multimodal', False))
    return cat


class Catalog:
    def __init__(self, path, legacy=None):
        self.path = Path(path).expanduser()
        self.legacy = legacy

    def load(self):
        if self.path.exists():
            data = json.loads(self.path.read_text())
        else:
            data = migrate_paperqa(self.legacy) if self.legacy and Path(self.legacy).exists() else default_catalog()
            self.save(data)
        base = default_catalog()
        for key in ('params', 'index'):
            data[key] = {**base[key], **(data.get(key) or {})}
        data.setdefault('services', {})
        for s in SERVICES:
            data['services'].setdefault(s, {'connection': '', 'model': ''})
        data.setdefault('connections', [])
        data['mode'] = data.get('mode', 'deeptutor')
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    # ---- browser-facing views -------------------------------------------------
    def public(self):
        data = copy.deepcopy(self.load())
        mode = data.get('mode', 'deeptutor')
        dt_sync = None
        try:
            dt_data = sync_model_from_deeptutor()
            dt_sync = {
                'available': True,
                'info': dt_data.get('deeptutor_info', {}),
            }
        except Exception as e:
            dt_sync = {
                'available': False,
                'error': str(e),
            }
        data['deeptutor_sync'] = dt_sync
        data['mode'] = mode

        # If mode is 'deeptutor' and sync info is available, explicitly reflect the active DeepTutor model
        if mode == 'deeptutor' and dt_sync and dt_sync.get('available'):
            info = dt_sync['info']
            llm_prov_name = PROVIDERS.get(info.get('llm_provider'), {}).get('name', str(info.get('llm_provider', 'Cloud')).upper())
            emb_prov_name = PROVIDERS.get(info.get('emb_provider'), {}).get('name', str(info.get('emb_provider', 'Embed')).upper())
            dt_conn_llm = {
                'id': 'conn-dt-synced-llm',
                'name': f'DeepTutor ({llm_prov_name})',
                'provider': info.get('llm_provider', 'gemini'),
                'base_url': info.get('llm_base_url', ''),
                'api_key_set': True,
                'api_key_hint': '跟随 DeepTutor 授权',
                'models': [info.get('llm_model', '')],
                'is_deeptutor_sync': True
            }
            dt_conn_emb = {
                'id': 'conn-dt-synced-emb',
                'name': f'DeepTutor ({emb_prov_name})',
                'provider': info.get('emb_provider', 'ollama'),
                'base_url': info.get('emb_base_url', ''),
                'api_key_set': True,
                'api_key_hint': '跟随 DeepTutor 授权',
                'models': [info.get('emb_model', '')],
                'is_deeptutor_sync': True
            }
            data['connections'] = [dt_conn_llm, dt_conn_emb] + [c for c in data['connections'] if not c.get('id', '').startswith('conn-dt-synced')]
            data['services']['llm'] = {'connection': 'conn-dt-synced-llm', 'model': info.get('llm_model', '')}
            data['services']['summary_llm'] = {'connection': 'conn-dt-synced-llm', 'model': info.get('llm_model', '')}
            data['services']['embedding'] = {'connection': 'conn-dt-synced-emb', 'model': info.get('emb_model', '')}

        for c in data['connections']:
            key = c.pop('api_key', '') or ''
            c['api_key_set'] = bool(key) or c.get('api_key_set', False)
            c['api_key_hint'] = c.get('api_key_hint') or (('…' + key[-4:]) if len(key) >= 8 else ('已设置' if key else ''))
        data['providers'] = PROVIDERS
        data['service_labels'] = SERVICES
        data['presets'] = {k: v['label'] for k, v in PRESETS.items()}
        data['index_name'] = index_name(data)
        return data

    def update(self, body):
        data = self.load()
        old = {c['id']: c for c in data['connections']}
        if 'mode' in body:
            data['mode'] = 'deeptutor' if body['mode'] == 'deeptutor' else 'custom'
        if 'connections' in body:
            conns = []
            for c in body['connections']:
                provider = c.get('provider')
                if provider not in PROVIDERS:
                    raise ValueError('未知的服务商：' + str(provider))
                cid = str(c.get('id') or 'conn-' + uuid.uuid4().hex[:8])[:40]
                if not re.fullmatch(r'[\w-]+', cid):
                    raise ValueError('连接 ID 无效')
                base = str(c.get('base_url') or '').strip()
                if base and not re.match(r'^https?://', base):
                    raise ValueError('Base URL 需以 http:// 或 https:// 开头')
                key = c.get('api_key')
                if key is None:  # omitted -> keep the stored secret
                    key = old.get(cid, {}).get('api_key', '')
                models = [str(m).strip()[:200] for m in c.get('models') or [] if str(m).strip()]
                conns.append({'id': cid, 'name': str(c.get('name') or PROVIDERS[provider]['name'])[:80],
                              'provider': provider, 'base_url': base, 'api_key': str(key).strip(),
                              'models': list(dict.fromkeys(models))})
            data['connections'] = conns
        ids = {c['id'] for c in data['connections']}
        if 'services' in body:
            for s, sel in body['services'].items():
                if s not in SERVICES:
                    continue
                sel = {'connection': str(sel.get('connection') or ''), 'model': str(sel.get('model') or '').strip()}
                if sel['connection'] and sel['connection'] not in ids:
                    raise ValueError(f'{SERVICES[s]} 引用了不存在的连接')
                data['services'][s] = sel
        for s in ('llm', 'embedding'):
            sel = data['services'].get(s) or {}
            if data.get('mode') != 'deeptutor' and (sel.get('connection') not in ids or not sel.get('model')):
                raise ValueError(f'请为「{SERVICES[s]}」选择连接和模型')
        if 'params' in body:
            p = body['params']
            data['params'].update({
                'temperature': _num(p.get('temperature', data['params']['temperature']), 0, 2, float),
                'num_ctx': _num(p.get('num_ctx', data['params']['num_ctx']), 1024, 262144, int),
                'think': bool(p.get('think', data['params']['think'])),
                'preset': p.get('preset') if p.get('preset') in PRESETS else data['params']['preset'],
                'evidence_relevance_score_cutoff': _num(p.get('evidence_relevance_score_cutoff', data['params']['evidence_relevance_score_cutoff']), 0, 10, int),
                'answer_language': p.get('answer_language') if p.get('answer_language') in ('auto', 'zh', 'en') else data['params']['answer_language'],
            })
        if 'index' in body:
            i = body['index']
            chunk = _num(i.get('chunk_chars', data['index']['chunk_chars']), 500, 20000, int)
            data['index'].update({'chunk_chars': chunk,
                                  'overlap': _num(i.get('overlap', data['index']['overlap']), 0, chunk // 2, int),
                                  'multimodal': bool(i.get('multimodal', data['index']['multimodal'])),
                                  'use_doc_details': bool(i.get('use_doc_details', data['index']['use_doc_details']))})
        self.save(data)
        return self.public()

    def connection(self, cid, data=None):
        data = data or self.load()
        for c in data['connections']:
            if c['id'] == cid:
                return c
        raise ValueError('连接不存在')


def _num(value, lo, hi, kind):
    try:
        value = kind(value)
    except (TypeError, ValueError):
        raise ValueError('参数格式不正确') from None
    if not lo <= value <= hi:
        raise ValueError(f'参数需在 {lo}–{hi} 之间')
    return value


def litellm_name(conn, model):
    prefix = PROVIDERS[conn['provider']]['prefix']
    return model if model.startswith(prefix) else prefix + model


def litellm_params(conn, model, params, service='llm'):
    out = {'model': litellm_name(conn, model)}
    if conn.get('base_url'):
        out['api_base'] = conn['base_url']
    if conn.get('api_key'):
        out['api_key'] = conn['api_key']
    if service != 'embedding':
        out['temperature'] = params.get('temperature', 0.0)
        if conn['provider'] == 'ollama':
            out['num_ctx'] = params.get('num_ctx', 8192)
            out['think'] = bool(params.get('think', False))
        elif conn['provider'] == 'deepseek' and not params.get('think', False):
            # Disable DeepSeek thinking mode unless explicitly enabled so multi-turn
            # tool calls (e.g. PaperQA2 select_tool) don't fail with 400 missing reasoning_content.
            out['extra_body'] = {'thinking': {'type': 'disabled'}}
    return out


def resolve(data, service):
    sel = data['services'].get(service) or {}
    if service in OPTIONAL_SERVICES and not (sel.get('connection') and sel.get('model')):
        sel = data['services']['llm']
    for c in data['connections']:
        if c['id'] == sel.get('connection'):
            return c, sel['model']
    raise ValueError(f'「{SERVICES[service]}」未配置有效的连接')


def index_name(data):
    """Index identity follows everything that changes chunk embeddings, so switching the
    embedding model never mixes vectors from two models in one index."""
    conn, model = resolve(data, 'embedding')
    i = data['index']
    raw = json.dumps([litellm_name(conn, model), i['chunk_chars'], i['overlap'], i['multimodal'], i['use_doc_details']])
    return 'pqa-' + hashlib.sha256(raw.encode()).hexdigest()[:10]


def _llm_block(conn, model, params, service='llm'):
    name = litellm_name(conn, model)
    return name, {'model_list': [{'model_name': name, 'litellm_params': litellm_params(conn, model, params, service)}]}


def _max_concurrent_for(llm_conn, sum_conn):
    if all(c['provider'] == 'ollama' for c in (llm_conn, sum_conn)):
        return 1
    if any(c['provider'] == 'gemini' for c in (llm_conn, sum_conn)):
        return 2
    return 4


def build_paperqa_settings(data, options=None):
    """Return a dict accepted by ``paperqa.Settings(**dict)``; options are per-task overrides."""
    options = options or {}
    mode = options.get('mode') or data.get('mode', 'deeptutor')
    if mode == 'deeptutor' or options.get('use_deeptutor_model') or data.get('use_deeptutor_model'):
        try:
            dt_data = sync_model_from_deeptutor()
            dt_data['params'] = {**data.get('params', {}), **(dt_data.get('params') or {})}
            dt_data['index'] = {**data.get('index', {}), **(dt_data.get('index') or {})}
            data = dt_data
        except Exception:
            pass
    params = data['params']
    preset = PRESETS.get(options.get('preset') or params['preset'], PRESETS['balanced'])
    llm_conn, llm_model = resolve(data, 'llm')
    sum_conn, sum_model = resolve(data, 'summary_llm')
    enr_conn, enr_model = resolve(data, 'enrichment_llm')
    emb_conn, emb_model = resolve(data, 'embedding')
    llm, llm_cfg = _llm_block(llm_conn, llm_model, params)
    summ, summ_cfg = _llm_block(sum_conn, sum_model, params)
    enr, enr_cfg = _llm_block(enr_conn, enr_model, params)
    emb_kwargs = {k: v for k, v in litellm_params(emb_conn, emb_model, params, 'embedding').items() if k != 'model'}
    max_conc = _max_concurrent_for(llm_conn, sum_conn)
    answer_length = options.get('answer_length') or preset['answer_length']
    lang = options.get('answer_language') or params.get('answer_language', 'auto')
    if lang == 'zh':
        answer_length += '; write the answer in Simplified Chinese'
    elif lang == 'en':
        answer_length += '; write the answer in English'
    index = data['index']
    return {
        'llm': llm, 'llm_config': llm_cfg,
        'summary_llm': summ, 'summary_llm_config': summ_cfg,
        'embedding': litellm_name(emb_conn, emb_model), 'embedding_config': {'kwargs': emb_kwargs},
        'temperature': params['temperature'],
        'parsing': {'multimodal': index['multimodal'], 'use_doc_details': index['use_doc_details'],
                    'reader_config': {'chunk_chars': index['chunk_chars'], 'overlap': index['overlap']},
                    'disable_doc_valid_check': True,
                    'enrichment_llm': enr, 'enrichment_llm_config': enr_cfg},
        'agent': {'agent_llm': llm, 'agent_llm_config': llm_cfg, 'agent_type': 'fake',
                  'index': {'name': index_name(data)}},
        'answer': {'evidence_k': _num(options.get('evidence_k', preset['evidence_k']), 1, 50, int),
                   'answer_max_sources': _num(options.get('answer_max_sources', preset['answer_max_sources']), 1, 20, int),
                   'evidence_summary_length': options.get('evidence_summary_length') or preset['evidence_summary_length'],
                   'answer_length': answer_length,
                   'evidence_relevance_score_cutoff': _num(options.get('evidence_relevance_score_cutoff', params['evidence_relevance_score_cutoff']), 0, 10, int),
                   'max_concurrent_requests': max_conc},
    }


def redact(obj):
    """Deep copy with every api_key replaced, for task records on disk."""
    if isinstance(obj, dict):
        return {k: ('***' if k == 'api_key' and v else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def test_payload(data, conn_body, service):
    """Build a connection-test request; conn_body may carry an unsaved key from the form."""
    cid = conn_body.get('id')
    if (cid and cid.startswith('conn-dt-synced')) or conn_body.get('is_deeptutor_sync'):
        dt = sync_model_from_deeptutor()
        if dt:
            dt_conns = dt.get('connections', [])
            svc_key = 'embedding' if service == 'embedding' else 'llm'
            target_cid = dt.get('services', {}).get(svc_key, {}).get('connection')
            target_conn = next((c for c in dt_conns if c['id'] == target_cid), {})
            conn = {
                'provider': target_conn.get('provider') or 'gemini',
                'base_url': target_conn.get('base_url', ''),
                'api_key': target_conn.get('api_key', ''),
            }
            target_model = dt.get('services', {}).get(svc_key, {}).get('model', '')
            model = str(conn_body.get('model') or target_model or '').strip()
            kind = 'embedding' if service == 'embedding' else 'llm'
            return {'kind': 'test', 'service': kind, 'params': litellm_params(conn, model, data.get('params', {}), kind)}

    stored = next((c for c in data['connections'] if c['id'] == cid), {})
    provider = conn_body.get('provider') or stored.get('provider')
    if provider not in PROVIDERS:
        raise ValueError('未知的服务商')
    conn = {'provider': provider, 'base_url': str(conn_body.get('base_url') if conn_body.get('base_url') is not None else stored.get('base_url', '')).strip(),
            'api_key': conn_body.get('api_key') if conn_body.get('api_key') is not None else stored.get('api_key', '')}
    model = str(conn_body.get('model') or '').strip()
    if not model:
        raise ValueError('请先填写要测试的模型')
    if PROVIDERS[provider]['needs_key'] and not conn['api_key']:
        dt = sync_model_from_deeptutor()
        if dt:
            dt_conns = dt.get('connections', [])
            match = next((c for c in dt_conns if c.get('provider') == provider and c.get('api_key')), None)
            if match:
                conn['api_key'] = match['api_key']
    if PROVIDERS[provider]['needs_key'] and not conn['api_key']:
        raise ValueError('该服务商需要 API Key')
    kind = 'embedding' if service == 'embedding' else 'llm'
    return {'kind': 'test', 'service': kind, 'params': litellm_params(conn, model, data.get('params', {}), kind)}
