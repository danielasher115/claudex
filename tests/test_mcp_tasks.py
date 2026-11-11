import asyncio
import uuid

import pytest

from codex_sessions.mcp_tasks import Tasks

THREAD, TURN, NEXT, MESSAGE = [str(uuid.uuid4()) for _ in range(4)]


class Gateway:
    def __init__(self):
        self.calls = []
        self.active = TURN
        self.messages = []
        self.results = {TURN: {"status": "running", "report": ""}}
        self.offline = False

    async def __call__(self, method, params):
        self.calls.append((method, params))
        if self.offline:
            raise ConnectionError("Daemon disconnected")
        if method == "agents.launch":
            return {"thread_id": THREAD, "turn_id": TURN, "status": "running"}
        if method == "agents.send":
            message = {"id": MESSAGE, "status": "queued", "turn_id": None, "created_at": 10}
            self.messages.append(message)
            return message
        if method == "agents.status":
            return {"thread_id": THREAD, "status": "active" if self.active else "idle", "active_turn_id": self.active, "messages": self.messages}
        if method == "agents.result":
            return {"thread_id": THREAD, "turn_id": params["turn_id"], **self.results[params["turn_id"]]}
        if method == "agents.cancel":
            assert "include_children" not in params
            if params.get("message_id"):
                self.messages[0]["status"] = "cancelled"
            else:
                assert params["turn_id"] == TURN
                self.results[TURN] = {"status": "interrupted", "report": ""}
                if self.active == TURN:
                    self.active = None
            return {"status": "cancelled"}
        raise AssertionError(method)


def test_task_survives_bridge_restart_and_reads_its_own_completed_turn():
    async def check():
        gateway = Gateway()
        task = await Tasks(gateway).start("agents_launch", {"prompt": "work", "files": ["input.txt"]})
        assert task["status"] == "working"
        assert len(task["taskId"]) <= 128
        assert gateway.calls[0][1]["files"] == ["input.txt"]
        assert (await Tasks(gateway).get(task["taskId"]))["status"] == "working"
        assert not any(m == "agents.result" for m, _ in gateway.calls)
        gateway.results[TURN] = {"status": "completed", "report": "Done", "changed_files": [{"path": "a.py"}], "completed_at": 1780000000}
        gateway.active = NEXT
        done = await Tasks(gateway).get(task["taskId"])
        assert done["status"] == "completed"
        assert done["result"]["structuredContent"]["report"] == "Done"
        assert done["result"]["structuredContent"]["changed_files"] == [{"path": "a.py"}]
        assert done["_meta"]["codexSessions"]["turnId"] == TURN
        before = list(gateway.calls)
        await Tasks(gateway).cancel(task["taskId"])
        assert not any(m == "agents.cancel" for m, _ in gateway.calls[len(before):])
        assert done["lastUpdatedAt"] == (await Tasks(gateway).get(task["taskId"]))["lastUpdatedAt"]
    asyncio.run(check())


def test_queued_task_follows_durable_message_receipt_and_can_cancel_before_delivery():
    async def check():
        gateway = Gateway()
        tasks = Tasks(gateway)
        queued = await tasks.start("agents_send", {"thread_id": THREAD, "text": "Next"})
        assert "queued" in queued["statusMessage"].lower()
        assert (await Tasks(gateway).get(queued["taskId"]))["status"] == "working"
        gateway.messages[0].update(status="delivered", turn_id=NEXT)
        gateway.results[NEXT] = {"status": "completed", "report": "Follow-up done"}
        gateway.active = None
        done = await Tasks(gateway).get(queued["taskId"])
        assert done["result"]["structuredContent"]["turn_id"] == NEXT
        gateway.messages[0].update(status="queued", turn_id=None)
        await tasks.cancel(queued["taskId"])
        assert ("agents.cancel", {"thread_id": THREAD, "message_id": MESSAGE}) in gateway.calls
        assert (await tasks.get(queued["taskId"]))["status"] == "cancelled"
    asyncio.run(check())


def test_cancel_targets_exact_turn_and_disconnect_never_becomes_completion():
    async def check():
        gateway = Gateway()
        tasks = Tasks(gateway)
        started = await tasks.start("codex", {"prompt": "work"})
        await tasks.cancel(started["taskId"])
        assert ("agents.cancel", {"thread_id": THREAD, "turn_id": TURN}) in gateway.calls
        assert (await tasks.get(started["taskId"]))["status"] == "cancelled"
        gateway.offline = True
        with pytest.raises(ConnectionError):
            await tasks.get(started["taskId"])
        with pytest.raises(ValueError, match="task"):
            await tasks.get("invalid")
    asyncio.run(check())


def test_uncertain_delivery_is_an_explicit_error_and_is_not_replayed():
    async def check():
        gateway = Gateway()
        task = await Tasks(gateway).start("codex-reply", {"threadId": THREAD, "prompt": "Continue"})
        gateway.messages[0].update(status="uncertain", error="Acknowledgment lost")
        failed = await Tasks(gateway).get(task["taskId"])
        assert failed["status"] == "failed"
        assert "Acknowledgment lost" in failed["error"]["message"]
        assert failed["result"]["isError"]
        assert sum(m == "agents.send" for m, _ in gateway.calls) == 1
    asyncio.run(check())


