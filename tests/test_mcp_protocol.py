import asyncio

from test_mcp_tasks import THREAD, TURN, Gateway

from codex_sessions.interfaces import tools
from codex_sessions.mcp import TASKS, VERSION, Bridge


def request(method, params=None, *, modern=True, task_support=True):
    params = dict(params or {})
    if modern:
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": VERSION,
                           "io.modelcontextprotocol/clientCapabilities": {"extensions": {TASKS: {}} if task_support else {}}}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def test_discovery_and_capability_are_per_request_and_empty_extension_opts_in():
    async def check():
        gateway = Gateway()
        bridge = Bridge(gateway)
        discovery = (await bridge.handle(request("server/discover")))["result"]
        assert discovery["supportedVersions"] == [VERSION]
        assert TASKS in discovery["capabilities"]["extensions"]
        native = (await bridge.handle(request("tools/call", {"name": "agents_launch", "arguments": {"prompt": "Work"}})))["result"]
        assert native["resultType"] == "task"
        fetched = (await bridge.handle(request("tasks/get", {"taskId": native["taskId"]})))["result"]
        assert fetched["status"] == "working"
        await bridge.handle(request("tasks/cancel", {"taskId": native["taskId"]}))
        legacy = (await bridge.handle(request("tools/call", {"name": "agents_launch", "arguments": {"prompt": "Work"}}, modern=False)))["result"]
        assert "resultType" not in legacy
        assert legacy["structuredContent"]["thread_id"] == THREAD
        plain = (await bridge.handle(request("tools/call", {"name": "agents_launch", "arguments": {"prompt": "Work"}}, task_support=False)))["result"]
        assert plain["resultType"] == "complete"
        assert plain["structuredContent"]["thread_id"] == THREAD
        assert (await bridge.handle(request("tools/list")))["result"]["tools"]
        old = request("tools/list")
        old["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "future"
        assert (await bridge.handle(old))["error"]["code"] == -32022
    asyncio.run(check())


def test_legacy_codex_waits_and_keeps_compatibility_result():
    async def check():
        gateway = Gateway()
        gateway.active = None
        gateway.results[TURN] = {"status": "completed", "report": "Hello"}
        bridge = Bridge(gateway)
        initialized = (await bridge.handle(request("initialize", {"protocolVersion": "2025-11-25"}, modern=False)))["result"]
        assert initialized["capabilities"] == {"tools": {}, "resources": {}}
        result = (await bridge.handle(request("tools/call", {"name": "codex", "arguments": {"prompt": "Work"}}, modern=False)))["result"]
        assert "resultType" not in result
        assert result["structuredContent"]["threadId"] == THREAD
        assert result["structuredContent"]["content"] == "Hello"
    asyncio.run(check())


def test_shared_tools_preserved_and_transcript_is_not_a_new_privileged_native_tool():
    names = {t["name"] for t in tools()}
    assert {"agents_launch", "agents_send", "agents_children", "models_list", "artifacts_put", "agents_transcript"} <= names
    assert "agents_transcript" not in {t["name"] for t in tools(native=True)}


def test_bundled_skill_can_be_discovered_and_read_without_opening_arbitrary_paths():
    from codex_sessions.guide import SKILL_URI, read_skill
    async def check():
        bridge = Bridge(Gateway())
        listed = (await bridge.handle(request("resources/list", modern=False)))["result"]
        assert listed["resources"][0]["uri"] == SKILL_URI
        loaded = (await bridge.handle(request("resources/read", {"uri": SKILL_URI}, modern=False)))["result"]
        assert loaded["contents"][0]["text"] == read_skill()
        rejected = await bridge.handle(request("resources/read", {"uri": "file:///etc/passwd"}, modern=False))
        assert rejected["error"]["code"] == -32602
    asyncio.run(check())


def test_modern_completion_contains_a_complete_tool_result():
    async def check():
        gateway = Gateway()
        bridge = Bridge(gateway)
        task = (await bridge.handle(request("tools/call", {"name": "agents_launch", "arguments": {"prompt": "Work"}})))["result"]
        gateway.active = None
        gateway.results[TURN] = {"status": "completed", "report": "Done"}
        done = (await bridge.handle(request("tasks/get", {"taskId": task["taskId"]})))["result"]
        assert done["resultType"] == "complete"
        assert done["result"]["resultType"] == "complete"
        assert done["result"]["structuredContent"]["report"] == "Done"
    asyncio.run(check())
