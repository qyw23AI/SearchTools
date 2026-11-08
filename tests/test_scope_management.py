import tempfile
import unittest
from searchtools.core import Store
from searchtools.research import Research


class ScopeManagementTests(unittest.TestCase):
    def test_delete_and_restore_related_data_and_active_scope(self):
        with tempfile.TemporaryDirectory() as path:
            store=Store(path)
            other=store.create_project('Other')
            pid=store.create_project('Original')
            store.set_active_project(pid)
            store.save_evidence_card(pid,{'id':'scope-card','quote':'Source quote'})
            research=Research(store)
            run=research.call('run',{'action':'create','scope_id':pid,'topic':'Test'})
            research.call('atoms',{'run_id':run['id'],'action':'create','text':'Claim'})
            store.manage_scope('rename',pid,'Updated')
            self.assertEqual(store.project(pid)['name'],'Updated')
            result=store.manage_scope('delete',pid)
            self.assertEqual(result['active_project'],other)
            self.assertIsNone(store.get_evidence_card('scope-card'))
            self.assertEqual(research.records('', 'run'),[])
            self.assertEqual(store.manage_scope('trash')[0]['id'],pid)
            store.manage_scope('restore',pid)
            self.assertEqual(store.project(pid)['name'],'Updated')
            self.assertIsNotNone(store.get_evidence_card('scope-card'))
            self.assertEqual(len(research.records('', 'run')),1)
            self.assertEqual(store.manage_scope('trash'),[])

    def test_unfinished_tasks_block_deletion_and_last_scope_can_restore(self):
        with tempfile.TemporaryDirectory() as path:
            store=Store(path)
            pid=store.create_project('Last')
            with store.db() as db:
                db.execute("INSERT INTO jobs(id,project,kind,status,created) VALUES('job',?,'index','queued',0)",(pid,))
            with self.assertRaises(ValueError):store.manage_scope('delete',pid)
            store.update('job','done','finished')
            self.assertEqual(store.manage_scope('delete',pid)['active_project'],'')
            store.manage_scope('restore',pid)
            self.assertEqual(store.project(pid)['id'],pid)
            self.assertEqual(store.rows('SELECT * FROM jobs')[0]['id'],'job')