def test_cancellation_follows_the_receipt_when_queue_delivery_races_it():
    async def check():
        gateway = Gateway()
        async def operation(method, params):
            if method == "agents.cancel" and params.get("message_id"):
                gateway.messages[0].update(status="delivered", turn_id=TURN)
                raise ValueError("Message already delivered")
            return await gateway(method, params)
        task = await Tasks(operation).start("agents_send", {"thread_id": THREAD, "text": "Next"})
        await Tasks(operation).cancel(task["taskId"])
        assert ("agents.cancel", {"thread_id": THREAD, "turn_id": TURN}) in gateway.calls
        assert (await Tasks(operation).get(task["taskId"]))["status"] == "cancelled"
    asyncio.run(check())


@pytest.mark.parametrize("mode,status,words", [
    ("queue", "queued", ("queued", "next turn")),
    ("queue", "sending", ("sending", "queued")),
    ("queue", "delivered", ("queued message delivered",)),
    ("steer", "queued", ("steering", "queued")),
    ("steer", "sending", ("sending", "steering")),
    ("steer", "delivered", ("steering delivered", "active turn")),
])
def test_message_tasks_explain_delivery_at_start_and_after_bridge_restart(mode, status, words):
    async def check():
        gateway = Gateway()

        async def operation(method, params):
            result = await gateway(method, params)
            if method == "agents.send":
                result.update(mode=mode, status=status, turn_id=TURN if status == "delivered" else None)
            return result

        started = await Tasks(operation).start("agents_send", {"thread_id": THREAD, "text": "Guidance", "mode": mode})
        for task in (started, await Tasks(operation).get(started["taskId"])):
            meta = task["_meta"]["codexSessions"]
            assert {key: meta.get(key) for key in ("messageId", "deliveryMode", "deliveryStatus")} == {
                "messageId": MESSAGE, "deliveryMode": mode, "deliveryStatus": status,
            }
            assert all(word in task["statusMessage"].lower() for word in words)
    asyncio.run(check())


@pytest.mark.parametrize("turn_status,active,flags,task_status,words", [
    ("running", True, ["waitingOnApproval"], "working", ("steering delivered", "approval")),
    ("starting", False, [], "working", ("steering delivered", "waiting")),
    ("completed", False, [], "completed", ("steering delivered", "completed")),
    ("failed", False, [], "failed", ("steering delivered", "failed")),
    ("interrupted", False, [], "cancelled", ("steering delivered", "interrupted")),
])
def test_steering_receipt_remains_visible_while_following_its_turn(turn_status, active, flags, task_status, words):
    async def check():
        gateway = Gateway()
        started = await Tasks(gateway).start("agents_send", {"thread_id": THREAD, "text": "Guidance"})
        gateway.messages[0].update(mode="steer", status="delivered", turn_id=TURN)
        gateway.active = TURN if active else NEXT
        gateway.results[TURN] = {"status": turn_status, "report": "Result", "error": "Turn error"}

        async def operation(method, params):
            result = await gateway(method, params)
            if method == "agents.status":
                result["flags"] = flags
            return result

        task = await Tasks(operation).get(started["taskId"])
        assert task["status"] == task_status
        meta = task["_meta"]["codexSessions"]
        assert {key: meta.get(key) for key in ("messageId", "deliveryMode", "deliveryStatus", "turnId")} == {
            "messageId": MESSAGE, "deliveryMode": "steer", "deliveryStatus": "delivered", "turnId": TURN,
        }
        assert all(word in task["statusMessage"].lower() for word in words)
    asyncio.run(check())


@pytest.mark.parametrize("status", ["failed", "uncertain", "cancelled", "unknown"])
def test_unconfirmed_steering_never_claims_delivery(status):
    async def check():
        gateway = Gateway()

        async def operation(method, params):
            result = await gateway(method, params)
            if method == "agents.send":
                result.update(mode="steer", status=status, turn_id=TURN, error="Delivery is not confirmed")
            return result

        started = await Tasks(operation).start("agents_send", {"thread_id": THREAD, "text": "Guidance", "mode": "steer"})
        for task in (started, await Tasks(operation).get(started["taskId"])):
            assert task["status"] == ("cancelled" if status == "cancelled" else "failed")
            meta = task["_meta"]["codexSessions"]
            assert {key: meta.get(key) for key in ("messageId", "deliveryMode", "deliveryStatus")} == {
                "messageId": MESSAGE, "deliveryMode": "steer", "deliveryStatus": status,
            }
            assert "delivered" not in task["statusMessage"].lower()
            assert "accepted" not in task["statusMessage"].lower()
        assert not any(method == "agents.result" for method, _ in gateway.calls)
    asyncio.run(check())


def test_receipts_without_mode_remain_queue_tasks():
    async def check():
        gateway = Gateway()
        started = await Tasks(gateway).start("agents_send", {"thread_id": THREAD, "text": "Next"})
        for task in (started, await Tasks(gateway).get(started["taskId"])):
            assert task["_meta"]["codexSessions"].get("deliveryMode") == "queue"
            assert "queued" in task["statusMessage"].lower()
    asyncio.run(check())
