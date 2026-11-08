import tempfile
import unittest
from searchtools.core import Store
from searchtools.research import Research


class ResearchReviewTests(unittest.TestCase):
    def test_reviews_are_bound_to_the_actual_source_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            scope = store.create_project('isolated')
            research = Research(store)
            run = research.call('run', {'action':'create', 'scope_id':scope, 'topic':'review'})
            atom = research.call('atoms', {'action':'create', 'run_id':run['id'], 'text':'Claim'})
            store.save_evidence_card(scope, {'id':'card', 'quote':'original quotation'})
            store.locate_card = lambda *a, **kw: {'status':'exact', 'spans':[{'paragraph_id':'p1'}]}
            binding = research.call('bindings', {'action':'assign', 'run_id':run['id'], 'atom_id':atom['id'], 'card_id':'card'})
            packet = research.packet(binding)
            request = {'run_id':run['id'], 'binding_id':binding['id'], 'packet_revision':packet['packet_revision'],
                       'decision':'accept', 'relation':'supports', 'span_ids':['p1'], 'reason':'Directly supports the claim'}
            research.call('submit_review', request)
            self.assertEqual(research.accepted(run['id']), [])
            research.call('finalize_atom', {'run_id':run['id'], 'atom_id':atom['id'], 'overall_verdict':'supported', 'reason':'All candidate evidence reviewed'})
            self.assertEqual(len(research.accepted(run['id'])), 1)
            wrong = research.call('atoms', {'action':'create', 'run_id':run['id'], 'text':'Different claim'})
            with self.assertRaises(ValueError):
                store.format_evidence_cards_citation(['card'], doc_ref='research:' + run['id'] + ':' + wrong['id'])
            store.save_evidence_card(scope, {'id':'card', 'quote':'a changed quotation'})
            self.assertEqual(research.accepted(run['id']), [])
            with self.assertRaises(ValueError):
                research.call('submit_review', request)
            with self.assertRaises(ValueError):
                store.format_evidence_cards_citation(['card'])


if __name__ == '__main__':
    unittest.main()
