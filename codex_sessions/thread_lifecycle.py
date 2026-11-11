import asyncio
import logging
import time

from .rpc import RpcError

STARTUP_TIMEOUT = 120
log = logging.getLogger(__name__)


class ThreadLifecycle:
    def __init__(self, service):
        self.service = service
        self.released = set()
        self.connection = None
        self.wake = asyncio.Event()
        service.db.execute('CREATE TABLE IF NOT EXISTS agent_failures (session_id TEXT PRIMARY KEY, error TEXT NOT NULL)')
        service.db.commit()

    @staticmethod
    def runtime_config(overrides=None):
        return {"thread_unload_delay_secs": 1, **(overrides or {})}

    def failure(self, session_id):
        row = self.service.db.execute('SELECT error FROM agent_failures WHERE session_id=?', (session_id,)).fetchone()
        return row[0] if row else None

    def fail(self, session_id, error):
        self.service.db.execute('INSERT OR REPLACE INTO agent_failures VALUES (?,?)', (session_id, error))
        self.service.db.execute("UPDATE launch_requests SET state='failed' WHERE session_id=?", (session_id,))
        self.service.db.commit()
        self.service.changed(session_id)
        self.wake.set()

    def missing(self, agent):
        error = self.failure(agent['session_id'])
        if not error and time.time() - agent['created_at'] >= STARTUP_TIMEOUT:
            error = 'Agent never became available in daemon history or loaded threads before its startup deadline.'
            self.fail(agent['session_id'], error)
        return ('failed', error) if error else ('starting', None)

    def acquired(self, session_id):
        self.released.discard(session_id)
        self.service.db.execute('DELETE FROM agent_failures WHERE session_id=?', (session_id,))
        self.service.db.commit()
        self.wake.set()

    def observe(self, message):
        if message.get('method') in ('turn/completed', 'thread/status/changed', 'thread/closed'):
            self.wake.set()

    async def settle_start(self, sid, agent):
        if agent['turn_id'] or self.failure(sid) or time.time() - agent['created_at'] < STARTUP_TIMEOUT:
            return
        try:
            turns = await self.service.rpc.call('thread/turns/list', {'threadId': sid, 'limit': 1, 'itemsView': 'notLoaded'})
        except RpcError as exc:
            if not any(text in str(exc).lower() for text in ('not found', 'is empty', 'not materialized')):
                raise
            turns = {}
        if turns.get('data'):
            self.service.db.execute('UPDATE agents SET turn_id=? WHERE session_id=?', (turns['data'][0]['id'], sid))
            self.service.db.execute("UPDATE launch_requests SET state='running' WHERE session_id=?", (sid,))
            self.service.db.commit()
        else:
            self.fail(sid, 'Agent never started a turn before its startup deadline.')

    async def release(self, sid, agent, loaded):
        service = self.service
        async with service.turn_locks[sid]:
            pending = service.db.execute("SELECT 1 FROM messages WHERE session_id=? AND status IN ('queued','sending') LIMIT 1", (sid,)).fetchone()
            if pending:
                return
            if not loaded:
                await self.settle_start(sid, agent)
                return
            if not agent['turn_id'] and not self.failure(sid) and time.time() - agent['created_at'] < STARTUP_TIMEOUT:
                return
            thread = (await service.rpc.call('thread/read', {'threadId': sid}))['thread']
            if thread.get('status', {}).get('type') != 'idle':
                return
            await self.settle_start(sid, agent)
            result = await service.rpc.call('thread/unsubscribe', {'threadId': sid})
            if result.get('status') not in ('unsubscribed', 'notSubscribed', 'notLoaded'):
                raise RpcError('Codex did not acknowledge thread/unsubscribe')
            self.released.add(sid)
            service.changed(sid)

    async def reconcile(self):
        service = self.service
        # Launch and resume hold these locks through turn/start, including its acknowledgement gap.
        async with service.protocol.launch_lock:
            loaded, cursor = set(), None
            while True:
                page = await service.rpc.call('thread/loaded/list', {'cursor': cursor})
                loaded.update(page.get('data', []))
                next_cursor = page.get('nextCursor')
                if not next_cursor:
                    break
                if next_cursor == cursor:
                    raise RpcError('Codex returned a repeated loaded-thread cursor')
                cursor = next_cursor
            if self.connection is not service.rpc.ws:
                self.released.clear()
                self.connection = service.rpc.ws
            self.released.intersection_update(loaded)
            agents = {r['session_id']: dict(r) for r in service.db.execute('SELECT * FROM agents')}
            for sid, agent in agents.items():
                if sid in self.released or sid not in loaded and agent['turn_id']:
                    continue
                try:
                    await self.release(sid, agent, sid in loaded)
                except (RpcError, OSError, TimeoutError) as exc:
                    log.warning('Thread lifecycle reconciliation failed for %s: %s', sid, exc)

    async def run(self):
        while True:
            self.wake.clear()
            try:
                await self.service.sessions(fresh=True)
                await self.reconcile()
            except (RpcError, OSError, TimeoutError) as exc:
                log.warning('Idle thread release failed: %s', exc)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=15)
            except TimeoutError:
                pass
