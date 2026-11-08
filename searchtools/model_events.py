"""Acknowledged, session-scoped model events; no model/file polling."""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time
import uuid


def state_path():
    from .models import LIVE_SYNC_FILE
    return LIVE_SYNC_FILE.with_name('model_sync.json')


def read_selection(client_id=None, revision=None):
    path = state_path()
    if not path.exists():
        if client_id:
            raise ValueError('当前 DeepTutor 页面尚未确认模型同步')
        return None
    data = json.loads(path.read_text())
    client_id = client_id or data.get('active_client_id')
    state = data.get('clients', {}).get(client_id)
    if not state:
        raise ValueError('当前 DeepTutor 页面尚未确认模型同步')
    if revision is not None and state['revision'] != revision:
        raise ValueError('模型已切换，请等待新的同步确认后提交任务')
    return deepcopy(state)


class ModelEventHub:
    def __init__(self, cfg=None):
        self.cfg = cfg
        self.condition = threading.Condition(threading.RLock())
        self.generation = 0

    def current(self, client_id=None):
        return read_selection(client_id) or {'status': 'unbound', 'model': {}}

    def select(self, payload):
        from .models import sync_model_from_deeptutor
        client = str(payload.get('client_id') or '').strip()
        sequence = payload.get('sequence')
        if not client or len(client) > 200 or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError('模型同步事件缺少合法的页面标识或序号')
        selection = payload.get('selection')
        if not isinstance(selection, dict) or not selection.get('profile_id') or not selection.get('model_id'):
            raise ValueError('模型同步事件必须指定当前会话的模型 ID')
        selection = {k: selection[k] for k in ('profile_id','model_id','reasoning_effort') if selection.get(k)}
        # Resolve before acknowledgment; provider/auth failures never become a different model.
        resolved = sync_model_from_deeptutor(selection=selection)
        model = resolved['deeptutor_info']
        with self.condition:
            path = state_path()
            data = json.loads(path.read_text()) if path.exists() else {'clients': {}}
            previous = data['clients'].get(client)
            if previous and previous['sequence'] >= sequence:
                return dict(previous, stale=True)
            if self.cfg and self.cfg.get('paperqa_settings'):
                from .core import catalog_for
                from .models import build_paperqa_settings
                catalog = catalog_for(self.cfg).load()
                if catalog.get('mode', 'deeptutor') == 'deeptutor':
                    settings = build_paperqa_settings(catalog, {'deeptutor_selection': selection})
                    destination = Path(self.cfg['paperqa_settings']).expanduser()
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'w') as stream:
                        json.dump(settings, stream, ensure_ascii=False)
                    os.replace(temporary, destination)
            state = {'status': 'ready', 'client_id': client, 'session_id': str(payload.get('session_id') or ''),
                     'sequence': sequence, 'selection': selection, 'revision': uuid.uuid4().hex,
                     'received_at': time.time(), 'model': model}
            data['clients'][client] = state
            # Keep a bounded history, while retaining independent current-page contexts.
            if len(data['clients']) > 64:
                data['clients'] = dict(sorted(data['clients'].items(), key=lambda pair: pair[1]['received_at'])[-64:])
            data['active_client_id'] = client
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, path)
            self.generation += 1
            self.condition.notify_all()
            return deepcopy(state)
