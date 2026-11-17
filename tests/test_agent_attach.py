import asyncio
import json

import pytest

from codex_sessions.interfaces import tools
from codex_sessions.mcp import TASKS, VERSION, Bridge
from test_smoke import message, service


def test_attach_recovers_existing_gateway_thread_without_changing_its_work(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions['a'] = 'active'
        request = json.dumps({'context': {'instructions': [{'source': 'caller', 'text': 'Keep this constraint'}]}})
        for identity, parent in [('a', None), ('b', 'a')]:
            instance.db.execute('INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)',
                                (identity, parent, 'Existing worker', 0 if parent is None else 1, 2, 4,
                                 'read-only', str(tmp_path), 'original-turn', 42, request))
        receipt = await instance.send('a', message('Saved follow-up', mode='queue'))
        instance.db.commit()
        before = list(instance.db.iterdump())
        calls = []
        original = instance.rpc.call
        async def observed(method, params=None):
            calls.append((method, params))
            if method == 'thread/turns/list':
                return {'data': [{'id': 'turn-active', 'rootTurnId': 'task-root', 'status': 'inProgress'}]}
            return await original(method, params)
        instance.rpc.call = observed

        result = await instance.protocol.dispatch('agents.attach', {'thread_id': 'a'})

        assert result['thread_id'] == 'a' and result['status'] == 'active'
        assert result['active_turn_id'] == 'turn-active' and result['active_root_turn_id'] == 'task-root'
        assert result['attachment_mode'] == 'observe' and result['gateway_managed'] is True
        assert result['launch_turn_id'] == 'original-turn'
        assert result['can_send'] is True
        assert result['children'][0]['thread_id'] == 'b'
        assert result['task_handles'] == [
            {'thread_id': 'a', 'turn_id': 'task-root'},
            {'thread_id': 'a', 'message_id': receipt['id']}]
        assert result['dashboard_url'] == 'http://127.0.0.1:8766/sessions/a'
        assert 'agents_wait' in result['guidance'] and 'agents_send' in result['guidance']
        assert list(instance.db.iterdump()) == before
        assert not any(method in ('thread/start', 'thread/resume', 'turn/start', 'turn/interrupt', 'turn/steer')
                       for method, _ in calls)
    asyncio.run(check())


def test_attach_known_native_thread_does_not_adopt_or_reload_it(tmp_path, monkeypatch):
    async def check():
        instance = service(tmp_path)
        monkeypatch.setattr(instance.history, 'sessions', lambda: [{'id': 'saved-native', 'source': 'cli', 'cwd': str(tmp_path)}])
        calls = []
        original = instance.rpc.call
        async def observed(method, params=None):
            calls.append((method, params))
            return await original(method, params)
        instance.rpc.call = observed

        result = await instance.protocol.dispatch('agents.attach', {'thread_id': 'saved-native'})

        assert result['status'] == 'saved' and result['gateway_managed'] is False
        assert result['active_turn_id'] is None and result['task_handles'] == []
        assert result['can_send'] is True
        assert instance.protocol.agent('saved-native') is None
        assert instance.db.execute('SELECT 1 FROM managed WHERE id=?', ('saved-native',)).fetchone() is None
        assert not any(params and params.get('threadId') == 'saved-native' for _, params in calls)
    asyncio.run(check())


def test_attach_unknown_thread_and_mutating_arguments_are_rejected(tmp_path):
    async def check():
        instance = service(tmp_path)
        with pytest.raises(KeyError, match='Session not found'):
            await instance.protocol.dispatch('agents.attach', {'thread_id': 'unknown'})
        for extra in ({'prompt': 'Start a replacement'}, {'context': {}}, {'parent_id': 'b'}):
            with pytest.raises(ValueError, match='only'):
                await instance.protocol.dispatch('agents.attach', {'thread_id': 'a', **extra})
        assert not instance.rpc.starts
    asyncio.run(check())


@pytest.mark.parametrize('native', [False, True])
def test_attach_tool_requires_only_existing_thread_identity(native):
    schema = next(tool['inputSchema'] for tool in tools(native=native) if tool['name'] == 'agents_attach')
    assert schema['required'] == ['thread_id']
    assert set(schema['properties']) == {'thread_id', 'detail', 'fields'}
    assert schema['additionalProperties'] is False


@pytest.mark.parametrize('modern', [False, True])
def test_mcp_attach_returns_public_dashboard_url_without_launching_a_task(modern):
    async def check():
        calls = []
        async def operation(method, params):
            calls.append((method, params))
            return {'thread_id': 'existing-thread', 'status': 'active', 'attachment_mode': 'observe',
                    'dashboard_url': 'http://127.0.0.1:8766/sessions/existing-thread'}
        bridge = Bridge(operation, dashboard_url='http://codex.localhost:8765')

        params = {'name': 'agents_attach', 'arguments': {'thread_id': 'existing-thread'}}
        if modern:
            params['_meta'] = {'io.modelcontextprotocol/protocolVersion': VERSION,
                               'io.modelcontextprotocol/clientCapabilities': {'extensions': {TASKS: {}}}}
        result = await bridge.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': params})

        data = result['result']['structuredContent']
        assert data['dashboard_url'] == 'http://codex.localhost:8765/sessions/existing-thread'
        assert data['attachment_mode'] == 'observe'
        assert calls == [('agents.attach', {'thread_id': 'existing-thread'})]
        assert 'taskId' not in data
        assert 'taskId' not in result['result']
    asyncio.run(check())
