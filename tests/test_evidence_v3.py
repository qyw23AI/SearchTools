import json
import tempfile
import unittest
from pathlib import Path
from searchtools.locate import fitz, locate_quote_in_pdf, normalize_with_map
from searchtools.atomic import validate_records
from searchtools.core import Store


class EvidenceV3Tests(unittest.TestCase):
    @unittest.skipUnless(fitz, 'PDF fixture generation requires the PDF runtime')
    def test_complete_quote_duplicate_and_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'e.pdf'
            with fitz.open() as doc:
                for text in ('A reproducible experiment shows improved robot tracking accuracy.',
                             'A different experiment shows reduced energy consumption.'):
                    page = doc.new_page()
                    page.insert_text((50, 60), text)
                doc.save(path)
            result = locate_quote_in_pdf(path, 'A reproducible experiment shows improved robot tracking accuracy.')
            self.assertEqual(result['status'], 'exact')
            self.assertEqual(result['page_index'], 0)
            self.assertTrue(result['spans'][0]['paragraph_id'])
            self.assertEqual(locate_quote_in_pdf(path, 'A reproducible experiment shows fabricated results.')['status'], 'unverified')
            cross = locate_quote_in_pdf(path, 'accuracy. A different experiment shows reduced energy consumption.')
            self.assertEqual(len(cross['spans']), 2)
            with fitz.open(path) as doc:
                doc[1].insert_text((50, 90), 'A reproducible experiment shows improved robot tracking accuracy.')
                duplicate = Path(tmp)/'dup.pdf'
                doc.save(duplicate)
            self.assertEqual(locate_quote_in_pdf(duplicate, result['quote'], '1')['status'], 'ambiguous')

    def test_hyphenation_and_chinese_normalization(self):
        self.assertEqual(normalize_with_map('reinforce-\nment learning')[0], 'reinforcementlearning')
        self.assertEqual(normalize_with_map('脉冲 神经\n网络')[0], '脉冲神经网络')

    @unittest.skipUnless(fitz, 'PDF fixture runtime required')
    def test_manifest_identity_survives_model_generated_short_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'store')
            pid = store.create_project('identity test')
            quote = 'Controller A used less electrical energy during the walking experiment.'
            with fitz.open() as doc:
                doc.new_page().insert_text((50,60),quote)
                body = doc.tobytes()
            paper_id = store.import_paper(pid,'long-verification-document.pdf',body,title='A long verification document title')
            paper = store.rows('SELECT * FROM papers WHERE id=?',(paper_id,))[0]
            context = {'atomic_schema_version':3,'docname':'unknownauthorsshorttitle',
                       'source_paper_id':paper_id,'source_sha256':paper['sha'],
                       'quote':quote,'text':quote,'source_context':quote,'claim':'Controller A used less energy.',
                       'relation':'supports','summary':'Lower walking energy.', 'score':8}
            cards = store.extract_and_save_evidence_from_result(pid,'job-ok',{'contexts':[context]})
            self.assertEqual(len(cards),1)
            self.assertEqual(cards[0]['paper_id'],paper_id)
            self.assertEqual(cards[0]['locate']['status'],'exact')
            context['source_sha256']='incorrect'
            self.assertEqual(store.extract_and_save_evidence_from_result(pid,'job-bad',{'contexts':[context]}),[])

    def test_atomic_quote_backcheck(self):
        context = {'text': 'The robot consumed 20 percent less energy during walking trials.'}
        valid, rejected = validate_records([
            {'claim': 'Walking energy decreased by 20 percent.', 'quote': context['text'], 'relation': 'supports'},
            {'claim': 'Accuracy improves.', 'quote': 'This sentence was invented by the model.', 'relation': 'supports'}], context)
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 1)

    def test_actions_survive_restart_and_need_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            aid = store.enqueue_zotero_action({'action': 'open_card', 'card_id': 'test'})
            store = Store(tmp)
            self.assertEqual(store.pop_zotero_actions()[0]['action_id'], aid)
            self.assertEqual(store.pop_zotero_actions(), [])
            with store.db() as db:
                db.execute('UPDATE zotero_actions SET lease_until=0 WHERE id=?', (aid,))
            self.assertEqual(store.pop_zotero_actions()[0]['action_id'], aid)
            self.assertEqual(store.ack_zotero_action(aid)['status'], 'done')
            self.assertEqual(store.pop_zotero_actions(), [])
