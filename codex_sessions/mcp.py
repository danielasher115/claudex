from .platform import configure_stdio
import asyncio
import json
import sys
import time

from . import __version__
from .client import call, endpoint
from .interfaces import tools
from .mcp_tasks import TERMINAL, Tasks, tool_result
from .guide import read_skill, SKILL_URI

VERSION = "2026-07-28"
TASKS = "io.modelcontextprotocol/tasks"
LEGACY = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
INFO = {"name": "claudex", "version": __version__}
INSTRUCTIONS = (
    "Before delegating, read the bundled codex-agents skill with protocol_guide or resources/read at codex-sessions://skills/codex-agents. "
    "The gateway defaults to High effort unless the user or parent explicitly selects another effort. "
    "Use models_list to choose a Codex worker, then agents_launch to delegate work. "
    "Launches inherit available saved instructions, memory, skill inventories and compatible settings by default. "
    "The installed Claude context hook exports the exact caller's public history automatically; verify caller_session_id and conversation_messages in inherited_context. "
    "Use session_summary for an extra handoff or when the hook is unavailable. After hook installation restart Claude and resume its chat, then reconnect this MCP. "
    "Task-capable clients receive a tracked MCP task; do not launch the same work again while it runs. "
    "Other clients receive an immediate thread_id and can use agents_status, agents_wait and agents_result. "
    "Use agents_transcript for paginated history and agents_children for delegated children. "
    "agents_send steers active agents by default and starts a new turn when idle; use mode=queue explicitly to defer. "
    "The bridge binds an omitted steering target to the observed active turn. Explicit stale targets and steering races fail without falling back to queue. "
    "Use agents_steer with message_id and expected_turn_id to steer an already queued message without cancelling or resending it. "
    "tasks/cancel follows the task's logical root and native continuation segments in that session, or its exact queued message; unrelated later roots remain untouched. "
    "Cancellation waits for observed stopping, not merely an accepted interrupt. agents_stop explicitly stops the agent and its children. "
    "The same sessions appear in the local Claudex dashboard. "
    "Approval requests require the original Codex approval client and are never automatically accepted."
)


