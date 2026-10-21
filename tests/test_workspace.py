import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from searchtools.core import Store,Worker
from searchtools.server import App,handler


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.a=self.store.create_project('甲项目')
        self.b=self.store.create_project('乙项目')

    def tearDown(self):self.tmp.cleanup()

    def test_file_identity_and_project_isolation(self):
        first=self.store.import_paper(self.a,'../论文.txt',b'first paper')
        again=self.store.import_paper(self.a,'other.txt',b'first paper')
        other=self.store.import_paper(self.b,'论文.txt',b'first paper')
        self.assertEqual(first,again)
        self.assertNotEqual(first,other)
        self.assertEqual(len(list((self.store.folder(self.a)/'papers').iterdir())),1)
        self.assertEqual(len(self.store.rows('SELECT * FROM papers WHERE project=?',(self.a,))),1)
        with self.assertRaises(ValueError):self.store.folder('../escape')
        with self.assertRaises(ValueError):self.store.import_paper(self.a,'bad.pdf',b'not a pdf')

    def test_queued_task_freezes_input_scope(self):
        self.store.import_paper(self.a,'a.txt',b'original')
        jid=self.store.enqueue(self.a,'ask','what?','local')
        self.store.import_paper(self.a,'b.txt',b'added later')
        work=self.store.folder(self.a)/'jobs'/jid
        self.assertEqual(len(json.loads((work/'manifest.json').read_text())),1)
        self.assertEqual(len(list((work/'papers').iterdir())),1)

    def test_invalid_local_scope_and_cancel(self):
        with self.assertRaises(ValueError):self.store.enqueue(self.a,'ask','q')
        jid=self.store.enqueue(self.a,'research','topic','web')
        worker=Worker(self.store,{})
        worker.cancel(jid)
        self.assertEqual(self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status'],'cancelled')

    def test_gpu_guard_does_not_launch_engine(self):
        jid=self.store.enqueue(self.a,'research','topic','web')
        calls=[]
        def guard():
            calls.append(1)
            worker.stopped.set()
            return True,'training is running'
        worker=Worker(self.store,{},guard=guard)
        worker.run()
        row=self.store.rows('SELECT * FROM jobs WHERE id=?',(jid,))[0]
        self.assertEqual(row['status'],'waiting')
        self.assertIsNone(worker.process)
        self.assertEqual(len(calls),1)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=App({'data':self.tmp.name,'port':0})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.app))
        self.app.cfg['port']=self.server.server_port
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def test_same_origin_and_mutation_token(self):
        with self.assertRaises(HTTPError) as e:
            urlopen(Request(self.base+'/api/projects',data=b'{"name":"x"}',headers={'Content-Type':'application/json'}))
        self.assertEqual(e.exception.code,403)
        req=Request(self.base+'/api/projects',data=b'{"name":"x"}',headers={'Content-Type':'application/json','X-SearchTools-Token':self.app.token})
        pid=json.load(urlopen(req))['id']
        result=json.load(urlopen(self.base+'/api/project?id='+pid))
        self.assertEqual(result['project']['name'],'x')
        with self.assertRaises(HTTPError):
            urlopen(Request(self.base+'/',headers={'Host':'evil.example'}))
        with self.assertRaises(HTTPError):
            urlopen(Request(self.base+'/api/projects',data=b'{"name":"x"}',headers={'X-SearchTools-Token':self.app.token,'Origin':'https://evil.example'}))

    def test_user_content_is_not_returned_as_executable_html(self):
        with urlopen(self.base+'/') as r:
            self.assertIn('frame-ancestors',r.headers['Content-Security-Policy'])
            self.assertNotIn(b'__TOKEN__',r.read())
        with self.assertRaises(HTTPError):urlopen(self.base+'/../../etc/passwd')


if __name__=='__main__':unittest.main()

class ProcessTests(unittest.TestCase):
    def test_worker_persists_success_and_cancel_stops_only_its_process(self):
        import sys,time
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            engine=base/'searchtools'
            engine.mkdir()
            (engine/'engine_worker.py').write_text('''import json,sys,time
from pathlib import Path
job=json.loads(Path(sys.argv[1]).read_text())
if job['prompt']=='wait': time.sleep(60)
Path(job['output']).write_text(json.dumps({'answer':'Verified result','sources':[]}))
''')
            store=Store(base/'data')
            pid=store.create_project('process test')
            cfg={'gpt_python':sys.executable,'gpt_root':tmp,'paperqa_settings':str(base/'settings.json')}
            worker=Worker(store,cfg)
            first=store.enqueue(pid,'research','complete','web')
            with patch('searchtools.core.ROOT',base):
                worker.execute(store.rows('SELECT * FROM jobs WHERE id=?',(first,))[0])
                self.assertEqual(store.rows('SELECT status FROM jobs WHERE id=?',(first,))[0]['status'],'done')
                self.assertEqual((store.folder(pid)/'results'/f'{first}.md').read_text(),'Verified result')
                second=store.enqueue(pid,'research','wait','web')
                thread=threading.Thread(target=worker.execute,args=(store.rows('SELECT * FROM jobs WHERE id=?',(second,))[0],))
                thread.start()
                for _ in range(100):
                    if worker.process:break
                    time.sleep(.02)
                self.assertIsNotNone(worker.process)
                process=worker.process
                worker.cancel(second)
                thread.join(timeout=15)
                self.assertFalse(thread.is_alive())
                self.assertIsNotNone(process.poll())
                self.assertEqual(store.rows('SELECT status FROM jobs WHERE id=?',(second,))[0]['status'],'cancelled')
