"""Resolve native continuation chains and retain cancellation for their exact roots."""
import asyncio
import sqlite3

from .rpc import RpcError


class Turns:
    def __init__(self, service):
        self.service = service
        self.roots = {}
        self.observed = {}
        self.jobs = set()
        self.pending = set()
        self.interrupted = set()
        self.errors = {}
        self.poll_error = None
        service.db.execute('CREATE TABLE IF NOT EXISTS cancelled_turns (session_id TEXT, root_id TEXT, PRIMARY KEY(session_id, root_id))')
        service.db.commit()

    def root(self, session_id, turn):
        key = (session_id, turn['id'])
        root = turn.get('rootTurnId') or self.roots.get(key) or turn['id']
        self.roots[key] = root
        return root

    def cancelled(self, session_id, root_id):
        return bool(root_id and self.service.db.execute('SELECT 1 FROM cancelled_turns WHERE session_id=? AND root_id=?', (session_id, root_id)).fetchone())

    async def find(self, session_id, target):
        cursor, seen = None, []
        root = self.roots.get((session_id, target))
        while True:
            params = {'threadId': session_id, 'limit': 100, 'itemsView': 'notLoaded', 'sortDirection': 'desc'}
            if cursor:
                params['cursor'] = cursor
            page = await self.service.rpc.call('thread/turns/list', params)
            for turn in page.get('data', []):
                self.root(session_id, turn)
                seen.append(turn)
            anchor = next((turn for turn in seen if turn['id'] == target), None)
            if anchor:
                root = self.root(session_id, anchor)
            elif any(turn.get('rootTurnId') == target for turn in seen):
                root = target
            if root:
                matching = [turn for turn in seen if self.root(session_id, turn) == root]
                active = [turn for turn in matching if turn.get('status') == 'inProgress']
                if len(active) > 1:
                    raise ValueError('Multiple active continuations were reported; cancellation cannot safely choose a turn')
                if matching:
                    return (active[0] if active else matching[0]), root, (active[0] if active else None)
            next_cursor = page.get('nextCursor')
            if not next_cursor:
                return None, root, None
            if next_cursor == cursor:
                raise RpcError('Codex returned a repeated turn-history cursor')
            cursor = next_cursor

    def mark_cancelled(self, session_id, root):
        self.service.db.execute('INSERT OR IGNORE INTO cancelled_turns VALUES (?, ?)', (session_id, root))
        self.service.db.commit()
        self.service.changed(session_id)
        for (thread_id, _), turn in list(self.observed.items()):
            if thread_id == session_id and turn['rootTurnId'] == root:
                self.schedule(session_id, turn['id'])

    async def interrupt_exact(self, session_id, turn_id, root):
        page = await self.service.rpc.call('thread/turns/list', {'threadId': session_id, 'limit': 1, 'itemsView': 'notLoaded', 'sortDirection': 'desc'})
        latest = next(iter(page.get('data', [])), None)
        if latest and latest.get('status') == 'inProgress' and self.root(session_id, latest) != root:
            raise ValueError('The task changed before interruption; unrelated newer work was left running')
        await self.service.rpc.call('turn/interrupt', {'threadId': session_id, 'turnId': turn_id})
        self.interrupted.add((session_id, turn_id))
        self.errors.pop((session_id, root), None)
        self.service.changed(session_id)

    async def cancel(self, session_id, target):
        async with self.service.turn_locks[session_id]:
            turn, root, active = await self.find(session_id, target)
            if turn is None:
                agent = self.service.protocol.agent(session_id)
                if not agent or agent['turn_id'] != target:
                    return {'status': 'not_active', 'turn_id': target}
                root = target
                active = {'id': target}
            self.mark_cancelled(session_id, root)
            if active:
                await self.interrupt_exact(session_id, active['id'], root)
            self.errors.pop((session_id, root), None)
            return {'status': 'cancelling' if active else 'not_active', 'turn_id': target,
                    'root_turn_id': root, 'interrupted_turn_id': active['id'] if active else None}

    def track(self, session_id, turn):
        if not turn.get('id'):
            return
        root = self.root(session_id, turn)
        key = (session_id, turn['id'])
        if turn.get('status') != 'inProgress':
            self.observed.pop(key, None)
            return
        self.observed[key] = {'id': turn['id'], 'rootTurnId': root, 'status': 'inProgress'}
        has_cancelled = self.service.db.execute('SELECT 1 FROM cancelled_turns WHERE session_id=? LIMIT 1', (session_id,)).fetchone()
        if not has_cancelled:
            return
        if not self.cancelled(session_id, root) and turn.get('rootTurnId'):
            return
        self.schedule(session_id, turn['id'])

    def schedule(self, session_id, turn_id):
        key = (session_id, turn_id)
        if key in self.pending or key in self.interrupted:
            return
        self.pending.add(key)
        task = asyncio.create_task(self.enforce(session_id, turn_id))
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

    async def enforce(self, session_id, turn_id):
        try:
            known_root = self.roots.get((session_id, turn_id), turn_id)
            if self.cancelled(session_id, known_root):
                async with self.service.turn_locks[session_id]:
                    if (session_id, turn_id) not in self.interrupted and (session_id, turn_id) in self.observed:
                        await self.interrupt_exact(session_id, turn_id, known_root)
                return
            _, root, active = await self.find(session_id, turn_id)
            if active and self.cancelled(session_id, root):
                await self.cancel(session_id, root)
        except (RpcError, OSError, TimeoutError, ValueError, sqlite3.Error) as exc:
            root = self.roots.get((session_id, turn_id), turn_id)
            self.errors[(session_id, root)] = str(exc)
        finally:
            self.pending.discard((session_id, turn_id))

    def observe(self, message):
        params = message.get('params') or {}
        if message.get('method') in ('turn/started', 'turn/completed') and params.get('threadId') and isinstance(params.get('turn'), dict):
            self.track(params['threadId'], params['turn'])

    async def reconcile(self):
        cancelled_sessions = {row[0] for row in self.service.db.execute('SELECT DISTINCT session_id FROM cancelled_turns')}
        if not cancelled_sessions:
            return
        cursor = None
        while True:
            page = await self.service.rpc.call('thread/loaded/list', {'cursor': cursor})
            for session_id in cancelled_sessions.intersection(page.get('data', [])):
                thread = (await self.service.rpc.call('thread/read', {'threadId': session_id}))['thread']
                if thread.get('status', {}).get('type') == 'active':
                    turns = await self.service.rpc.call('thread/turns/list', {'threadId': session_id, 'limit': 1, 'itemsView': 'notLoaded', 'sortDirection': 'desc'})
                    for turn in turns.get('data', []):
                        self.track(session_id, turn)
            next_cursor = page.get('nextCursor')
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise RpcError('Codex returned a repeated loaded-thread cursor')
            cursor = next_cursor

    async def run(self):
        while True:
            try:
                await self.reconcile()
                self.poll_error = None
            except (RpcError, OSError, TimeoutError, ValueError, sqlite3.Error) as exc:
                self.poll_error = str(exc)
            await asyncio.sleep(1)

    async def close(self):
        for job in list(self.jobs):
            job.cancel()
        if self.jobs:
            await asyncio.gather(*list(self.jobs), return_exceptions=True)
