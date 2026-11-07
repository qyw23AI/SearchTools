import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from searchtools.core import Store
from searchtools.server import App, handler


class ServerExtensionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        cfg = {
            'data': str(base / 'data'),
            'port': 0,
            'models_file': str(base / 'models.json'),
            'paperqa_settings': str(base / 'paperqa.json'),
            'paperqa_python': 'python3',
            'gpt_python': 'python3',
            'deeptutor_url': 'http://127.0.0.1:3782/chat',
            'gpt_url': 'http://127.0.0.1:8002'
        }
        self.app = App(cfg)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.app))
        self.app.cfg['port'] = self.server.server_port
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.project_id = self.app.store.create_project('测试项目')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def _req(self, path, data=None, token=True):
        headers = {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if token:
                headers['X-SearchTools-Token'] = self.app.token
            body = json.dumps(data).encode('utf-8')
        else:
            body = None
        req = Request(self.base + path, data=body, headers=headers)
        with urlopen(req) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def test_models_api_get_and_update(self):
        # 1. GET /api/models
        data = self._req('/api/models')
        self.assertIn('providers', data)
        self.assertIn('connections', data)
        self.assertIn('services', data)

        # 2. POST /api/models update
        update_payload = {
            'connections': [
                {
                    'id': 'conn-gemini',
                    'name': 'Google Gemini Flash',
                    'provider': 'gemini',
                    'api_key': 'AIzaKeySecret9999',
                    'models': ['gemini-2.5-flash']
                },
                {
                    'id': 'conn-ollama',
                    'name': '本地 Ollama',
                    'provider': 'ollama',
                    'base_url': 'http://127.0.0.1:11434',
                    'models': ['qwen3-embedding:4b']
                }
            ],
            'services': {
                'llm': {'connection': 'conn-gemini', 'model': 'gemini-2.5-flash'},
                'embedding': {'connection': 'conn-ollama', 'model': 'qwen3-embedding:4b'}
            }
        }
        res = self._req('/api/models', data=update_payload)
        self.assertTrue(any(c['id'] == 'conn-gemini' and c['api_key_set'] for c in res['connections']))

    def test_paper_remove_and_index_status(self):
        # Import paper with metadata
        pid = self.project_id
        paper_id = self.app.store.import_paper(
            pid, 'test_paper.pdf', b'%PDF-1.5 test content',
            zotero_item='K7XDUCU4', title='测试论文标题', doi='10.1234/test', origin='zotero'
        )

        # Verify index status API
        idx_status = self._req(f'/api/index/status?project={pid}')
        self.assertEqual(idx_status['total'], 1)
        self.assertEqual(idx_status['indexed'], 0)
        self.assertEqual(idx_status['pending'], 1)

        # Remove paper
        del_res = self._req('/api/paper/remove', data={'project': pid, 'id': paper_id})
        self.assertTrue(del_res['ok'])

        # Verify paper is deleted
        papers = self.app.store.rows('SELECT * FROM papers WHERE project=?', (pid,))
        self.assertEqual(len(papers), 0)

    def test_enqueue_with_options_and_evidence_kind(self):
        pid = self.project_id
        self.app.store.import_paper(pid, 'doc.txt', b'some paper content')

        job_id = self._req('/api/jobs', data={
            'project': pid,
            'kind': 'evidence',
            'prompt': '查找文献中的强化学习核心实验',
            'source': 'local',
            'options': {'preset': 'deep', 'answer_language': 'zh'}
        })['id']

        job_row = self.app.store.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
        self.assertEqual(job_row['kind'], 'evidence')
        options = json.loads(job_row['options'])
        self.assertEqual(options['preset'], 'deep')
        self.assertEqual(options['answer_language'], 'zh')

    def test_job_remove_completed_or_failed(self):
        pid = self.project_id
        self.app.store.import_paper(pid, 'doc2.txt', b'content')
        jid = self.app.store.enqueue(pid, 'ask', 'question', 'local')
        # Mark as failed or done
        self.app.store.update(jid, 'failed', '出错了')

        res = self._req('/api/job/remove', data={'project': pid, 'id': jid})
        self.assertTrue(res['ok'])
        self.assertEqual(len(self.app.store.rows('SELECT * FROM jobs WHERE id=?', (jid,))), 0)

        # Cannot remove running job
        jid2 = self.app.store.enqueue(pid, 'ask', 'question 2', 'local')
        self.app.store.update(jid2, 'running', '执行中')
        with self.assertRaises(HTTPError) as ctx:
            self._req('/api/job/remove', data={'project': pid, 'id': jid2})
        self.assertEqual(ctx.exception.code, 400)

    def test_mode_switch_api(self):
        # Initial mode should be deeptutor
        res = self._req('/api/models')
        self.assertEqual(res.get('mode'), 'deeptutor')

        # Switch to custom
        res_custom = self._req('/api/models/mode', data={'mode': 'custom'})
        self.assertEqual(res_custom.get('mode'), 'custom')

        # Verify via GET
        res_get = self._req('/api/models')
        self.assertEqual(res_get.get('mode'), 'custom')

        # Switch back to deeptutor
        res_dt = self._req('/api/models/mode', data={'mode': 'deeptutor'})
        self.assertEqual(res_dt.get('mode'), 'deeptutor')

    def test_deeptutor_live_selection_sync_api(self):
        # Create a mock DeepTutor directory with multiple profiles (Gemini & DeepSeek)
        dt_dir = Path(self.tmp.name) / 'mock_deeptutor'
        settings_dir = dt_dir / 'data/user/settings'
        settings_dir.mkdir(parents=True, exist_ok=True)
        catalog_path = settings_dir / 'model_catalog.json'
        catalog_path.write_text(json.dumps({
            'connections': [
                {'id': 'conn-gemini', 'provider': 'gemini', 'api_key': 'AIzaMockGemini', 'base_url': ''},
                {'id': 'conn-deepseek', 'provider': 'deepseek', 'api_key': 'sk-mock-deepseek', 'base_url': 'https://api.deepseek.com'}
            ],
            'services': {
                'llm': {
                    'active_profile_id': 'prof-gemini',
                    'active_model_id': 'mod-gemini-38',
                    'profiles': [
                        {
                            'id': 'prof-gemini',
                            'name': 'Gemini',
                            'binding': 'gemini',
                            'models': [{'id': 'mod-gemini-38', 'model': 'models/gemini-3.8-flash'}]
                        },
                        {
                            'id': 'prof-deepseek',
                            'name': 'DeepSeek',
                            'binding': 'deepseek',
                            'models': [{'id': 'mod-deepseek-flash', 'model': 'deepseek-flash'}]
                        }
                    ]
                },
                'embedding': {
                    'active_profile_id': 'prof-emb',
                    'active_model_id': 'mod-emb',
                    'profiles': [
                        {
                            'id': 'prof-emb',
                            'name': 'Ollama',
                            'binding': 'ollama',
                            'models': [{'id': 'mod-emb', 'model': 'qwen3-embedding:4b'}]
                        }
                    ]
                }
            }
        }), encoding='utf-8')

        from searchtools.models import sync_deeptutor_live_selection, sync_model_from_deeptutor
        # Before live sync: should be gemini-3.8-flash
        initial = sync_model_from_deeptutor(deeptutor_dir=dt_dir)
        self.assertEqual(initial['deeptutor_info']['llm_provider'], 'gemini')
        self.assertEqual(initial['deeptutor_info']['llm_model'], 'gemini-3.8-flash')

        # Simulate DeepTutor UI dropdown selection of DeepSeek
        synced = sync_deeptutor_live_selection({'model': 'deepseek-flash', 'profile_name': 'DeepSeek', 'provider': 'deepseek'}, deeptutor_dir=dt_dir)
        self.assertEqual(synced['deeptutor_info']['llm_provider'], 'deepseek')
        self.assertEqual(synced['deeptutor_info']['llm_model'], 'deepseek-flash')

        # Verify model_catalog.json was also updated
        after = sync_model_from_deeptutor(deeptutor_dir=dt_dir)
        self.assertEqual(after['deeptutor_info']['llm_provider'], 'deepseek')
        self.assertEqual(after['deeptutor_info']['llm_model'], 'deepseek-flash')


if __name__ == '__main__':
    unittest.main()
