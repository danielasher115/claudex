from .platform import configure_stdio
import asyncio
import json
import sys
import time
from urllib.parse import quote

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
    "Claudex launches and manages local Codex agents. Read protocol_guide once for the bundled codex-agents skill. "
    "Use models_list for actual model IDs, then agents_launch with a focused prompt/session_summary; default effort is high. "
    "Codex is not a native Claude subagent: keep inherit_context=true for normal delegation so caller rules, memories, skills and "
    "a digest-checked public transcript reference are inherited. Inspect inherited_context enabled/counts/caller identity/warnings. "
    "inherit_context=false deliberately creates an isolated handoff; never use it silently to bypass an export error. "
    "Save thread/turn/message handles or the MCP taskId. Continue parent work and wait on those exact handles with agents_wait "
    "(up to 60 seconds), or tasks/get for an MCP task. Repeated agents_list/status enumeration wastes context. "
    "Status, attach, wait and result use compact summaries by default; final reports are complete. Use fields for exact selected "
    "values or detail=full for commands, patches, queued-message text and inherited source inventories. "
    "Code Mode callers can batch independent read calls, keep handles in JavaScript, wait locally across running responses, "
    "and return only changed states or final reports. Do not print full responses or keep polling Claude for unchanged progress. "
    "Tools return structuredContent and compatible JSON text; errors have isError=true. "
    "Recover lost handles with agents_list (newest first; filter=all for history), then observational agents_attach; do not relaunch accepted work. "
    "agents_send steers active work by default; mode=queue defers. agents_steer sends an existing queued message without resending it. "
    "Use exact turn/message cancellation or tasks/cancel for one task, agents_stop for an agent and descendants. "
    "Approval requests remain with the original native client. Never automatically accept them. "
    "The installed skill/hook settings reload live; reconnect only this MCP through /mcp after server/tool updates, retaining the conversation."
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
                    if name == "agents_attach":
                        data = {**data, "dashboard_url": f"{self.tasks.dashboard_url}/sessions/{quote(data['thread_id'], safe='')}"}
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
