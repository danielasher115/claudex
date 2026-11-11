import asyncio
import sqlite3

import pytest

from codex_sessions.reasoning import Reasoning
from codex_sessions.gateway import Gateway as Service
from codex_sessions.server import create_app
from test_smoke import Runtime, service


def notify(app, method, *, thread="a", turn_id="turn-active", **params):
    app.notification({"method": method, "params": {"threadId": thread, "turnId": turn_id, **params}})


def native_reasoning(identity="r", **extra):
    return {"id": identity, "type": "reasoning", "summary": [], "content": [], **extra}


def turn_source(app, items, **metadata):
    original = app.rpc.call

    async def call(method, params=None):
        if method == "thread/turns/list":
            return {"data": [{"id": "turn-active", "status": "inProgress", "items": items, **metadata}]}
        return await original(method, params)

    app.rpc.call = call


def test_streamed_readable_parts_survive_empty_completion_and_restart(tmp_path):
    async def check():
        app = service(tmp_path)
        native = native_reasoning()
        turn_source(app, [native])
        long_text = "First readable paragraph. " * 300
        notify(app, "item/reasoning/summaryTextDelta", itemId="r", summaryIndex=1, delta="Second part")
        notify(app, "item/reasoning/summaryPartAdded", itemId="r", summaryIndex=0)
        notify(app, "item/reasoning/summaryTextDelta", itemId="r", summaryIndex=0, delta=long_text)
        notify(app, "item/reasoning/textDelta", itemId="r", contentIndex=0, delta="Public native reasoning")
        live = (await app.page("a"))["items"][1]
        assert live["summary"] == [long_text, "Second part"]
        assert live["content"] == ["Public native reasoning"]
        assert native == native_reasoning()
        notify(app, "item/completed", item=native)
        restarted = Service(app.home, app.home / "socket", tmp_path / "data")
        restarted.rpc = app.rpc
        saved = (await restarted.page("a"))["items"][1]
        assert saved["summary"] == [long_text, "Second part"]
        assert saved["content"] == ["Public native reasoning"]
        app.db.close()
        restarted.db.close()

    asyncio.run(check())


def test_projection_respects_native_text_and_isolates_session_turn_and_item(tmp_path):
    async def check():
        app = service(tmp_path)
        turn_source(app, [native_reasoning(summary=["Authoritative native summary"], custom="keep")])
        for thread, turn, identity, text in [
            ("a", "turn-active", "r", "Buffered summary"),
            ("b", "turn-active", "r", "Other session"),
            ("a", "old", "r", "Old turn"),
            ("a", "turn-active", "r2", "Second reasoning item"),
        ]:
            notify(app, "item/reasoning/summaryTextDelta", thread=thread, turn_id=turn, itemId=identity, summaryIndex=0, delta=text)
        items = (await app.page("a"))["items"]
        assert items[1]["summary"] == ["Authoritative native summary"]
        assert items[1]["custom"] == "keep"
        assert items[2]["summary"] == ["Second reasoning item"]
        assert items[2]["id"] == "turn-active:r2"
        app.db.close()

    asyncio.run(check())


def test_native_turn_and_item_timing_reaches_chat_without_losing_item_fields(tmp_path):
    async def check():
        app = service(tmp_path)
        turn_source(app, [], startedAt=100, completedAt=120, durationMs=19500)
        original = app.rpc.call

        async def call(method, params=None):
            if method == "thread/items/list":
                return {"data": [{"turnId": "turn-active", "startedAtMs": 101250, "completedAtMs": 119500,
                                  "item": native_reasoning(custom="keep", durationMs=18250)}]}
            return await original(method, params)

        app.rpc.call = call
        marker, item = (await app.page("a", compact=True))["items"]
        assert (marker["started_at"], marker["completed_at"], marker["duration_ms"]) == (100, 120, 19500)
        assert (item["_started_at"], item["_completed_at"]) == (101.25, 119.5)
        assert item["durationMs"] == 18250
        assert item["custom"] == "keep"
        app.db.close()

    asyncio.run(check())


def test_buffer_pressure_keeps_whole_parts_across_multiple_disk_flushes(tmp_path):
    path = tmp_path / "parts.sqlite"
    with sqlite3.connect(path) as db:
        projection = Reasoning(db, max_items=1, max_buffer_chars=100)
        for identity, delta in [("r", "a" * 80), ("r", "b" * 80), ("r2", "other"), ("r", "c" * 80)]:
            projection.observe({"method": "item/reasoning/summaryTextDelta", "params": {
                "threadId": "a", "turnId": "t", "itemId": identity, "summaryIndex": 0, "delta": delta,
            }})
        projection.flush()
    with sqlite3.connect(path) as db:
        items = Reasoning(db).items("a", "t", [native_reasoning(), native_reasoning("r2")])
        assert items[0]["summary"] == ["a" * 80 + "b" * 80 + "c" * 80]
        assert items[1]["summary"] == ["other"]


def test_tokens_remain_in_memory_until_turn_completion_and_native_completion_wins(tmp_path):
    app = service(tmp_path)
    writes = app.db.total_changes
    for delta in ["Partial", " public", " summary"]:
        notify(app, "item/reasoning/summaryTextDelta", itemId="r", summaryIndex=0, delta=delta)
    assert app.db.total_changes == writes
    notify(app, "item/reasoning/textDelta", itemId="r2", contentIndex=1, delta="Second content part")
    notify(app, "turn/completed", turn={"id": "turn-active", "status": "completed", "items": [
        native_reasoning(summary=["Final native summary"], encryptedContent="never persist this"),
    ]})
    restarted = Service(app.home, app.home / "socket", tmp_path / "data")
    items = restarted.reasoning.items("a", "turn-active", [])
    assert items == [
        {"id": "r", "type": "reasoning", "summary": ["Final native summary"]},
        {"id": "r2", "type": "reasoning", "content": ["", "Second content part"]},
    ]
    assert "never persist this" not in str(list(restarted.db.execute("SELECT text FROM reasoning_parts")))
    app.db.close()
    restarted.db.close()


