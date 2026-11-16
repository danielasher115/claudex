import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
import httpx

from codex_sessions.rpc import RpcError
from codex_sessions.server import create_app


def native_usage():
    return {
        'accountId': 'private-account', 'email': 'private@example.com',
        'rateLimits': {'planType': 'pro', 'primary': {'usedPercent': 90}},
        'rateLimitsByLimitId': {
            'codex': {'limitId': 'codex', 'limitName': 'Codex', 'planType': 'pro',
                      'primary': {'usedPercent': 62, 'windowDurationMins': 300, 'resetsAt': 1900000000},
                      'secondary': {'usedPercent': 0, 'windowDurationMins': 10080, 'resetsAt': None},
                      'credits': {'hasCredits': True, 'unlimited': False, 'balance': '12.50', 'secret': 'private-credit'}},
            'review': {'limitName': 'Code review', 'planType': 'pro', 'primary': None, 'secondary': None},
        },
        'rateLimitResetCredits': {'availableCount': 3, 'credits': [
            {'id': 'earned-1', 'title': 'Earned reset', 'expiresAt': None, 'grantedAt': 100,
             'status': 'available', 'resetType': 'codexRateLimits', 'description': 'private-detail'},
            {'id': 'old', 'status': 'redeemed', 'resetType': 'codexRateLimits', 'grantedAt': 99},
        ]},
    }


class UsageRuntime:
    def __init__(self):
        self.payload = native_usage()
        self.calls = []
        self.error = None
        self.outcome = 'reset'
        self.block = None
        self.entered = asyncio.Event()

    async def call(self, method, params=None):
        self.calls.append((method, params))
        if self.error:
            raise self.error
        if self.block:
            self.entered.set()
            await self.block.wait()
        if method == 'account/rateLimits/read':
            return copy.deepcopy(self.payload)
        if method == 'account/rateLimitResetCredit/consume':
            self.payload['rateLimitResetCredits'] = {'availableCount': 2, 'credits': None}
            return {'outcome': self.outcome}
        raise AssertionError(method)


def request(app, method, path, **kwargs):
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:8766') as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(check())


def owner():
    from codex_sessions.account_usage import AccountUsage
    runtime = UsageRuntime()
    return AccountUsage(SimpleNamespace(rpc=runtime)), runtime


def test_usage_route_normalizes_buckets_and_omits_private_native_fields(tmp_path):
    app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
    app.state.service.rpc = UsageRuntime()
    response = request(app, 'GET', '/api/account/usage')
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'available'
    assert result['plan_type'] == 'pro'
    assert isinstance(result['checked_at'], (float, int))
    assert result['limits'] == [
        {'id': 'codex', 'name': 'Codex', 'primary': {'used_percent': 62, 'window_minutes': 300, 'resets_at': 1900000000},
         'secondary': {'used_percent': 0, 'window_minutes': 10080, 'resets_at': None},
         'credits': {'has_credits': True, 'unlimited': False, 'balance': '12.50'}},
        {'id': 'review', 'name': 'Code review', 'primary': None, 'secondary': None, 'credits': None},
    ]
    assert result['reset_credits'] == {'available_count': 3, 'credits': [{'id': 'earned-1', 'title': 'Earned reset', 'expires_at': None}]}
    assert 'private' not in json.dumps(result)
    assert len(app.state.service.rpc.calls) == 1


@pytest.mark.parametrize(('summary', 'expected'), [
    (None, None),
    ({'availableCount': 0, 'credits': None}, {'available_count': 0, 'credits': None}),
    ({'availableCount': 4, 'credits': []}, {'available_count': 4, 'credits': []}),
])
def test_fallback_snapshot_keeps_unknown_and_authoritative_credit_count(summary, expected):
    async def check():
        usage, runtime = owner()
        runtime.payload = {'rateLimits': {'primary': {'usedPercent': 0}}, 'rateLimitsByLimitId': {},
                           'rateLimitResetCredits': summary}
        result = await usage.get()
        assert result['reset_credits'] == expected
        assert result['plan_type'] is None
        assert result['limits'] == [{'id': 'codex', 'name': 'Codex', 'primary': {
            'used_percent': 0, 'window_minutes': None, 'resets_at': None}, 'secondary': None, 'credits': None}]
    asyncio.run(check())


def test_cache_single_flight_refresh_expiry_and_notification_invalidation(monkeypatch):
    async def check():
        usage, runtime = owner()
        clock = [1000]
        monkeypatch.setattr('codex_sessions.account_usage.time.monotonic', lambda: clock[0])
        runtime.block = asyncio.Event()
        reads = [asyncio.create_task(usage.get()) for _ in range(4)]
        await runtime.entered.wait()
        runtime.block.set()
        results = await asyncio.gather(*reads)
        assert all(result == results[0] for result in results)
        assert len(runtime.calls) == 1
        await usage.get()
        assert len(runtime.calls) == 1
        await asyncio.gather(usage.get(refresh=True), usage.get(refresh=True))
        assert len(runtime.calls) == 2
        clock[0] += 61
        await usage.get()
        assert len(runtime.calls) == 3
        usage.observe({'method': 'account/rateLimits/updated', 'params': {'rateLimits': {}}})
        await usage.get()
        assert len(runtime.calls) == 4
        usage.observe({'method': 'account/updated', 'params': {'planType': 'plus'}})
        runtime.error = RpcError('private-account error', -32601)
        result = await usage.get()
        assert result['status'] == 'unavailable'
        assert result['plan_type'] is None and result['limits'] == [] and result['reset_credits'] is None
        assert 'private' not in json.dumps(result)
        assert len(runtime.calls) == 5
    asyncio.run(check())


