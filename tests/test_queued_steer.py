import asyncio
import base64

import pytest
from starlette.testclient import TestClient

from codex_sessions.rpc import RpcError
from codex_sessions.server import create_app
from test_smoke import Runtime, message, run_worker, service


def test_steering_a_queued_message_keeps_its_identity_and_attachments_and_is_idempotent(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions["a"] = "active"
        artifact = instance.protocol.artifacts.put({"name": "notes.txt", "data": base64.b64encode(b"Read these details").decode()})
        queued = await instance.send("a", message("Use these details now", attachments=[artifact["id"]]))
        arguments = {"thread_id": "a", "message_id": queued["id"], "expected_turn_id": "turn-active"}
        first = await instance.protocol.dispatch("agents.steer", arguments)
        second = await instance.protocol.dispatch("agents.steer", arguments)
        assert first == second
        assert first["message_id"] == queued["id"]
        assert first["mode"] == "steer" and first["status"] == "delivered"
        assert instance.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert len(instance.rpc.starts) == 1
        method, params = instance.rpc.starts[0]
        assert method == "turn/steer" and params["expectedTurnId"] == "turn-active"
        assert "Read these details" in str(params["input"])
        await run_worker(instance)
        assert len(instance.rpc.starts) == 1
    asyncio.run(check())


@pytest.mark.parametrize("expected,state", [("old-turn", "active"), ("turn-active", "idle")])
def test_a_stale_steering_target_leaves_the_original_queue_untouched(tmp_path, expected, state):
    async def check():
        instance = service(tmp_path)
        queued = await instance.send("a", message())
        instance.rpc.sessions["a"] = state
        with pytest.raises(ValueError, match="turn"):
            await instance.steer_queued("a", queued["id"], expected)
        row = dict(instance.db.execute("SELECT * FROM messages").fetchone())
        assert row["mode"] == "queue" and row["status"] == "queued"
        assert not instance.rpc.starts
    asyncio.run(check())


@pytest.mark.parametrize("status", ["cancelled", "sending", "delivered", "failed"])
def test_only_a_still_queued_message_can_be_promoted(tmp_path, status):
    async def check():
        instance = service(tmp_path)
        queued = await instance.send("a", message())
        instance.db.execute("UPDATE messages SET status=?", (status,)); instance.db.commit()
        with pytest.raises(ValueError, match="queued"):
            await instance.steer_queued("a", queued["id"], "turn-active")
        assert not instance.rpc.starts
    asyncio.run(check())


def test_cancellation_during_target_refresh_wins_without_sending(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions["a"] = "active"
        queued = await instance.send("a", message())
        original = instance.session
        async def refresh(*args, **kwargs):
            result = await original(*args, **kwargs)
            instance.cancel("a", queued["id"])
            return result
        instance.session = refresh
        with pytest.raises(ValueError, match="queued"):
            await instance.steer_queued("a", queued["id"], "turn-active")
        assert not instance.rpc.starts
        assert instance.db.execute("SELECT status FROM messages").fetchone()[0] == "cancelled"
    asyncio.run(check())


def test_rejected_steering_never_falls_back_to_a_new_turn(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc.sessions["a"] = "active"
        queued = await instance.send("a", message())
        instance.rpc.reject = RpcError("Turn finished", -32000)
        result = await instance.steer_queued("a", queued["id"], "turn-active")
        assert result["status"] == "failed" and result["error"] == "Turn finished"
        instance.rpc.sessions["a"] = "idle"
        await run_worker(instance)
        assert not instance.rpc.starts
    asyncio.run(check())


def test_ui_endpoint_steers_the_existing_message(tmp_path):
    app = create_app(tmp_path, tmp_path / "socket", tmp_path / "data")
    instance = app.state.service
    instance.rpc = Runtime(); instance.rpc.sessions["a"] = "active"
    queued = asyncio.run(instance.send("a", message()))
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(f"/api/sessions/a/messages/{queued['id']}/steer", json={"expected_turn_id": "turn-active"}, headers={"Origin": "http://localhost"})
        assert response.status_code == 200
        assert response.json()["id"] == queued["id"]
        assert response.json()["mode"] == "steer" and response.json()["status"] == "delivered"


def test_native_agents_cannot_steer_messages_outside_their_delegation_tree(tmp_path):
    from test_protocol import AgentRuntime
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        caller = await instance.protocol.launch({"prompt": "Caller", "cwd": str(tmp_path), "inherit_context": False})
        with pytest.raises(ValueError, match="only itself and its children"):
            await instance.protocol.native_call({"method": "item/tool/call", "params": {"threadId": caller["thread_id"], "tool": "agents_steer", "arguments": {"thread_id": "unrelated", "message_id": "other-message", "expected_turn_id": "turn-active"}}})
    asyncio.run(check())


def test_mcp_promotion_updates_the_existing_task_handle_without_resending():
    from test_mcp_tasks import Gateway, THREAD, TURN, MESSAGE
    from test_mcp_protocol import request
    from codex_sessions.mcp import Bridge
    async def check():
        gateway = Gateway()
        async def operation(method, params):
            if method == "agents.steer":
                receipt = gateway.messages[0]
                assert params == {"thread_id": THREAD, "message_id": MESSAGE, "expected_turn_id": TURN}
                receipt.update(mode="steer", status="delivered", turn_id=TURN)
                return {**receipt, "message_id": MESSAGE, "thread_id": THREAD}
            return await gateway(method, params)
        bridge = Bridge(operation)
        task = await bridge.tasks.start("agents_send", {"thread_id": THREAD, "text": "Use this now"})
        result = (await bridge.handle(request("tools/call", {"name": "agents_steer", "arguments": {"thread_id": THREAD, "message_id": MESSAGE, "expected_turn_id": TURN}})))["result"]
        assert not result["isError"] and result["structuredContent"]["message_id"] == MESSAGE
        polled = await bridge.tasks.get(task["taskId"])
        assert polled["_meta"]["codexSessions"]["deliveryMode"] == "steer"
        assert "Steering delivered" in polled["statusMessage"]
        assert sum(method == "agents.send" for method, _ in gateway.calls) == 1
    asyncio.run(check())
