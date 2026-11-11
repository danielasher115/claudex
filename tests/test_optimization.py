import asyncio
from pathlib import Path
import json

from test_smoke import service, message, run_worker
from codex_sessions.history import History


def test_concurrent_session_requests_share_discovery(tmp_path):
    async def check():
        instance = service(tmp_path)
        original = instance.rpc.call
        calls = 0
        async def call(method, params=None):
            nonlocal calls
            if method == "thread/list":
                calls += 1
                await asyncio.sleep(.01)
            return await original(method, params)
        instance.rpc.call = call
        snapshots = await asyncio.gather(*(instance.sessions() for _ in range(8)))
        assert calls == 1
        assert all(len(s["sessions"]) == 2 for s in snapshots)
    asyncio.run(check())


def test_live_status_refresh_does_not_rescan_the_archive(tmp_path):
    async def check():
        instance = service(tmp_path)
        original = instance.rpc.call
        archive_reads = 0
        async def call(method, params=None):
            nonlocal archive_reads
            if method == "thread/list":
                archive_reads += 1
            return await original(method, params)
        instance.rpc.call = call
        await instance.sessions()
        instance.rpc.sessions["a"] = "active"
        refreshed = await instance.sessions(fresh=True)
        assert next(s for s in refreshed["sessions"] if s["id"] == "a")["status"] == "active"
        assert archive_reads == 1
    asyncio.run(check())


