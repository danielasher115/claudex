import asyncio
import json
import uuid

import httpx
import pytest

from codex_sessions.client import call
from test_smoke import service


@pytest.mark.parametrize('mode,status,expected_mode', [(None, 'active', 'steer'), ('queue', 'active', 'queue'), (None, 'idle', 'queue'), ('steer', 'active', 'steer')])
def test_protocol_sends_steer_active_by_default_but_preserve_explicit_queue(tmp_path, mode, status, expected_mode):
    async def check():
        instance = service(tmp_path); instance.rpc.sessions['a'] = status
        arguments = {'thread_id': 'a', 'text': 'New guidance'}
        if mode is not None:
            arguments['mode'] = mode
        receipt = await instance.protocol.dispatch('agents.send', arguments)
        assert receipt['mode'] == expected_mode
        if expected_mode == 'steer':
            assert receipt['status'] == 'delivered'
            assert instance.rpc.starts[-1][0] == 'turn/steer'
            assert instance.rpc.starts[-1][1]['expectedTurnId'] == 'turn-active'
        else:
            assert receipt['status'] == 'queued'
            assert not instance.rpc.starts
    asyncio.run(check())


@pytest.mark.parametrize('spelling', ['canonical', 'upper', 'braced', 'hex'])
def test_implicit_mode_retry_keeps_the_original_receipt_after_the_turn_finishes(tmp_path, spelling):
    async def check():
        instance = service(tmp_path); instance.rpc.sessions['a'] = 'active'
        value = uuid.UUID('ea799ef5-6151-452f-b66c-f6005defa473')
        request_id = {'canonical': str(value), 'upper': str(value).upper(), 'braced': '{' + str(value) + '}', 'hex': value.hex}[spelling]
        arguments = {'thread_id': 'a', 'text': 'New guidance', 'request_id': request_id}
        first = await instance.protocol.dispatch('agents.send', arguments)
        instance.rpc.sessions['a'] = 'idle'
        second = await instance.protocol.dispatch('agents.send', arguments)
        assert first == second and first['mode'] == 'steer'
        assert len(instance.rpc.starts) == 1
    asyncio.run(check())


def test_active_agent_with_unresolved_turn_never_silently_queues(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc.sessions['a'] = 'active'
        original = instance.rpc.call
        async def racing_state(method, params=None):
            if method == 'thread/turns/list':
                return {'data': [{'id': 'previous-turn', 'status': 'completed'}]}
            return await original(method, params)
        instance.rpc.call = racing_state
        with pytest.raises(ValueError, match='active turn'):
            await instance.protocol.dispatch('agents.send', {'thread_id': 'a', 'text': 'Guidance for current work'})
        assert instance.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0
    asyncio.run(check())


def test_explicit_steering_of_an_idle_agent_never_queues(tmp_path):
    async def check():
        instance = service(tmp_path)
        with pytest.raises(ValueError, match='active turn'):
            await instance.protocol.dispatch('agents.send', {'thread_id': 'a', 'text': 'Steer now', 'mode': 'steer'})
        assert instance.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0
    asyncio.run(check())


@pytest.mark.parametrize('mode,active,expected_mode', [(None, True, 'steer'), ('queue', True, 'queue'), (None, False, 'queue')])
def test_mcp_http_client_resolves_defaults_before_sending_to_an_existing_gateway(tmp_path, monkeypatch, mode, active, expected_mode):
    (tmp_path / 'mcp.token').write_text('local-test-token')
    requests = []
    def handler(request):
        body = json.loads(request.content); requests.append(body)
        if body['method'] == 'agents.status':
            assert mode != 'queue'
            result = {'status': 'active' if active else 'idle', 'active_turn_id': 'native-turn' if active else None, 'messages': []}
        else:
            assert body['method'] == 'agents.send'
            result = body['params']
        return httpx.Response(200, json={'result': result})
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)))
    params = {'thread_id': 'a', 'text': 'New guidance'}
    if mode is not None:
        params['mode'] = mode
    receipt = call('agents.send', params, 8766, tmp_path)
    assert receipt['mode'] == expected_mode
    if expected_mode == 'steer':
        assert receipt['expected_turn_id'] == 'native-turn'
    if mode == 'queue':
        assert len(requests) == 1
