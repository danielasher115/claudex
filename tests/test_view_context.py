import asyncio
import json

import httpx

from codex_sessions.server import create_app
from test_smoke import Runtime


def test_view_uses_recorded_context_tokens_without_runtime_inventory_or_cumulative_fallback(tmp_path):
    async def check():
        app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
        service = app.state.service
        service.rpc = Runtime()
        (tmp_path / 'sessions').mkdir()
        rollout = tmp_path / 'sessions' / 'session.jsonl'
        rollout.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'total_tokens': 900000},
            'last_token_usage': {'total_tokens': 550, 'input_tokens': 500}, 'model_context_window': 128000}}}) + '\n')
        service.history.sessions = lambda: [{'id': 'a', 'rollout_path': str(rollout), 'cwd': '/repo'}]
        original = service.rpc.call
        calls = []
        async def call(method, params=None):
            calls.append(method)
            return await original(method, params)
        service.rpc.call = call
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:8766') as client:
            result = await client.get('/api/sessions/a/view')
            assert result.status_code == 200
            assert result.json()['context_usage'] == {'last': {'total_tokens': 550, 'input_tokens': 500}, 'model_context_window': 128000}
            assert not any(method in calls for method in ('config/read', 'skills/list', 'app/list', 'mcpServerStatus/list'))
            rollout.write_text('')
            service.details.recorded_cache.clear()
            assert (await client.get('/api/sessions/a/view')).json()['context_usage'] is None
        service.db.close()
    asyncio.run(check())


def test_view_never_loads_full_recorded_details_or_history_for_usage(tmp_path):
    from unittest.mock import AsyncMock, Mock

    async def check():
        app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
        service = app.state.service
        service.page = AsyncMock(return_value={'items': [], 'next_cursor': None, 'source': 'codex', 'warning': None})
        service.session = AsyncMock(return_value={'id': 'a'})
        service.details.get = AsyncMock(side_effect=AssertionError('full details must not load'))
        service.details.recorded = Mock(side_effect=AssertionError('full recorded scan must not load'))
        service.history.records = Mock(side_effect=AssertionError('full history must not load'))
        service.history.sessions = lambda: []
        service.details.token_usage['a'] = {'last': {'inputTokens': 450, 'totalTokens': 500},
                                            'total': {'totalTokens': 9000000}, 'modelContextWindow': 1000}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:8766') as client:
            result = await client.get('/api/sessions/a/view')
            assert result.status_code == 200
            assert result.json()['context_usage'] == {'last': {'input_tokens': 450, 'total_tokens': 500},
                                                       'model_context_window': 1000}
        service.db.close()
    asyncio.run(check())


def test_context_usage_reads_only_a_bounded_tail_and_caches_file_signature(tmp_path, monkeypatch):
    from pathlib import Path
    from codex_sessions.context_usage import ContextUsage, TAIL_LIMIT
    from codex_sessions.history import History

    directory = tmp_path / 'sessions'
    directory.mkdir()
    rollout = directory / 'session.jsonl'
    event = lambda count: json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'total_token_usage': {'total_tokens': 9000000}, 'last_token_usage': {'total_tokens': count},
        'model_context_window': 1000}}}).encode() + b'\n'
    rollout.write_bytes(event(1) + b'x' * (TAIL_LIMIT * 3) + b'\n' + event(500))
    history = History(tmp_path)
    history.sessions = lambda: [{'id': 'a', 'rollout_path': str(rollout)}]
    history.records = lambda row: (_ for _ in ()).throw(AssertionError('full history must not load'))
    original = Path.open
    reads = []

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def seek(self, offset):
            return self.stream.seek(offset)
        def read(self, size=-1):
            assert 0 <= size <= TAIL_LIMIT
            chunk = self.stream.read(size)
            reads.append(len(chunk))
            return chunk

    def opened(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return BoundedReader(stream) if path == rollout and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', opened)
    usage = ContextUsage(history, {})
    assert usage.get('a')['last']['total_tokens'] == 500
    assert reads == [TAIL_LIMIT]
    assert usage.get('a')['last']['total_tokens'] == 500
    assert reads == [TAIL_LIMIT]
    with original(rollout, 'ab') as stream:
        stream.write(event(600))
    assert usage.get('a')['last']['total_tokens'] == 600
    assert reads == [TAIL_LIMIT, TAIL_LIMIT]


def test_context_usage_returns_unknown_when_snapshot_is_outside_tail_or_scope(tmp_path):
    from codex_sessions.context_usage import ContextUsage, TAIL_LIMIT
    from codex_sessions.history import History

    directory = tmp_path / 'sessions'
    directory.mkdir()
    rollout = directory / 'session.jsonl'
    rollout.write_bytes(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': {'total_tokens': 500}, 'model_context_window': 1000}}}).encode() + b'\n' + b'x' * (TAIL_LIMIT + 1))
    outside = tmp_path / 'outside.jsonl'
    outside.write_text(rollout.read_text())
    linked = directory / 'linked.jsonl'
    linked.symlink_to(outside)
    history = History(tmp_path)
    history.sessions = lambda: [{'id': name, 'rollout_path': str(path)} for name, path in
                                [('a', rollout), ('outside', outside), ('linked', linked)]]
    usage = ContextUsage(history, {})
    assert usage.get('a') is None
    assert usage.get('outside') is None
    assert usage.get('linked') is None