def test_graceful_app_shutdown_persists_incomplete_readable_reasoning(tmp_path):
    async def check():
        home, data = tmp_path / "home", tmp_path / "data"
        home.mkdir()
        app = create_app(home, home / "socket", data)
        app.state.service.rpc = Runtime()
        async with app.router.lifespan_context(app):
            notify(app.state.service, "item/reasoning/summaryTextDelta", itemId="r", summaryIndex=0, delta="Still working")
        restarted = Service(home, home / "socket", data)
        restarted.rpc = Runtime()
        turn_source(restarted, [native_reasoning()])
        assert (await restarted.page("a"))["items"][1]["summary"] == ["Still working"]
        restarted.db.close()

    asyncio.run(check())


def test_failed_flush_keeps_pending_parts_and_retry_does_not_duplicate_text():
    with sqlite3.connect(":memory:") as db:
        projection = Reasoning(db)
        for index, delta in enumerate(["first", "blocked"]):
            projection.observe({"method": "item/reasoning/summaryTextDelta", "params": {
                "threadId": "a", "turnId": "t", "itemId": "r", "summaryIndex": index, "delta": delta,
            }})
        db.execute("CREATE TRIGGER reject_part BEFORE INSERT ON reasoning_parts WHEN NEW.text='blocked' BEGIN SELECT RAISE(ABORT, 'blocked'); END")
        with pytest.raises(sqlite3.IntegrityError, match="blocked"):
            projection.flush()
        assert projection.items("a", "t", [native_reasoning()])[0]["summary"] == ["first", "blocked"]
        db.execute("DROP TRIGGER reject_part")
        projection.flush()
        assert projection.items("a", "t", [native_reasoning()])[0]["summary"] == ["first", "blocked"]


def test_missing_reasoning_precedes_first_sync_final_without_reordering_native_items():
    with sqlite3.connect(":memory:") as db:
        projection = Reasoning(db)
        projection.observe({"method": "item/reasoning/summaryTextDelta", "params": {
            "threadId": "a", "turnId": "t", "itemId": "missing", "summaryIndex": 0, "delta": "Readable work",
        }})
        native = [
            {"id": "user", "type": "userMessage"},
            {"id": "async", "type": "agentMessage", "phase": "final_answer", "delivery": "async"},
            {"id": "question", "type": "agentMessage", "phase": "final_answer", "questions": [{"title": "Continue?"}]},
            native_reasoning("present", summary=["Existing reasoning"]),
            {"id": "final", "type": "agentMessage", "phase": "final_answer", "delivery": "sync", "questions": []},
            {"id": "second-final", "type": "agentMessage", "phase": "final_answer"},
        ]
        assert [item["id"] for item in projection.items("a", "t", native)] == [
            "user", "async", "question", "present", "missing", "final", "second-final",
        ]
        assert [item["id"] for item in projection.items("a", "t", native[:4])] == [
            "user", "async", "question", "present", "missing",
        ]


def test_compact_pages_only_inject_current_live_reasoning_and_full_pages_retain_history(tmp_path):
    async def check():
        app = service(tmp_path)
        for index in range(99):
            app.reasoning.observe({"method": "item/completed", "params": {
                "threadId": "a", "turnId": "turn-active", "item": native_reasoning(f"r{index}", summary=[f"Saved {index}"]),
            }})
        turn_source(app, [native_reasoning("r0")])
        original = app.rpc.call

        async def call(method, params=None):
            if method == "thread/items/list":
                return {"data": [{"turnId": "turn-active", "item": native_reasoning("r0")}]}
            return await original(method, params)

        app.rpc.call = call
        notify(app, "item/reasoning/summaryTextDelta", itemId="r99", summaryIndex=0, delta="Current live work")
        compact = (await app.page("a", compact=True))["items"][1:]
        assert [(item["id"], item["summary"]) for item in compact] == [
            ("turn-active:r0", ["Saved 0"]), ("turn-active:r99", ["Current live work"]),
        ]
        older = (await app.page("a", cursor="items:older", compact=True))["items"][1:]
        assert [item["id"] for item in older] == ["turn-active:r0"]
        assert len((await app.page("a"))["items"][1:]) == 100
        notify(app, "turn/completed", turn={"id": "turn-active", "status": "completed"})
        turn_source(app, [native_reasoning("r0")], status="completed")
        completed = (await app.page("a", compact=True))["items"][1:]
        assert [item["id"] for item in completed] == ["turn-active:r0"]
        app.db.close()

    asyncio.run(check())


@pytest.mark.parametrize("refresh", ["page", "sessions"])
def test_activity_hydration_uses_captured_readable_text_when_native_history_is_empty(tmp_path, refresh):
    async def check():
        app = service(tmp_path)
        turn_source(app, [native_reasoning()])
        notify(app, "item/reasoning/summaryTextDelta", itemId="r", summaryIndex=0, delta="Inspecting the shutdown handler")
        app.activity.states["a"]["live_at"] = 0
        if refresh == "sessions":
            app.rpc.sessions["a"] = "active"
            snapshot = await app.sessions(fresh=True)
            detail = next(session for session in snapshot["sessions"] if session["id"] == "a")["activity"]["detail"]
        else:
            assert (await app.page("a"))["items"][1]["summary"] == ["Inspecting the shutdown handler"]
            detail = app.activity.snapshot("a", "active", [])["detail"]
        assert detail == "Inspecting the shutdown handler"
        app.db.close()

    asyncio.run(check())
