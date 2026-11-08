import tempfile
import unittest

from searchtools.core import Store, DeletedEvidenceCardError
from searchtools.research import Research


class CardDeletionTests(unittest.TestCase):
    def test_delete_is_scoped_idempotent_and_prevents_resurrection(self):
        with tempfile.TemporaryDirectory() as path:
            store = Store(path)
            scope = store.create_project('deletion')
            other = store.create_project('other')
            card = {'id':'delete-me', 'job_id':'old-job', 'paper_id':'paper', 'quote':'Original evidence quote'}
            store.save_evidence_card(scope, card)
            revision = store.cards_revision()
            action = store.enqueue_zotero_action({'action':'open_card', 'card_id':card['id']})
            with self.assertRaises(ValueError):
                store.remove_evidence_card(other, card['id'])
            self.assertIsNotNone(store.get_evidence_card(card['id']))
            result = store.remove_evidence_card(None, card['id'])
            self.assertTrue(result['ok'])
            self.assertNotEqual(revision, store.cards_revision())
            self.assertIsNone(store.get_evidence_card(card['id']))
            self.assertEqual(store.list_evidence_cards(scope), [])
            self.assertEqual(store.zotero_action_status(action)['status'], 'failed')
            self.assertTrue(store.remove_evidence_card(scope, card['id'])['already_deleted'])
            self.assertEqual(result['cards_revision'], store.cards_revision())
            self.assertFalse(store.remove_evidence_card(scope, 'unknown')['ok'])
            for identity in ('delete-me', 'new-random-id'):
                with self.assertRaises(DeletedEvidenceCardError):
                    store.save_evidence_card(scope, dict(card, id=identity))

    def test_deleted_source_preserves_review_history_without_crashing_audit(self):
        with tempfile.TemporaryDirectory() as path:
            store = Store(path)
            scope = store.create_project('review')
            research = Research(store)
            store.save_evidence_card(scope, {'id':'source', 'quote':'Original paragraph'})
            run = research.call('run', {'action':'create', 'scope_id':scope, 'topic':'deletion'})
            atom = research.call('atoms', {'action':'create', 'run_id':run['id'], 'text':'Claim'})
            research.call('bindings', {'action':'assign', 'run_id':run['id'], 'atom_id':atom['id'], 'card_id':'source'})
            store.locate_card = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Panel listing must not parse PDFs'))
            self.assertEqual(research.card_reviews('source')[0]['status'], 'pending')
            store.remove_evidence_card(scope, 'source')
            audit = research.call('audit', {'run_id':run['id']})
            self.assertEqual(len(audit['bindings']), 1)
            self.assertTrue(audit['review_states'][0]['source_deleted'])
            self.assertEqual(audit['accepted_supports'], [])


if __name__ == '__main__':
    unittest.main()
