import asyncio
import uuid

import pytest

from codex_sessions.mcp_tasks import Tasks
from test_smoke import Runtime, service

THREAD, ROOT, CONTINUATION, NEXT = [str(uuid.uuid4()) for _ in range(4)]


class ContinuationRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.sessions = {THREAD: 'active'}
        self.turns = [{'id': CONTINUATION, 'rootTurnId': ROOT, 'status': 'inProgress'},
                      {'id': ROOT, 'rootTurnId': ROOT, 'status': 'interrupted'}]
        self.interrupted = []
        self.item_reads = []

    async def call(self, method, params=None):
        if method == 'thread/turns/list':
            return {'data': [dict(turn) for turn in self.turns[:params['limit']]], 'nextCursor': None}
        if method == 'turn/interrupt':
            self.interrupted.append(params['turnId'])
            next(turn for turn in self.turns if turn['id'] == params['turnId'])['status'] = 'interrupted'
            self.sessions[THREAD] = 'idle'
            return {}
        if method == 'thread/items/list':
            self.item_reads.append(params['turnId'])
            return {'data': [{'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Continuation finished'}}]}
        return await super().call(method, params)


def instance(tmp_path):
    value = service(tmp_path)
    value.rpc = ContinuationRuntime()
    return value


def task_id():
    return f'c1:r:{THREAD}:{ROOT}:1793961478635'


def test_original_task_stays_working_while_a_same_root_continuation_runs(tmp_path):
    async def check():
        value = instance(tmp_path)
        state = await Tasks(value.protocol.dispatch).get(task_id())
        assert state['status'] == 'working'
        assert state['_meta']['codexSessions']['turnId'] == CONTINUATION
    asyncio.run(check())


def test_cancel_interrupts_only_the_active_continuation_of_the_requested_root(tmp_path):
    async def check():
        value = instance(tmp_path)
        await Tasks(value.protocol.dispatch).cancel(task_id())
        assert value.rpc.interrupted == [CONTINUATION]
        assert (await Tasks(value.protocol.dispatch).get(task_id()))['status'] == 'cancelled'
        value.rpc.turns.insert(0, {'id': NEXT, 'rootTurnId': NEXT, 'status': 'inProgress'})
        value.rpc.sessions[THREAD] = 'active'
        await Tasks(value.protocol.dispatch).cancel(task_id())
        assert value.rpc.interrupted == [CONTINUATION]
    asyncio.run(check())


def test_completed_continuation_provides_the_original_tasks_result(tmp_path):
    async def check():
        value = instance(tmp_path); value.rpc.turns[0]['status'] = 'completed'; value.rpc.sessions[THREAD] = 'idle'
        result = await value.protocol.result(THREAD, ROOT)
        assert result['status'] == 'completed' and result['report'] == 'Continuation finished'
        assert result['turn_id'] == CONTINUATION and result['root_turn_id'] == ROOT
        assert value.rpc.item_reads == [CONTINUATION]
    asyncio.run(check())


def test_cancel_intent_interrupts_future_matching_continuations_but_not_new_work(tmp_path):
    async def check():
        value = instance(tmp_path)
        await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': ROOT})
        future = {'id': str(uuid.uuid4()), 'rootTurnId': ROOT, 'status': 'inProgress'}
        value.rpc.turns.insert(0, future); value.rpc.sessions[THREAD] = 'active'
        value.notification({'method': 'turn/started', 'params': {'threadId': THREAD, 'turn': future}})
        await asyncio.gather(*list(value.protocol.turns.jobs))
        assert value.rpc.interrupted == [CONTINUATION, future['id']]
        unrelated = {'id': NEXT, 'rootTurnId': NEXT, 'status': 'inProgress'}
        value.rpc.turns.insert(0, unrelated); value.rpc.sessions[THREAD] = 'active'
        value.notification({'method': 'turn/started', 'params': {'threadId': THREAD, 'turn': unrelated}})
        await asyncio.gather(*list(value.protocol.turns.jobs))
        assert value.rpc.interrupted == [CONTINUATION, future['id']]
    asyncio.run(check())


def test_missing_root_metadata_never_cancels_an_unrelated_active_turn(tmp_path):
    async def check():
        value = instance(tmp_path)
        value.rpc.turns = [{'id': CONTINUATION, 'status': 'inProgress'}, {'id': ROOT, 'status': 'interrupted'}]
        result = await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': ROOT})
        assert result['status'] == 'not_active'
        assert value.rpc.interrupted == []
    asyncio.run(check())


