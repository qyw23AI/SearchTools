import json
import tempfile
import unittest
from pathlib import Path

from searchtools.models import (
    Catalog,
    build_paperqa_settings,
    default_catalog,
    index_name,
    migrate_paperqa,
    test_payload,
    PROVIDERS
)


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog_file = Path(self.tmp.name) / 'models.json'
        self.cat = Catalog(self.catalog_file)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_catalog_and_public_masking(self):
        data = self.cat.load()
        self.assertEqual(data['version'], 1)
        self.assertIn('conn-ollama', [c['id'] for c in data['connections']])
        self.assertIn('llm', data['services'])
        self.assertIn('embedding', data['services'])

        # Public view must mask keys and not leak raw secrets
        pub = self.cat.public()
        self.assertIn('providers', pub)
        self.assertIn('presets', pub)
        for c in pub['connections']:
            self.assertNotIn('api_key', c)
            self.assertIn('api_key_set', c)

    def test_add_and_update_connection_with_api_key(self):
        # Add Gemini connection with secret
        update_body = {
            'connections': [
                {
                    'id': 'conn-gemini',
                    'name': 'Google Gemini Pro',
                    'provider': 'gemini',
                    'api_key': 'AIzaSyTestSecret12345678',
                    'models': ['gemini-2.5-flash', 'gemini-2.5-pro']
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
                'llm': {'connection': 'conn-gemini', 'model': 'gemini-2.5-pro'},
                'embedding': {'connection': 'conn-ollama', 'model': 'qwen3-embedding:4b'}
            }
        }
        pub = self.cat.update(update_body)
        gemini_conn = next(c for c in pub['connections'] if c['id'] == 'conn-gemini')
        self.assertTrue(gemini_conn['api_key_set'])
        self.assertTrue(gemini_conn['api_key_hint'].endswith('5678'))

        # Updating without specifying api_key must retain the secret
        update_no_key = {
            'connections': [
                {
                    'id': 'conn-gemini',
                    'name': 'Google Gemini Pro (Renamed)',
                    'provider': 'gemini',
                    'models': ['gemini-2.5-flash', 'gemini-2.5-pro']
                },
                {
                    'id': 'conn-ollama',
                    'name': '本地 Ollama',
                    'provider': 'ollama',
                    'base_url': 'http://127.0.0.1:11434',
                    'models': ['qwen3-embedding:4b']
                }
            ]
        }
        pub2 = self.cat.update(update_no_key)
        gemini_conn2 = next(c for c in pub2['connections'] if c['id'] == 'conn-gemini')
        self.assertTrue(gemini_conn2['api_key_set'])

        # Check raw stored secret in file
        raw = self.cat.load()
        raw_conn = next(c for c in raw['connections'] if c['id'] == 'conn-gemini')
        self.assertEqual(raw_conn['api_key'], 'AIzaSyTestSecret12345678')

    def test_build_paperqa_settings_hybrid_architecture(self):
        # Configure Hybrid: Gemini for LLM and Local Ollama for Embedding
        data = self.cat.load()
        data['mode'] = 'custom'
        data['connections'] = [
            {
                'id': 'c-gemini',
                'name': 'Gemini',
                'provider': 'gemini',
                'api_key': 'AIzaKey',
                'models': ['gemini-2.5-flash']
            },
            {
                'id': 'c-ollama',
                'name': 'Ollama',
                'provider': 'ollama',
                'base_url': 'http://127.0.0.1:11434',
                'api_key': '',
                'models': ['qwen3-embedding:4b']
            }
        ]
        data['services']['llm'] = {'connection': 'c-gemini', 'model': 'gemini-2.5-flash'}
        data['services']['embedding'] = {'connection': 'c-ollama', 'model': 'qwen3-embedding:4b'}

        settings = build_paperqa_settings(data, options={'preset': 'deep', 'answer_language': 'zh'})

        # Check LLM routing
        self.assertEqual(settings['llm'], 'gemini/gemini-2.5-flash')
        llm_params = settings['llm_config']['model_list'][0]['litellm_params']
        self.assertEqual(llm_params['model'], 'gemini/gemini-2.5-flash')
        self.assertEqual(llm_params['api_key'], 'AIzaKey')

        # Check Embedding routing (remains local Ollama)
        self.assertEqual(settings['embedding'], 'ollama/qwen3-embedding:4b')
        emb_kwargs = settings['embedding_config']['kwargs']
        self.assertEqual(emb_kwargs['api_base'], 'http://127.0.0.1:11434')

        # Check Preset overrides
        self.assertEqual(settings['answer']['evidence_k'], 20)
        self.assertIn('Simplified Chinese', settings['answer']['answer_length'])

    def test_test_payload_structure(self):
        data = self.cat.load()
        conn_body = {
            'provider': 'deepseek',
            'base_url': 'https://api.deepseek.com',
            'api_key': 'sk-deepseek-123',
            'model': 'deepseek-chat'
        }
        payload = test_payload(data, conn_body, 'llm')
        self.assertEqual(payload['kind'], 'test')
        self.assertEqual(payload['service'], 'llm')
        self.assertEqual(payload['params']['model'], 'deepseek/deepseek-chat')
        self.assertEqual(payload['params']['api_key'], 'sk-deepseek-123')
        self.assertEqual(payload['params']['api_base'], 'https://api.deepseek.com')


    def test_mode_switching_and_public_sync(self):
        # Default mode is deeptutor
        data = self.cat.load()
        self.assertEqual(data.get('mode'), 'deeptutor')

        pub = self.cat.public()
        self.assertEqual(pub.get('mode'), 'deeptutor')
        self.assertIn('deeptutor_sync', pub)
        # Should have synthetic deeptutor connections injected in public view
        conn_ids = [c['id'] for c in pub['connections']]
        self.assertIn('conn-dt-synced-llm', conn_ids)
        self.assertIn('conn-dt-synced-emb', conn_ids)
        # Services should point to synced connection
        self.assertEqual(pub['services']['llm']['connection'], 'conn-dt-synced-llm')
        self.assertEqual(pub['services']['embedding']['connection'], 'conn-dt-synced-emb')

        # Switch to custom mode
        pub_custom = self.cat.update({'mode': 'custom'})
        self.assertEqual(pub_custom['mode'], 'custom')
        raw = self.cat.load()
        self.assertEqual(raw['mode'], 'custom')

        # In custom mode, synthetic connections are not injected
        custom_ids = [c['id'] for c in pub_custom['connections']]
        self.assertNotIn('conn-dt-synced-llm', custom_ids)

        # Switch back to deeptutor
        pub_dt = self.cat.update({'mode': 'deeptutor'})
        self.assertEqual(pub_dt['mode'], 'deeptutor')
        self.assertEqual(self.cat.load()['mode'], 'deeptutor')

    def test_test_payload_with_deeptutor_sync(self):
        data = self.cat.load()
        conn_body = {
            'id': 'conn-dt-synced-llm',
            'is_deeptutor_sync': True
        }
        payload = test_payload(data, conn_body, 'llm')
        self.assertEqual(payload['kind'], 'test')
        self.assertEqual(payload['service'], 'llm')
        self.assertIn('/', payload['params']['model'])
        # API key should have been picked up from DeepTutor catalog
        self.assertTrue(bool(payload['params']['api_key']))


if __name__ == '__main__':
    unittest.main()
