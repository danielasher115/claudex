import asyncio

from starlette.testclient import TestClient

from codex_sessions.rpc import RpcError
from codex_sessions.server import create_app
from test_smoke import Runtime, service


def test_native_background_shells_are_paginated_and_scoped_to_the_selected_session(tmp_path):
    async def check():
        app = service(tmp_path)
        original = app.rpc.call
        calls = []
        async def call(method, params=None):
            if method == 'thread/backgroundTerminals/list':
                calls.append(params)
                if not params.get('cursor'):
                    return {'data': [{'processId': 'shell-1', 'itemId': 'cmd-1', 'command': 'sleep 600', 'cwd': '/repo',
                                      'osPid': 123, 'cpuPercent': 0.0, 'rssKb': 2048, 'unrelated': 'not public'}], 'nextCursor': 'next'}
                return {'data': [{'processId': 'shell-2', 'itemId': 'cmd-2', 'command': 'npm run dev', 'cwd': '/repo'}]}
            return await original(method, params)
        app.rpc.call = call
        result = await app.background_shells.get('a')
        assert result['status'] == 'available'
        assert [item['processId'] for item in result['items']] == ['shell-1', 'shell-2']
        assert 'unrelated' not in result['items'][0]
        assert result['items'][0]['cpuPercent'] == 0
        assert [params['threadId'] for params in calls] == ['a', 'a']
        assert calls[1]['cursor'] == 'next'
        assert not result['partial']
        app.db.close()
    asyncio.run(check())


def test_native_failure_is_unavailable_instead_of_an_empty_running_inventory(tmp_path):
    async def check():
        app = service(tmp_path)
        original = app.rpc.call
        async def call(method, params=None):
            if method == 'thread/backgroundTerminals/list':
                raise RpcError('Internal detail TOKEN=do-not-show', -32601)
            return await original(method, params)
        app.rpc.call = call
        result = await app.background_shells.get('a')
        assert result['status'] == 'unavailable'
        assert 'TOKEN' not in str(result)
        assert 'runtime' in result['message'].lower()
        app.db.close()
    asyncio.run(check())


def test_repeated_native_cursor_is_bounded_and_reported_as_partial(tmp_path):
    async def check():
        app = service(tmp_path)
        original = app.rpc.call
        calls = []
        async def call(method, params=None):
            if method == 'thread/backgroundTerminals/list':
                calls.append(params)
                return {'data': [{'processId': 'shell', 'itemId': 'cmd', 'command': 'sleep 600', 'cwd': '/repo'}], 'nextCursor': 'repeated'}
            return await original(method, params)
        app.rpc.call = call
        result = await app.background_shells.get('a')
        assert result['partial']
        assert len(result['items']) == 1
        assert len(calls) == 2
        app.db.close()
    asyncio.run(check())


def test_background_shell_route_is_read_only_and_unknown_sessions_are_not_found(tmp_path):
    app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
    service = app.state.service
    service.rpc = Runtime()
    original = service.rpc.call
    calls = []
    async def call(method, params=None):
        calls.append(method)
        if method == 'thread/backgroundTerminals/list':
            return {'data': []}
        return await original(method, params)
    service.rpc.call = call
    with TestClient(app, base_url='http://localhost:8766') as client:
        result = client.get('/api/sessions/a/background-shells')
        assert result.status_code == 200
        assert result.json()['status'] == 'available'
        assert result.json()['items'] == []
        assert client.get('/api/sessions/missing/background-shells').status_code == 404
        assert not any(method in calls for method in ('thread/resume', 'turn/start', 'thread/backgroundTerminals/terminate'))