class Bridge:
    def __init__(self, operation, notify=None, dashboard_url="http://127.0.0.1:8766"):
        self.operation = operation
        self.notify = notify
        self.tasks = Tasks(operation, dashboard_url)
        self.active_calls = {}
        self.mutations = {}

    async def wait(self, task, progress_token=None):
        started = time.monotonic()
        while True:
            task = await self.tasks.get(task["taskId"])
            if task["status"] in TERMINAL:
                return task["result"]
            if self.notify and progress_token is not None:
                self.notify({"jsonrpc": "2.0", "method": "notifications/progress",
                             "params": {"progressToken": progress_token, "progress": time.monotonic() - started,
                                        "message": task["statusMessage"]}})
            await asyncio.sleep(1)

    async def mutate(self, request_id, name, coroutine):
        mutation = asyncio.create_task(coroutine)
        self.mutations[request_id] = (name, mutation)
        return await asyncio.shield(mutation)

    async def cancel_request(self, request_id):
        mutation = self.mutations.get(request_id)
        task_id = self.active_calls.get(request_id)
        if mutation:
            name, pending = mutation
            receipt = await asyncio.shield(pending)
            task_id = receipt.get("taskId") or task_id
            if not task_id:
                thread_id = receipt.get("thread_id")
                if receipt.get("turn_id"):
                    await self.operation("agents.cancel", {"thread_id": thread_id, "turn_id": receipt["turn_id"]})
                elif name == "agents_send" and receipt.get("id"):
                    await self.operation("agents.cancel", {"thread_id": thread_id, "message_id": receipt["id"]})
                return
        if task_id:
            await self.tasks.cancel(task_id)

    async def handle(self, message):
        request_id = message.get("id")
        if request_id is None:
            return None
        method = message.get("method")
        modern = False
        try:
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise TypeError("Expected object parameters")
            meta = params.get("_meta") or {}
            if not isinstance(meta, dict):
                raise TypeError("Expected object metadata")
            version = meta.get("io.modelcontextprotocol/protocolVersion")
            modern = version is not None or method == "server/discover"
            if version is not None and version != VERSION:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32022, "message": "Unsupported protocol version",
                        "data": {"supported": [VERSION], "requested": version}}}
            if method == "server/discover":
                result = {"resultType": "complete", "supportedVersions": [VERSION],
                          "capabilities": {"tools": {}, "resources": {}, "extensions": {TASKS: {}}},
                          "_meta": {"io.modelcontextprotocol/serverInfo": INFO}, "instructions": INSTRUCTIONS,
                          "ttlMs": 3600000, "cacheScope": "public"}
            elif method == "initialize":
                requested = params.get("protocolVersion")
                result = {"protocolVersion": requested if requested in LEGACY else "2025-11-25",
                          "capabilities": {"tools": {}, "resources": {}}, "serverInfo": INFO, "instructions": INSTRUCTIONS}
                modern = False
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools()}
            elif method == "resources/list":
                result = {"resources": [{"uri": SKILL_URI, "name": "codex-agents", "title": "Codex agent delegation skill", "description": "Claude Code guidance for launching, monitoring and cancelling Codex workers.", "mimeType": "text/markdown"}]}
            elif method == "resources/read":
                if params.get("uri") != SKILL_URI:
                    raise ValueError("Unknown resource URI")
                result = {"contents": [{"uri": SKILL_URI, "mimeType": "text/markdown", "text": read_skill()}]}
            elif method == "tasks/get" and modern:
                result = await self.tasks.get(params.get("taskId"))
            elif method == "tasks/cancel" and modern:
                await self.tasks.cancel(params.get("taskId"))
                result = {}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("Expected object tool arguments")
                if name not in {tool["name"] for tool in tools()}:
                    raise ValueError("Unknown tool")
                capabilities = meta.get("io.modelcontextprotocol/clientCapabilities") or {}
                opted_in = modern and TASKS in capabilities.get("extensions", {})
                if name in ("codex", "codex-reply") or opted_in and name in ("agents_launch", "agents_send"):
                    task = await self.mutate(request_id, name, self.tasks.start(name, arguments))
                    if opted_in:
                        result = {**task, "resultType": "task"}
                    else:
                        self.active_calls[request_id] = task["taskId"]
                        result = await self.wait(task, meta.get("progressToken"))
                else:
                    operation = self.operation(name.replace("_", ".", 1), arguments)
                    data = await self.mutate(request_id, name, operation) if name in ("agents_launch", "agents_send") else await operation
                    result = tool_result(data, isinstance(data, dict) and data.get("status") in ("failed", "interrupted", "uncertain"))
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
            if modern and "resultType" not in result:
                result["resultType"] = "complete"
            if modern and isinstance(result.get("result"), dict):
                result["result"]["resultType"] = "complete"
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:  # noqa: BLE001 - Keep transport errors inside their JSON-RPC response.
            if method == "tools/call":
                result = tool_result({"error": str(exc)}, True)
                if modern:
                    result["resultType"] = "complete"
                return {"jsonrpc": "2.0", "id": request_id, "result": result}
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602 if isinstance(exc, (ValueError, TypeError, KeyError)) else -32603, "message": str(exc)}}
        finally:
            self.active_calls.pop(request_id, None)
            self.mutations.pop(request_id, None)


def run(port, data_dir):
    configure_stdio()
    def emit(value):
        if value is not None:
            print(json.dumps(value), flush=True)

    async def main():
        pending = {}
        cancellations = set()
        async def operation(method, params):
            return await asyncio.to_thread(call, method, params, port, data_dir)
        bridge = Bridge(operation, emit, endpoint(port, data_dir)[1])
        async def handle(message):
            emit(await bridge.handle(message))
        async def cancel(request_id):
            try:
                await bridge.cancel_request(request_id)
            except Exception as exc:  # noqa: BLE001 - Keep transport errors inside their JSON-RPC response.
                print(f"Could not cancel Codex task: {exc}", file=sys.stderr, flush=True)
            task = pending.get(request_id)
            if task:
                task.cancel()
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        continue
                    if message.get("method") == "notifications/cancelled":
                        task = asyncio.create_task(cancel(message.get("params", {}).get("requestId")))
                        cancellations.add(task)
                        task.add_done_callback(cancellations.discard)
                    elif "id" in message:
                        task = asyncio.create_task(handle(message))
                        pending[message["id"]] = task
                        task.add_done_callback(lambda _, rid=message["id"]: pending.pop(rid, None))
                except (ValueError, TypeError):
                    continue
        finally:
            # The daemon owns accepted work; closing stdio only ends local request waiters.
            await asyncio.gather(*list(cancellations), return_exceptions=True)
            remaining = list(pending.values())
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
    asyncio.run(main())
