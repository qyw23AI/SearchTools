"""Loopback-only adapter using DeepTutor's own provider and login lifecycle.

Installed as deeptutor.api.routers.searchtools_bridge. No OAuth credentials
are returned to SearchTools, browsers or PaperQA2.
"""
import json
from pathlib import Path
import secrets
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()
KEY_FILE = Path.home() / '.config/searchtools/deeptutor_gateway.key'


@router.post('/chat/completions')
async def completions(request: Request):
    if request.client is None or request.client.host not in ('127.0.0.1', '::1'):
        raise HTTPException(403, 'Local inference adapter only')
    try:
        expected = KEY_FILE.read_text().strip()
    except OSError:
        raise HTTPException(503, 'Local inference adapter not provisioned') from None
    supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(401, 'Invalid local inference adapter credential')
    try:
        selection = json.loads(request.headers.get('x-deeptutor-selection', 'null'))
        if not isinstance(selection, dict) or not selection.get('profile_id') or not selection.get('model_id'):
            raise ValueError('Missing selection')
        from deeptutor.services.model_selection.runtime import resolve_llm_config_for_selection
        from deeptutor.services.llm.factory import LLMFactory
        config = resolve_llm_config_for_selection(selection)
        body = await request.json()
        if body.get('model') != config.model.removeprefix('models/'):
            raise HTTPException(409, 'Requested model differs from the pinned DeepTutor selection')
        messages = body.get('messages')
        if not isinstance(messages, list) or not messages:
            raise ValueError('Messages required')
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, 'Invalid DeepTutor selection or request') from None
    provider = LLMFactory.get_provider(config)
    # Provider owns OAuth renewal, account authorization, routing and inference guard.
    response = await provider.chat(
        messages=messages, model=config.model, tools=body.get('tools'),
        tool_choice=body.get('tool_choice'), temperature=body.get('temperature', 0),
        max_tokens=body.get('max_tokens', 4096), reasoning_effort=config.reasoning_effort)
    if response.finish_reason == 'error':
        raise HTTPException(502, response.content or 'DeepTutor provider failed')
    tool_calls = [{'id': call.id, 'type': 'function', 'function': {'name': call.name,
                   'arguments': call.arguments if isinstance(call.arguments, str) else json.dumps(call.arguments)}}
                  for call in response.tool_calls]
    message = {'role': 'assistant', 'content': response.content or ''}
    if tool_calls:
        message['tool_calls'] = tool_calls
    result = {'id': 'dt-' + uuid.uuid4().hex, 'object': 'chat.completion', 'created': int(time.time()),
              'model': config.model.removeprefix('models/'),
              'choices': [{'index': 0, 'message': message, 'finish_reason': response.finish_reason or 'stop'}]}
    headers = {'X-DeepTutor-Model': result['model'], 'X-DeepTutor-Profile': selection['profile_id']}
    if not body.get('stream'):
        return JSONResponse(result, headers=headers)

    async def events():
        # A standards-compliant aggregate stream; generation stays in the provider.
        chunk = {key: result[key] for key in ('id', 'created', 'model')}
        chunk['object'] = 'chat.completion.chunk'
        delta = dict(message)
        if tool_calls:
            delta['tool_calls'] = [dict(call, index=i) for i, call in enumerate(tool_calls)]
        yield 'data: ' + json.dumps(dict(chunk, choices=[{'index': 0, 'delta': delta, 'finish_reason': None}])) + '\n\n'
        yield 'data: ' + json.dumps(dict(chunk, choices=[{'index': 0, 'delta': {}, 'finish_reason': response.finish_reason or 'stop'}])) + '\n\n'
        yield 'data: [DONE]\n\n'
    return StreamingResponse(events(), media_type='text/event-stream', headers=headers)
