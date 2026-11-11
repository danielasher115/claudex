"""MCP task handles over durable gateway threads, turns and message receipts."""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

TERMINAL = {"completed", "failed", "cancelled"}
CANCEL_TIMEOUT = 5


def tool_result(data, error=False):
    return {"content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data, "isError": error}


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Tasks:
    def __init__(self, operation, dashboard_url="http://127.0.0.1:8766"):
        self.operation = operation
        self.dashboard_url = dashboard_url.rstrip("/")

    @staticmethod
    def decode(task_id):
        try:
            version, kind, thread_id, source_id, created = task_id.split(":")
            if version != "c1" or kind not in ("r", "m") or len(task_id) > 128:
                raise ValueError()
            uuid.UUID(thread_id)
            uuid.UUID(source_id)
            created = int(created) / 1000
            timestamp(created)
            return kind, thread_id, source_id, created
        except (AttributeError, ValueError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid Codex task ID") from exc

    def format(self, task_id, status, message, *, turn_id=None, root_turn_id=None, data=None, receipt=None):
        _, thread_id, _, created = self.decode(task_id)
        url = f"{self.dashboard_url}/sessions/{quote(thread_id, safe='')}"
        meta = {"threadId": thread_id, "turnId": turn_id, "dashboardUrl": url}
        root_turn_id = root_turn_id or (data or {}).get("root_turn_id")
        if root_turn_id:
            meta["rootTurnId"] = root_turn_id
        if receipt is not None:
            mode, delivery_status = receipt.get("mode", "queue"), receipt["status"]
            meta.update(messageId=receipt["id"], deliveryMode=mode, deliveryStatus=delivery_status)
            if delivery_status == "queued":
                message = "Steering queued for delivery to the active turn" if mode == "steer" else "Message queued for the next turn"
            elif delivery_status == "sending":
                message = "Sending steering to the active turn" if mode == "steer" else "Sending the queued message"
            elif delivery_status == "delivered" and turn_id:
                delivery = "Steering delivered to the active turn" if mode == "steer" else "Queued message delivered"
                message = f"{delivery}; {message}"
        result = {"taskId": task_id, "status": status, "statusMessage": message,
                  "createdAt": timestamp(created), "lastUpdatedAt": timestamp(max(data.get("completed_at") or created, created)) if data else timestamp(created),
                  "ttlMs": None, "pollIntervalMs": 2000,
                  "_meta": {"codexSessions": meta}}
        if data is not None:
            value = {**data, "thread_id": thread_id, "threadId": thread_id, "content": data.get("report", ""), "dashboardUrl": url}
            result["result"] = tool_result(value, status in ("failed", "cancelled"))
        if status == "failed":
            error = data.get("error") if data else None
            result["error"] = {"code": -32000, "message": str(error or message)}
        return result

    async def start(self, name, arguments):
        created = int(time.time() * 1000)
        if name in ("codex", "agents_launch"):
            receipt = await self.operation("agents.launch", arguments)
            thread_id, source_id, kind = receipt["thread_id"], receipt.get("turn_id"), "r"
        else:
            arguments = dict(arguments)
            thread_id = arguments.get("thread_id") or arguments.pop("threadId", None) or arguments.pop("conversationId", None)
            arguments.pop("conversationId", None)
            arguments["thread_id"] = thread_id
            if "prompt" in arguments:
                arguments["text"] = arguments.pop("prompt")
            receipt = await self.operation("agents.send", arguments)
            source_id, kind = receipt["id"], "m"
        if not source_id:
            raise ValueError(f"The accepted launch has no confirmed turn. Inspect thread {thread_id} before retrying.")
        task_id = f"c1:{kind}:{thread_id}:{source_id}:{created}"
        self.decode(task_id)
        if receipt.get("status") in ("failed", "uncertain", "cancelled") or (
            kind == "m" and receipt.get("status") not in ("queued", "sending", "delivered")
        ):
            return await self.get(task_id)
        return self.format(task_id, "working", "Codex is working", turn_id=receipt.get("turn_id"),
                           receipt=receipt if kind == "m" else None)

    async def get(self, task_id):
        kind, thread_id, source_id, _ = self.decode(task_id)
        state = await self.operation("agents.status", {"thread_id": thread_id})
        turn_id = source_id
        receipt = None
        if kind == "m":
            receipt = next((m for m in state["messages"] if m["id"] == source_id), None)
            if not receipt:
                raise ValueError("The task's durable message receipt was not found")
            status = receipt["status"]
            if status in ("queued", "sending"):
                return self.format(task_id, "working", "Waiting for message delivery", receipt=receipt)
            if status != "delivered":
                status = "cancelled" if status == "cancelled" else "failed"
                return self.format(task_id, status, receipt.get("error") or f"Message {receipt['status']}",
                                   data={"status": receipt["status"], "error": receipt.get("error"), "message_id": source_id}, receipt=receipt)
            turn_id = receipt.get("turn_id")
            if not turn_id:
                raise ValueError("Delivered message has no confirmed turn; inspect the session before retrying")
        if state["status"] == "active" and state.get("active_turn_id") and turn_id in (state["active_turn_id"], state.get("active_root_turn_id")):
            flags = state.get("flags") or []
            message = "Codex is waiting for its original approval client" if "waitingOnApproval" in flags else "Codex is working"
            if state.get("cancellation_requested"):
                message = "Cancellation requested; waiting for Codex to stop"
                if state.get("cancellation_error"):
                    message += ": " + state["cancellation_error"]
            return self.format(task_id, "working", message, turn_id=state["active_turn_id"], root_turn_id=state.get("active_root_turn_id"), receipt=receipt)
        result = await self.operation("agents.result", {"thread_id": thread_id, "turn_id": turn_id})
        source_status = result["status"]
        turn_id = result.get("turn_id") or turn_id
        if source_status in ("running", "starting"):
            return self.format(task_id, "working", "Waiting for Codex turn state", turn_id=turn_id, root_turn_id=result.get("root_turn_id"), receipt=receipt)
        status = {"completed": "completed", "failed": "failed", "interrupted": "cancelled"}.get(source_status)
        if not status:
            raise ValueError(f"Unknown Codex turn status: {source_status}")
        return self.format(task_id, status, f"Codex turn {source_status}", turn_id=turn_id, data=result, receipt=receipt)

    async def cancel(self, task_id):
        _, thread_id, source_id, _ = self.decode(task_id)
        state = await self.get(task_id)
        if state["status"] in ("completed", "failed"):
            return
        turn_id = state["_meta"]["codexSessions"].get("turnId")
        params = {"thread_id": thread_id, **({"turn_id": turn_id} if turn_id else {"message_id": source_id})}
        try:
            await self.operation("agents.cancel", params)
        except ValueError:
            # Delivery or completion may race cancellation; always keep the original target.
            latest = await self.get(task_id)
            if latest["status"] in TERMINAL:
                return
            delivered_turn = latest["_meta"]["codexSessions"].get("turnId")
            if not turn_id and delivered_turn:
                await self.operation("agents.cancel", {"thread_id": thread_id, "turn_id": delivered_turn})
            else:
                raise
        deadline = time.monotonic() + CANCEL_TIMEOUT
        while True:
            latest = await self.get(task_id)
            if latest["status"] in TERMINAL:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Cancellation was requested but Codex has not confirmed stopping. Keep polling this task; do not assume it stopped.")
            await asyncio.sleep(.1)
