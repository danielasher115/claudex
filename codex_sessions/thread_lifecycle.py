import asyncio
import json
import logging
import time

from .rpc import RpcError
from .context import render
from .worker_policy import DELEGATION_INSTRUCTIONS, isolated_config

STARTUP_TIMEOUT = 120
MISSING_HISTORY_ERROR = 'Agent never became available in daemon history or loaded threads before its startup deadline.'
MISSING_TURN_ERROR = 'Agent never started a turn before its startup deadline.'
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

    async def worker_parameters(self, cwd, overrides=None, developer_instructions=None, context=None):
        result = await self.service.rpc.call('config/read', {'cwd': str(cwd), 'includeLayers': False})
        effective = result.get('config')
        if not isinstance(effective, dict) or not isinstance(effective.get('mcp_servers', {}), dict):
            raise RpcError('Codex did not return a valid MCP configuration for worker isolation')
        config = isolated_config(effective.get('mcp_servers', {}), self.runtime_config(overrides))
        if developer_instructions is None:
            developer_instructions = config.get('developer_instructions', effective.get('developer_instructions'))
        instructions = '\n\n'.join(value for value in (developer_instructions, render(context) if context else None,
                                                        DELEGATION_INSTRUCTIONS) if value)
        return {'config': config, 'developerInstructions': instructions}

    async def resume_parameters(self, session_id, cwd):
        params = {'threadId': session_id, 'excludeTurns': True, 'config': self.runtime_config()}
        agent = self.service.protocol.agent(session_id)
        if agent:
            request = json.loads(agent['request_json'])
            params.update(await self.worker_parameters(cwd, request.get('config'), request.get('developer-instructions'),
                                                       request.get('context')))
        return params

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
            error = MISSING_HISTORY_ERROR
            self.fail(agent['session_id'], error)
        return ('failed', error) if error else ('starting', None)

    def available(self, agent):
        sid = agent['session_id']
        if not agent['turn_id'] or self.failure(sid) not in (MISSING_HISTORY_ERROR, MISSING_TURN_ERROR):
            return
        self.service.db.execute('DELETE FROM agent_failures WHERE session_id=?', (sid,))
        self.service.db.execute("UPDATE launch_requests SET state='running' WHERE session_id=?", (sid,))
        self.service.db.commit()
        self.service.changed(sid)
        self.wake.set()

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
            self.fail(sid, MISSING_TURN_ERROR)

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
