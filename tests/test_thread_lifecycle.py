import asyncio
import json
import time

from test_smoke import message, service
from codex_sessions.rpc import RpcError


def owned(instance, sid='a', age=300, turn='original'):
    instance.db.execute('INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)',
                        (sid, None, sid, 0, 3, 4, 'read-only', '/test', turn, time.time() - age, '{}'))
    instance.db.commit()


def test_discovery_merges_native_history_even_with_local_sqlite_rows(tmp_path):
    async def check():
        instance = service(tmp_path)
        owned(instance, 'saved-native')
        instance.history.sessions = lambda: [{'id': 'local', 'source': 'appServer'}]
        original = instance.rpc.call
        async def call(method, params=None):
            if method == 'thread/list':
                return {'data': [{'id': 'saved-native', 'cwd': '/test', 'status': {'type': 'notLoaded'}}]}
            return await original(method, params)
        instance.rpc.call = call
        sessions = {r['id']: r for r in (await instance.sessions())['sessions']}
        assert sessions['saved-native']['status'] == 'saved'
        assert sessions['local']['status'] == 'saved'
    asyncio.run(check())


def test_missing_old_start_is_failed_but_recent_and_offline_are_not(tmp_path):
    async def check():
        instance = service(tmp_path)
        owned(instance, 'old')
        owned(instance, 'recent', age=1, turn=None)
        rows = {r['id']: r for r in (await instance.sessions())['sessions']}
        assert rows['old']['status'] == 'failed'
        assert 'never became available' in rows['old']['reason']
        assert rows['recent']['status'] == 'starting'
        async def offline(*args, **kwargs):
            raise RpcError('offline')
        instance.rpc.call = offline
        assert next(r for r in (await instance.sessions(fresh=True))['sessions'] if r['id'] == 'recent')['status'] == 'unavailable'
        result = await instance.protocol.result('old')
        assert result['status'] == 'failed'
    asyncio.run(check())


def test_only_idle_owned_without_pending_work_are_unsubscribed_and_can_reload(tmp_path):
    async def check():
        instance = service(tmp_path)
        owned(instance)
        calls = []
        original = instance.rpc.call
        async def call(method, params=None):
            calls.append((method, params))
            if method == 'thread/unsubscribe':
                return {'status': 'unsubscribed'}
            return await original(method, params)
        instance.rpc.call = call
        await instance.thread_lifecycle.reconcile()
        assert [p['threadId'] for m, p in calls if m == 'thread/unsubscribe'] == ['a']
        calls.clear()
        await instance.thread_lifecycle.reconcile()
        assert not any(m == 'thread/unsubscribe' for m, _ in calls)
        queued = await instance.send('a', message())
        await instance.start_queued(instance.db.execute('SELECT * FROM messages WHERE id=?', (queued['id'],)).fetchone())
        assert any(m == 'thread/resume' for m, _ in calls)
        assert any(m == 'turn/start' for m, _ in calls)
        calls.clear()
        await instance.thread_lifecycle.reconcile()
        assert not any(m == 'thread/unsubscribe' for m, _ in calls)
        instance.rpc.sessions['a'] = 'idle'
        await instance.thread_lifecycle.reconcile()
        assert [p['threadId'] for m, p in calls if m == 'thread/unsubscribe'] == ['a']
    asyncio.run(check())


def test_queue_and_inflight_launch_protect_idle_thread(tmp_path):
    async def check():
        instance = service(tmp_path)
        owned(instance)
        calls=[]
        original=instance.rpc.call
        async def call(method, params=None):
            calls.append(method)
            if method == 'thread/unsubscribe':
                return {'status':'unsubscribed'}
            return await original(method,params)
        instance.rpc.call=call
        queued=await instance.send('a',message())
        await instance.thread_lifecycle.reconcile()
        assert 'thread/unsubscribe' not in calls
        instance.cancel('a',queued['id'])
        async with instance.protocol.launch_lock:
            task=asyncio.create_task(instance.thread_lifecycle.reconcile())
            await asyncio.sleep(0)
            assert not task.done()
            assert 'thread/unsubscribe' not in calls
        await task
        assert 'thread/unsubscribe' in calls
    asyncio.run(check())


def test_failed_turn_start_is_terminal_and_released(tmp_path):
    from test_protocol import AgentRuntime
    async def check():
        instance=service(tmp_path)
        instance.rpc=AgentRuntime()
        original=instance.rpc.call
        released=[]
        async def call(method, params=None):
            if method == 'turn/start':
                raise RpcError('Invalid model request', -32602)
            if method == 'thread/unsubscribe':
                released.append(params['threadId'])
                return {'status':'unsubscribed'}
            return await original(method,params)
        instance.rpc.call=call
        try:
            await instance.protocol.launch({'prompt':'Fail before generation','cwd':str(tmp_path),'inherit_context':False})
        except RpcError:
            pass
        else:
            raise AssertionError('Launch unexpectedly succeeded')
        result=await instance.protocol.result('child-0')
        assert result['status']=='failed'
        assert 'Invalid model request' in result['error']
        assert (await instance.session('child-0'))['status']=='failed'
        await instance.thread_lifecycle.reconcile()
        assert released==['child-0']
    asyncio.run(check())


def test_never_started_loaded_thread_expires_without_a_client_poll(tmp_path):
    async def check():
        instance=service(tmp_path)
        owned(instance, turn=None)
        original=instance.rpc.call
        async def call(method,params=None):
            if method=='thread/turns/list':
                return {'data':[]}
            if method=='thread/unsubscribe':
                return {'status':'unsubscribed'}
            return await original(method,params)
        instance.rpc.call=call
        await instance.thread_lifecycle.reconcile()
        assert instance.thread_lifecycle.failure('a')
        assert (await instance.protocol.result('a'))['status']=='failed'
    asyncio.run(check())


def test_bad_thread_does_not_block_other_idle_releases_or_mark_everyone_offline(tmp_path):
    async def check():
        instance=service(tmp_path)
        owned(instance,'a')
        owned(instance,'b')
        released=[]
        original=instance.rpc.call
        async def call(method,params=None):
            if method=='thread/read' and params['threadId']=='a':
                raise RpcError('Thread no longer exists',-32602)
            if method=='thread/unsubscribe':
                released.append(params['threadId'])
                return {'status':'unsubscribed'}
            return await original(method,params)
        instance.rpc.call=call
        await instance.thread_lifecycle.reconcile()
        assert released==['b']
        snapshot=await instance.sessions()
        assert snapshot['connection']['connected']
        rows={r['id']:r for r in snapshot['sessions']}
        assert rows['a']['status']=='unavailable'
        assert not rows['a']['can_send']
        assert rows['b']['status']=='idle'
        assert rows['b']['can_send']
    asyncio.run(check())


def test_saved_unstarted_thread_expires_and_lost_ack_is_recovered(tmp_path):
    async def check():
        instance=service(tmp_path)
        owned(instance,'empty',turn=None)
        owned(instance,'accepted',turn=None)
        original=instance.rpc.call
        async def call(method,params=None):
            if method=='thread/turns/list':
                return {'data':[{'id':'persisted','status':'completed'}] if params['threadId']=='accepted' else []}
            return await original(method,params)
        instance.rpc.call=call
        await instance.thread_lifecycle.reconcile()
        assert instance.thread_lifecycle.failure('empty')
        assert (await instance.protocol.result('empty'))['status']=='failed'
        assert not instance.thread_lifecycle.failure('accepted')
        assert instance.protocol.agent('accepted')['turn_id']=='persisted'
    asyncio.run(check())