def test_interrupt_acknowledgement_is_not_reported_as_confirmed_cancellation(tmp_path, monkeypatch):
    async def check():
        value = instance(tmp_path)
        original = value.rpc.call
        async def acknowledge_only(method, params=None):
            if method == 'turn/interrupt':
                return {}
            return await original(method, params)
        value.rpc.call = acknowledge_only
        monkeypatch.setattr('codex_sessions.mcp_tasks.CANCEL_TIMEOUT', 0)
        with pytest.raises(TimeoutError, match='not confirmed'):
            await Tasks(value.protocol.dispatch).cancel(task_id())
        state = await Tasks(value.protocol.dispatch).get(task_id())
        assert state['status'] == 'working' and 'Cancellation requested' in state['statusMessage']
        await value.protocol.turns.close()
    asyncio.run(check())


def test_cancellation_guard_survives_a_gateway_restart(tmp_path):
    from codex_sessions.turns import Turns
    async def check():
        value = instance(tmp_path)
        await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': ROOT})
        value.protocol.turns = Turns(value)
        future = {'id': NEXT, 'rootTurnId': ROOT, 'status': 'inProgress'}
        value.rpc.turns.insert(0, future); value.rpc.sessions[THREAD] = 'active'
        value.notification({'method': 'turn/started', 'params': {'threadId': THREAD, 'turn': future}})
        await asyncio.gather(*list(value.protocol.turns.jobs))
        assert value.rpc.interrupted == [CONTINUATION, NEXT]
    asyncio.run(check())


def test_cancelling_a_continuation_handle_follows_its_root_and_rejects_ambiguous_active_segments(tmp_path):
    async def check():
        value = instance(tmp_path)
        value.rpc.turns.insert(0, {'id': NEXT, 'rootTurnId': ROOT, 'status': 'inProgress'})
        with pytest.raises(ValueError, match='Multiple active'):
            await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': CONTINUATION})
        assert value.rpc.interrupted == []
        value.rpc.turns[1]['status'] = 'interrupted'
        await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': CONTINUATION})
        assert value.rpc.interrupted == [NEXT]
    asyncio.run(check())


def test_started_event_interrupts_a_matching_continuation_before_history_catches_up(tmp_path):
    async def check():
        value = instance(tmp_path)
        await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': ROOT})
        future = {'id': NEXT, 'rootTurnId': ROOT, 'status': 'inProgress'}
        value.rpc.turns.insert(0, future); value.rpc.sessions[THREAD] = 'active'
        original = value.rpc.call
        async def stale_history(method, params=None):
            result = await original(method, params)
            if method == 'thread/turns/list':
                result['data'] = [turn for turn in result['data'] if turn['id'] != NEXT]
            return result
        value.rpc.call = stale_history
        value.notification({'method': 'turn/started', 'params': {'threadId': THREAD, 'turn': future}})
        await asyncio.gather(*list(value.protocol.turns.jobs))
        assert value.rpc.interrupted == [CONTINUATION, NEXT]
    asyncio.run(check())


def test_restart_reconciliation_needs_no_ui_or_task_status_poll(tmp_path):
    from codex_sessions.turns import Turns
    async def check():
        value = instance(tmp_path)
        await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': ROOT})
        value.protocol.turns = Turns(value)
        future = {'id': NEXT, 'rootTurnId': ROOT, 'status': 'inProgress'}
        value.rpc.turns.insert(0, future); value.rpc.sessions[THREAD] = 'active'
        await value.protocol.turns.reconcile()
        await asyncio.gather(*list(value.protocol.turns.jobs))
        assert value.rpc.interrupted == [CONTINUATION, NEXT]
    asyncio.run(check())


def test_delayed_start_notification_cannot_interrupt_newer_unrelated_work(tmp_path):
    async def check():
        value = instance(tmp_path)
        await value.protocol.dispatch('agents.cancel', {'thread_id': THREAD, 'turn_id': ROOT})
        stale = {'id': str(uuid.uuid4()), 'rootTurnId': ROOT, 'status': 'inProgress'}
        value.rpc.turns.insert(0, {'id': NEXT, 'rootTurnId': NEXT, 'status': 'inProgress'})
        value.rpc.sessions[THREAD] = 'active'
        value.notification({'method': 'turn/started', 'params': {'threadId': THREAD, 'turn': stale}})
        await asyncio.gather(*list(value.protocol.turns.jobs))
        assert value.rpc.interrupted == [CONTINUATION]
    asyncio.run(check())