@pytest.mark.parametrize('failure', [RpcError('private API key only', -32600), RpcError('private unknown method', -32601), OSError('private path'), TimeoutError('private token')])
def test_unavailable_runtime_returns_sanitized_unknown_usage(failure):
    async def check():
        usage, runtime = owner()
        runtime.error = failure
        result = await usage.get()
        assert result['status'] == 'unavailable'
        assert result['reset_credits'] is None and result['limits'] == []
        assert 'private' not in json.dumps(result)
    asyncio.run(check())


@pytest.mark.parametrize('outcome', ['reset', 'alreadyRedeemed', 'nothingToReset', 'noCredit'])
def test_reset_forwards_idempotency_key_and_refreshes_after_every_outcome(outcome):
    async def check():
        usage, runtime = owner()
        before = await usage.get()
        runtime.outcome = outcome
        result = await usage.consume({'idempotency_key': 'same-logical-attempt', 'credit_id': 'earned-1'})
        assert result['outcome'] == outcome
        assert before['reset_credits']['available_count'] == 3
        assert result['usage']['reset_credits']['available_count'] == 2
        assert runtime.calls == [('account/rateLimits/read', {}),
                                 ('account/rateLimitResetCredit/consume', {'idempotencyKey': 'same-logical-attempt', 'creditId': 'earned-1'}),
                                 ('account/rateLimits/read', {})]
    asyncio.run(check())


def test_concurrent_reset_is_rejected_instead_of_queued_for_another_credit():
    async def check():
        usage, runtime = owner()
        from codex_sessions.account_usage import AccountUsageError
        runtime.block = asyncio.Event()
        pending = asyncio.create_task(usage.consume({'idempotency_key': 'first'}))
        await runtime.entered.wait()
        with pytest.raises(AccountUsageError) as error:
            await usage.consume({'idempotency_key': 'second'})
        assert error.value.status == 409
        runtime.block.set()
        await pending
        assert sum(method.endswith('/consume') for method, _ in runtime.calls) == 1
    asyncio.run(check())


@pytest.mark.parametrize('body', [{}, {'idempotency_key': ''}, {'idempotency_key': ' '}, {'idempotency_key': 42},
                                 {'idempotency_key': 'valid', 'credit_id': ''}, {'idempotency_key': 'valid', 'credit_id': None}])
def test_reset_route_validates_before_native_call(tmp_path, body):
    app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
    runtime = app.state.service.rpc = UsageRuntime()
    response = request(app, 'POST', '/api/account/usage/reset', json=body, headers={'origin': 'http://localhost:8766'})
    assert response.status_code == 400
    assert runtime.calls == []


def test_reset_route_rejects_cross_origin_and_sanitizes_native_failure_without_retry(tmp_path):
    app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
    runtime = app.state.service.rpc = UsageRuntime()
    assert request(app, 'POST', '/api/account/usage/reset', json={'idempotency_key': 'attempt'}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert runtime.calls == []
    runtime.error = RpcError('private-account secret-token', -32601)
    response = request(app, 'POST', '/api/account/usage/reset', json={'idempotency_key': 'attempt'}, headers={'origin': 'http://localhost:8766'})
    assert response.status_code == 503
    assert 'private' not in response.text and 'secret' not in response.text
    assert runtime.calls == [('account/rateLimitResetCredit/consume', {'idempotencyKey': 'attempt'})]


def test_continuous_usage_updates_do_not_keep_the_read_pending_forever():
    async def check():
        usage, runtime = owner()
        original = runtime.call
        async def changing(method, params=None):
            result = await original(method, params)
            usage.observe({'method': 'account/rateLimits/updated'})
            await asyncio.sleep(0)
            return result
        runtime.call = changing
        result = await asyncio.wait_for(usage.get(), 0.2)
        assert result['status'] == 'unavailable'
        assert len(runtime.calls) <= 2
    asyncio.run(check())


def test_close_cancels_and_awaits_the_owned_read_task():
    async def check():
        usage, runtime = owner()
        runtime.block = asyncio.Event()
        pending = asyncio.create_task(usage.get())
        await runtime.entered.wait()
        await usage.close()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert usage.pending is None
    asyncio.run(check())


def test_usage_routes_refresh_observed_account_changes_and_return_reset_result(tmp_path):
    async def check():
        app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
        service = app.state.service
        runtime = service.rpc = UsageRuntime()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost:8766') as client:
            first = (await client.get('/api/account/usage')).json()
            runtime.payload['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 11
            assert (await client.get('/api/account/usage')).json() == first
            refreshed = (await client.get('/api/account/usage?refresh=1')).json()
            assert refreshed['limits'][0]['primary']['used_percent'] == 11
            response = await client.post('/api/account/usage/reset', json={'idempotency_key': 'route-attempt'},
                                         headers={'origin': 'http://localhost:8766'})
            assert response.status_code == 200
            assert response.json()['outcome'] == 'reset'
            assert response.json()['usage']['reset_credits']['available_count'] == 2
            service.notification({'method': 'account/updated', 'params': {'planType': 'plus'}})
            runtime.error = RpcError('private-token', -32601)
            unavailable = (await client.get('/api/account/usage')).json()
            assert unavailable['status'] == 'unavailable' and unavailable['plan_type'] is None
        service.db.close()
    asyncio.run(check())


def test_cancelled_usage_waiter_does_not_reuse_finished_read_after_a_reset():
    async def check():
        usage, runtime = owner()
        runtime.block = asyncio.Event()
        request_task = asyncio.create_task(usage.get())
        await runtime.entered.wait()
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task
        runtime.block.set()
        await usage.pending
        result = await usage.consume({'idempotency_key': 'after-cancel'})
        assert result['usage']['reset_credits']['available_count'] == 2
    asyncio.run(check())
