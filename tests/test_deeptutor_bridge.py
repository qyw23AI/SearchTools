import json
from pathlib import Path
import tempfile
import unittest

from searchtools.core import Store
from searchtools.models import sync_model_from_deeptutor
from searchtools.deeptutor_bridge import get_deeptutor_status, register_mcp_server
from searchtools.mcp_server import zotero_list_collections_and_papers, saved_evidence_cards_query


class DeepTutorBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.store = Store(self.root / 'data')
        self.pid = self.store.create_project('测试论文项目')

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_sync_model_from_deeptutor(self):
        # Should read /home/qyw/DeepTutor/data/user/settings/model_catalog.json
        try:
            dt_cat = sync_model_from_deeptutor()
            self.assertIn('connections', dt_cat)
            self.assertIn('services', dt_cat)
            self.assertIn('llm', dt_cat['services'])
            self.assertIn('embedding', dt_cat['services'])
            info = dt_cat.get('deeptutor_info', {})
            self.assertTrue(info.get('llm_model'))
            self.assertTrue(info.get('emb_model'))
        except FileNotFoundError:
            self.skipTest('DeepTutor directory not found')

    def test_evidence_cards_crud_and_formatting(self):
        card = {
            'citation': 'Vaswani et al. (2017)',
            'doi': '10.48550/arXiv.1706.03762',
            'title': 'Attention Is All You Need',
            'page': 3,
            'quote': 'The Transformer allows for significantly more parallelization and can reach a new state of the art in translation quality.',
            'summary': 'Transformer 架构通过自注意力机制实现更高并行度',
            'claim': '比较 Transformer 与传统序列模型并行能力',
            'score': 9.5,
            'pinned': 1,
            'tags': 'architecture,parallel'
        }
        cid = self.store.save_evidence_card(self.pid, card)
        self.assertTrue(cid.startswith(('ec-', 'ev_')))

        # List cards
        cards = self.store.list_evidence_cards(self.pid)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['citation'], 'Vaswani et al. (2017)')
        self.assertEqual(cards[0]['score'], 9.5)
        self.assertEqual(cards[0]['pinned'], 1)

        # Filter by score
        cards_high = self.store.list_evidence_cards(self.pid, min_score=9.0)
        self.assertEqual(len(cards_high), 1)
        cards_none = self.store.list_evidence_cards(self.pid, min_score=9.8)
        self.assertEqual(len(cards_none), 0)

        # Toggle pin
        is_pinned = self.store.toggle_pin_evidence_card(self.pid, cid)
        self.assertFalse(is_pinned)
        is_pinned_again = self.store.toggle_pin_evidence_card(self.pid, cid)
        self.assertTrue(is_pinned_again)

        # Citation formatting
        prompt_format = self.store.format_evidence_cards_citation([cid], style='prompt')
        self.assertIn('Vaswani et al. (2017)', prompt_format)
        self.assertIn('p.3', prompt_format)
        self.assertTrue('核心要点' in prompt_format or '核心论据' in prompt_format)

        inline_format = self.store.format_evidence_cards_citation([cid], style='inline')
        self.assertTrue('[1]' in inline_format or 'Vaswani et al.' in inline_format)

        # Extract and save from mock result
        mock_result = {
            'question': '测试问题',
            'contexts': [
                {
                    'id': 'ctx1',
                    'citation': 'Dao (2023)',
                    'title': 'FlashAttention-2',
                    'summary': '改进的显存与计算分块算法',
                    'text': 'FlashAttention-2 achieves a 2x speedup over FlashAttention.',
                    'score': 8.8,
                    'page': 5
                }
            ]
        }
        extracted = self.store.extract_and_save_evidence_from_result(self.pid, 'job-123', mock_result)
        self.assertEqual(len(extracted), 1)
        all_cards = self.store.list_evidence_cards(self.pid)
        self.assertEqual(len(all_cards), 2)

        # Remove card
        ok = self.store.remove_evidence_card(self.pid, cid)
        self.assertTrue(ok)
        self.assertEqual(len(self.store.list_evidence_cards(self.pid)), 1)

    def test_mcp_registration(self):
        status = get_deeptutor_status()
        self.assertIn('alive', status)
        self.assertIn('backend', status)
        self.assertIn('model_sync', status)

    def test_saved_evidence_cards_query_tool(self):
        # Tool call
        res = saved_evidence_cards_query(project_name='测试论文项目')
        self.assertIn('测试论文项目', res)


if __name__ == '__main__':
    unittest.main()
