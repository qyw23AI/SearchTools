import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('fastapi', 'httpx', 'deeptutor'))


@unittest.skipUnless(AVAILABLE, 'DeepTutor gateway test runtime required')
class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from fastapi import FastAPI
        from integrations.deeptutor import evidence_router
        from deeptutor.services.llm.provider_core.base import LLMResponse
        self.temp = tempfile.TemporaryDirectory()
        key = Path(self.temp.name)/'key';key.write_text('local-adapter-test-key')
        self.config = SimpleNamespace(model='gpt-5.6-sol', reasoning_effort='low')
        self.provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content='GATEWAY_OK',finish_reason='stop')))
        self.patches = [patch.object(evidence_router, 'KEY_FILE', key),
                        patch('deeptutor.services.model_selection.runtime.resolve_llm_config_for_selection', return_value=self.config),
                        patch('deeptutor.services.llm.factory.LLMFactory.get_provider', return_value=self.provider)]
        for p in self.patches:p.start()
        app = FastAPI();app.include_router(evidence_router.router)
        self.app = app
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',123)),base_url='http://local')
        self.headers = {'Authorization':'Bearer local-adapter-test-key','X-DeepTutor-Selection':json.dumps({'profile_id':'codex','model_id':'gpt56'})}
        self.body = {'model':'gpt-5.6-sol','messages':[{'role':'user','content':'Synthetic test'}]}

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    async def test_exact_selected_provider_and_model(self):
        response = await self.client.post('/chat/completions',json=self.body,headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['X-DeepTutor-Model'],'gpt-5.6-sol')
        self.assertEqual(response.json()['choices'][0]['message']['content'],'GATEWAY_OK')
        self.assertNotIn('local-adapter-test-key',response.text)
        self.assertEqual(self.provider.chat.await_args.kwargs['model'],'gpt-5.6-sol')
        self.assertEqual(self.provider.chat.await_args.kwargs['reasoning_effort'],'low')

    async def test_wrong_auth_or_model_never_reaches_inference(self):
        response = await self.client.post('/chat/completions',json=self.body,headers={**self.headers,'Authorization':'Bearer wrong'})
        self.assertEqual(response.status_code,401)
        response = await self.client.post('/chat/completions',json={**self.body,'model':'another-model'},headers=self.headers)
        self.assertEqual(response.status_code,409)
        self.provider.chat.assert_not_awaited()

    async def test_streaming_completion_format(self):
        response = await self.client.post('/chat/completions',json={**self.body,'stream':True},headers=self.headers)
        self.assertEqual(response.status_code,200)
        chunks=[line.removeprefix('data: ') for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(chunks[-1],'[DONE]')
        self.assertEqual(json.loads(chunks[0])['choices'][0]['delta']['content'],'GATEWAY_OK')
        self.assertEqual(json.loads(chunks[-2])['choices'][0]['finish_reason'],'stop')

    async def test_non_loopback_client_is_rejected(self):
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,client=('192.0.2.1',123)),base_url='http://local') as client:
            response = await client.post('/chat/completions',json=self.body,headers=self.headers)
        self.assertEqual(response.status_code,403)
        self.provider.chat.assert_not_awaited()
