import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from searchtools.core import Store
from searchtools.models import build_paperqa_settings, default_catalog, sync_model_from_deeptutor
from searchtools.model_events import ModelEventHub, read_selection


class ModelSyncRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = self.root/'data/user/settings/model_catalog.json'
        self.settings.parent.mkdir(parents=True)
        self.catalog = {'services': {'llm': {'active_profile_id':'p1','active_model_id':'m1',
          'profiles': [
            {'id':'p1','binding':'ollama','models':[{'id':'m1','model':'model-one'}]},
            {'id':'p2','binding':'ollama','models':[{'id':'m2','model':'model-two'}]},
            {'id':'codex','binding':'openai_codex','models':[{'id':'gpt56','model':'gpt-5.6-sol'}]}
          ]}}}
        self.settings.write_text(json.dumps(self.catalog))
        self.live = self.root/'live.json'
        self.live.write_text(json.dumps({'updated_at':time.time()+100,'profile_id':'removed','model_id':'removed', 'binding':'ollama','model':'stale-model'}))
        self.patches = [patch('searchtools.models.DEEPTUTOR_DIR',self.root), patch('searchtools.models.LIVE_SYNC_FILE',self.live)]
        for p in self.patches:p.start()
        self.hub = ModelEventHub()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def select(self, client='tab-a', sequence=1, model='one'):
        return self.hub.select({'client_id':client,'sequence':sequence,'session_id':'session-'+client,
          'selection':{'profile_id':'p1' if model=='one' else 'p2','model_id':'m1' if model=='one' else 'm2'}})

    def test_events_wake_subscribers_and_override_stale_files_without_mutating_defaults(self):
        self.assertEqual(sync_model_from_deeptutor()['deeptutor_info']['llm_model'],'model-one')
        first=self.select()
        ready=threading.Event();received=[]
        def subscriber():
            with self.hub.condition:
                generation=self.hub.generation
                ready.set()
                self.hub.condition.wait_for(lambda:self.hub.generation!=generation,timeout=1)
                received.append(self.hub.current('tab-a'))
        thread=threading.Thread(target=subscriber);thread.start();ready.wait()
        started=time.monotonic();second=self.select(sequence=2,model='two');thread.join()
        self.assertLess(time.monotonic()-started,1)
        self.assertEqual(received[0]['revision'],second['revision'])
        self.assertEqual(sync_model_from_deeptutor(self.root)['deeptutor_info']['llm_model'],'model-two')
        self.assertEqual(json.loads(self.settings.read_text()),self.catalog)
        self.assertEqual(self.select(sequence=1)['revision'],second['revision'])
        with self.assertRaises(ValueError):read_selection('tab-a',first['revision'])
        self.select(client='tab-b')
        self.assertEqual(read_selection('tab-a')['model']['llm_model'],'model-two')
        self.assertEqual(read_selection('tab-b')['model']['llm_model'],'model-one')

    def test_codex_routes_through_local_provider_adapter(self):
        self.live.with_name('deeptutor_gateway.key').write_text('local-test-adapter-key')
        selection={'profile_id':'codex','model_id':'gpt56'}
        settings=build_paperqa_settings(default_catalog(), {'deeptutor_selection':selection})
        self.assertEqual(settings['llm'],'openai/gpt-5.6-sol')
        params=settings['llm_config']['model_list'][0]['litellm_params']
        self.assertEqual(params['api_base'],'http://127.0.0.1:8001/api/evidence-llm/v1')
        self.assertEqual(json.loads(params['extra_headers']['X-DeepTutor-Selection']),selection)
        self.assertEqual(params['api_key'],'local-test-adapter-key')
        self.assertTrue(params['drop_params'])
        with self.assertRaises(ValueError):sync_model_from_deeptutor(selection={'profile_id':'missing','model_id':'missing'})

    def test_queued_jobs_freeze_model_at_submission(self):
        models=self.root/'models.json';models.write_text(json.dumps(default_catalog()))
        cfg={'data':str(self.root/'store'),'models_file':str(models), 'paperqa_settings':str(self.root/'paperqa.json')}
        self.hub = ModelEventHub(cfg)
        store=Store(cfg['data'],cfg=cfg);pid=store.create_project('snapshot test')
        store.import_paper(pid,'source.txt',b'Synthetic source for model snapshot testing.')
        first=self.select()
        job1=store.enqueue(pid,'evidence','question','local', {'model_client_id':'tab-a','model_sync_revision':first['revision']})
        second=self.select(sequence=2,model='two')
        job2=store.enqueue(pid,'evidence','question','local', {'model_client_id':'tab-a','model_sync_revision':second['revision']})
        read=lambda jid:json.loads((store.folder(pid)/'jobs'/jid/'paperqa_settings.json').read_text())['llm']
        self.assertEqual(read(job1),'ollama/model-one')
        self.assertEqual(read(job2),'ollama/model-two')
        self.assertEqual(json.loads(Path(cfg['paperqa_settings']).read_text())['llm'], 'ollama/model-two')
        with self.assertRaises(ValueError):store.enqueue(pid,'evidence','question','local', {'model_client_id':'tab-a','model_sync_revision':first['revision']})
