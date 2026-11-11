import asyncio
import contextlib
import json
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from codex_sessions.history import History
from codex_sessions.rpc import RpcError
from codex_sessions.gateway import Gateway as Service
from codex_sessions.server import create_app


class Runtime:
    error = None
    ws = True
    socket = Path("/test/socket")

    def __init__(self):
        self.sessions = {"a": "idle", "b": "idle"}
        self.starts = []
        self.reject = None

    async def call(self, method, params=None):
        params = params or {}
        if method == "model/list":
            return {"data": [{"id": name, "model": name, "displayName": name, "description": "Test model", "isDefault": name == "gpt-6.1-sol", "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ["low", "medium", "high"]], "defaultReasoningEffort": "medium", "inputModalities": ["text", "image"]} for name in ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"]]}
        if method == "thread/list":
            return {"data": [{"id": i, "status": {"type": status}, "cwd": "/test", "source": "appServer"} for i, status in self.sessions.items()]}
        if method == "thread/loaded/list":
            return {"data": list(self.sessions)}
        if method == "thread/read":
            session_id = params["threadId"]
            return {"thread": {"id": session_id, "status": {"type": self.sessions[session_id]}, "cwd": "/test", "source": "appServer"}}
        if method == "thread/turns/list":
            return {"data": [{"id": "turn-active", "status": "inProgress"}]}
        if method == "thread/items/list":
            raise RpcError("Method not found", -32601)
        if method == "thread/resume":
            if params["threadId"] == "a" and self.reject:
                raise self.reject
            return {}
        if method in ("turn/start", "turn/steer"):
            if self.reject:
                raise self.reject
            self.starts.append((method, params))
            self.sessions[params["threadId"]] = "active"
            return {"turn": {"id": "turn-next"}, "turnId": "turn-active"}
        raise AssertionError(method)

    async def close(self):
        pass


def service(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    instance = Service(home, home / "socket", tmp_path / "data")
    instance.rpc = Runtime()
    return instance


def message(text="hello", mode="queue", **extra):
    return {"text": text, "mode": mode, "request_id": str(uuid.uuid4()), **extra}


async def run_worker(instance):
    task = asyncio.create_task(instance.worker())
    await asyncio.sleep(.03)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_queue_waits_and_steer_targets_the_active_turn(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions["a"] = "active"
        queued = await instance.send("a", message())
        await run_worker(instance)
        assert not instance.rpc.starts
        with pytest.raises(ValueError, match="active turn changed"):
            await instance.send("a", message(mode="steer", expected_turn_id="old-turn"))
        await instance.send("a", message(mode="steer", expected_turn_id="turn-active"))
        assert instance.rpc.starts[0][1]["expectedTurnId"] == "turn-active"
        instance.rpc.sessions["a"] = "idle"
        await run_worker(instance)
        assert instance.db.execute("SELECT status FROM messages WHERE id=?", (queued["id"],)).fetchone()[0] == "delivered"
        assert instance.rpc.starts[-1][0] == "turn/start"
    asyncio.run(check())


def test_broken_queue_does_not_block_other_sessions(tmp_path):
    async def check():
        instance = service(tmp_path)
        first = await instance.send("a", message())
        second = await instance.send("b", message())
        instance.rpc.reject = RpcError("Unknown session", -32602)
        # Only session a's resume rejects; b's actual send is accepted.
        original = instance.rpc.call
        async def call(method, params=None):
            if method == "turn/start":
                instance.rpc.reject = None
            return await original(method, params)
        instance.rpc.call = call
        await run_worker(instance)
        assert instance.db.execute("SELECT status FROM messages WHERE id=?", (first["id"],)).fetchone()[0] == "failed"
        assert instance.db.execute("SELECT status FROM messages WHERE id=?", (second["id"],)).fetchone()[0] == "delivered"
    asyncio.run(check())


def test_idempotent_send_and_ambiguous_ack_are_not_retried(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions["a"] = "active"
        body = message(mode="steer", expected_turn_id="turn-active")
        instance.rpc.reject = RpcError("Connection lost")
        first = await instance.send("a", body)
        assert first["status"] == "uncertain"
        second = await instance.send("a", body)
        assert second["id"] == first["id"]
        assert instance.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        await run_worker(instance)
        assert not instance.rpc.starts
    asyncio.run(check())


def test_foreign_origin_cannot_submit_messages(tmp_path):
    app = create_app(tmp_path, tmp_path / "socket", tmp_path / "data")
    app.state.service.rpc = Runtime()
    with TestClient(app, base_url="http://localhost") as client:
        assert client.post("/api/sessions/a/messages", json=message(), headers={"origin": "https://evil.example"}).status_code == 403
        response = client.post("/api/sessions/a/messages", json=message(), headers={"origin": "http://localhost"})
        assert response.status_code == 200
        assert response.json()["status"] == "queued"


def test_partial_legacy_records_and_unsafe_paths(tmp_path):
    path = tmp_path / "sessions" / "test.jsonl"
    path.parent.mkdir()
    path.write_text('\n'.join(json.dumps(r) for r in [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "hello"}]}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "hello"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "world"}]}},
    ]) + '\n{"incomplete":')
    history = History(tmp_path)
    result = history.page({"rollout_path": str(path)})
    assert len(result["items"]) == 2
    assert history.records({"rollout_path": "/etc/passwd"}) == []
    with pytest.raises(ValueError, match="paginated"):
        history.page({"history_mode": "paginated"})


def test_restart_preserves_queue_and_marks_unacknowledged_send(tmp_path):
    instance = service(tmp_path)
    instance.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", ("one", "a", "hello", "queue", "queued", None, None, 1))
    instance.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", ("two", "a", "hello", "steer", "sending", None, None, 2))
    instance.db.commit()
    instance.db.close()
    resumed = Service(tmp_path / "home", tmp_path / "socket", tmp_path / "data")
    assert dict(resumed.db.execute("SELECT id,status FROM messages")) == {"one": "queued", "two": "uncertain"}