def test_stop_interrupts_exact_turn_and_cancels_queued_followups(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions["a"] = "active"
        queued = await instance.send("a", message())
        original = instance.rpc.call
        stopped = []
        async def call(method, params=None):
            if method == "turn/interrupt":
                stopped.append(params)
                instance.rpc.sessions[params["threadId"]] = "idle"
                return {}
            return await original(method, params)
        instance.rpc.call = call
        result = await instance.stop("a")
        assert result["status"] == "stopped"
        assert stopped == [{"threadId": "a", "turnId": "turn-active"}]
        await run_worker(instance)
        assert not instance.rpc.starts
        assert instance.db.execute("SELECT status FROM messages WHERE id=?", (queued["id"],)).fetchone()[0] == "cancelled"
    asyncio.run(check())


def test_cancelled_queue_is_not_delivered(tmp_path):
    async def check():
        instance = service(tmp_path)
        queued = await instance.send("a", message())
        assert instance.cancel("a", queued["id"])["status"] == "cancelled"
        await run_worker(instance)
        assert not instance.rpc.starts
        assert instance.db.execute("SELECT status FROM messages WHERE id=?", (queued["id"],)).fetchone()[0] == "cancelled"
    asyncio.run(check())


def test_daemon_requests_cannot_consume_outgoing_response_ids(tmp_path):
    from codex_sessions.rpc import Rpc
    async def check():
        class Feed:
            def __aiter__(self):
                return self.read()
            async def read(self):
                yield json.dumps({"id": 1, "method": "approval/request", "params": {"threadId": "a"}})
                yield json.dumps({"id": 1, "result": {"accepted": True}})
        rpc = Rpc(tmp_path, tmp_path / "socket")
        feed = Feed()
        rpc.ws = feed
        future = asyncio.get_running_loop().create_future()
        rpc.pending[1] = future
        await rpc._read(feed)
        assert await future == {"accepted": True}
    asyncio.run(check())


def test_websocket_close_during_send_is_uncertain_and_keeps_worker_alive(tmp_path):
    from codex_sessions.rpc import Rpc
    from websockets.exceptions import ConnectionClosedOK
    async def check():
        class ClosedSocket:
            async def send(self, data):
                raise ConnectionClosedOK(None, None)
        instance = service(tmp_path)
        queued = await instance.send("a", message())
        transport = Rpc(tmp_path, tmp_path / "socket")
        transport.ws = ClosedSocket()
        original = instance.rpc.call
        async def call(method, params=None):
            if method == "turn/start":
                return await transport._request(method, params)
            return await original(method, params)
        instance.rpc.call = call
        await run_worker(instance)
        row = instance.db.execute("SELECT status FROM messages WHERE id=?", (queued["id"],)).fetchone()
        assert row[0] == "uncertain"
    asyncio.run(check())


def test_log_append_keeps_existing_records_and_waits_for_complete_line(tmp_path):
    path = tmp_path / "sessions" / "test.jsonl"
    path.parent.mkdir()
    first = {"type": "event_msg", "payload": {"type": "task_started"}}
    path.write_text(json.dumps(first) + '\n')
    history = History(tmp_path)
    row = {"rollout_path": str(path)}
    initial = history.records(row)
    first_record = initial[0]
    with path.open("a") as out:
        out.write('{"type":"event_msg","payload":')
    assert len(history.records(row)) == 1
    with path.open("a") as out:
        out.write('{"type":"task_complete"}}\n')
    result = history.records(row)
    assert len(result) == 2
    assert result[0] is first_record
    assert not history.external_busy(row)
    path.write_text(json.dumps(first) + '\n')
    assert len(history.records(row)) == 1
    assert history.external_busy(row)


def test_unchanged_history_is_fetched_once(tmp_path):
    async def check():
        instance = service(tmp_path)
        original = instance.rpc.call
        calls = 0
        async def call(method, params=None):
            nonlocal calls
            if method == "thread/turns/list":
                calls += 1
                return {"data": [{"id": "turn", "status": "completed", "items": [{"id": "reply", "type": "agentMessage", "text": "hello"}]}]}
            return await original(method, params)
        instance.rpc.call = call
        first = await instance.page("a")
        second = await instance.page("a")
        assert first["items"][-1]["text"] == "hello"
        assert second == first
        assert calls == 1
    asyncio.run(check())


def test_paginated_revisions_do_not_stat_migrated_rollout_files(tmp_path, monkeypatch):
    history = History(tmp_path)
    stat = Path.stat
    def forbidden(path, *args, **kwargs):
        if str(path) == "/mnt/c/old.jsonl":
            raise AssertionError("Paginated history must not stat migrated rollout files")
        return stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", forbidden)
    first = history.revision({"history_mode": "paginated", "rollout_path": "/mnt/c/old.jsonl", "updated_at_ms": 100})
    assert first == history.revision({"history_mode": "paginated", "updated_at_ms": 100})
    assert first != history.revision({"history_mode": "paginated", "updated_at_ms": 101})


def test_discovery_merges_the_daemon_state_database_and_caches_it(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.history.sessions = lambda: [{"id": key, "history_mode": "paginated", "updated_at_ms": 1} for key in ["a", "b"]]
        original = instance.rpc.call
        calls = []
        async def call(method, params=None):
            if method == "thread/list":
                assert params["useStateDbOnly"] is True
                calls.append(method)
            return await original(method, params)
        instance.rpc.call = call
        assert len((await instance.sessions())["sessions"]) == 2
        assert len((await instance.sessions(fresh=True))["sessions"]) == 2
        assert calls == ["thread/list"]
    asyncio.run(check())


def test_ui_pages_use_bounded_item_history_and_protocol_pages_keep_full_turns(tmp_path):
    async def check():
        instance = service(tmp_path)
        original = instance.rpc.call
        calls = []
        async def call(method, params=None):
            calls.append((method, params))
            if method == "thread/items/list":
                await asyncio.sleep(.01)
                text = "older" if params.get("cursor") else "latest"
                return {"data": [{"turnId": "turn-active", "item": {"id": text, "type": "agentMessage", "text": text}}], "nextCursor": None if params.get("cursor") else "older-items"}
            if method == "thread/turns/list":
                return {"data": [{"id": "turn-active", "status": "completed", "items": [] if params.get("itemsView") == "notLoaded" else [{"id": "complete", "type": "agentMessage", "text": "Complete protocol history"}]}]}
            return await original(method, params)
        instance.rpc.call = call
        pages = await asyncio.gather(*(instance.page("a", compact=True) for _ in range(3)))
        assert pages[0]["items"][-1]["text"] == "latest"
        assert pages[0]["next_cursor"] == "items:older-items"
        assert sum(method == "thread/items/list" for method, _ in calls) == 1
        assert not any(method == "thread/turns/list" and params.get("itemsView") == "full" for method, params in calls)
        assert (await instance.page("a", "items:older-items"))["items"][-1]["text"] == "older"
        assert (await instance.page("a"))["items"][-1]["text"] == "Complete protocol history"
    asyncio.run(check())
