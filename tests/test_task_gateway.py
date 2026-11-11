import asyncio

from test_smoke import message, service


def test_task_cancel_does_not_stop_a_newer_turn_or_cancel_other_queued_messages(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions["a"] = "active"
        queued = await instance.send("a", message())
        original = instance.rpc.call
        interrupted = []
        async def call(method, params=None):
            if method == "turn/interrupt":
                interrupted.append(params)
                return {}
            return await original(method, params)
        instance.rpc.call = call
        result = await instance.protocol.dispatch("agents.cancel", {"thread_id": "a", "turn_id": "old-turn"})
        assert result["status"] == "not_active"
        assert not interrupted
        await instance.protocol.dispatch("agents.cancel", {"thread_id": "a", "turn_id": "turn-active"})
        assert interrupted == [{"threadId": "a", "turnId": "turn-active"}]
        assert instance.db.execute("SELECT status FROM messages WHERE id=?", (queued["id"],)).fetchone()[0] == "queued"
        await instance.protocol.dispatch("agents.cancel", {"thread_id": "a", "message_id": queued["id"]})
        assert instance.db.execute("SELECT status FROM messages WHERE id=?", (queued["id"],)).fetchone()[0] == "cancelled"
    asyncio.run(check())


def test_target_result_paginates_metadata_and_unwraps_only_target_items(tmp_path):
    async def check():
        instance = service(tmp_path)
        original = instance.rpc.call
        calls = []
        async def call(method, params=None):
            calls.append((method, params))
            if method == "thread/turns/list":
                assert params["itemsView"] == "notLoaded"
                return {"data": [{"id": "old", "status": "completed", "completedAt": 1234}], "nextCursor": None} if params.get("cursor") else {"data": [{"id": "new", "status": "inProgress"}], "nextCursor": "earlier"}
            if method == "thread/items/list":
                assert params["turnId"] == "old"
                return {"data": [{"turnId": "old", "item": {"type": "agentMessage", "phase": "final_answer", "text": "Old answer"}}]}
            return await original(method, params)
        instance.rpc.call = call
        result = await instance.protocol.result("a", "old")
        assert result["report"] == "Old answer"
        assert result["completed_at"] == 1234
        assert sum(m == "thread/turns/list" for m, _ in calls) == 2
        assert sum(m == "thread/items/list" for m, _ in calls) == 1
    asyncio.run(check())


def test_transcript_uses_dashboard_history(tmp_path):
    async def check():
        instance = service(tmp_path)
        async def page(sid, cursor):
            assert (sid, cursor) == ("a", "older")
            return {"items": [{"type": "agentMessage", "text": "Recorded"}], "next_cursor": None}
        instance.page = page
        result = await instance.protocol.dispatch("agents.transcript", {"thread_id": "a", "cursor": "older"})
        assert result["items"][0]["text"] == "Recorded"
    asyncio.run(check())
