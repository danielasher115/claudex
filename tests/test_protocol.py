import asyncio
import base64
import uuid

import pytest

from test_smoke import Runtime, service


class AgentRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.launched = []
    async def call(self, method, params=None):
        if method == "thread/start":
            sid = f"child-{len(self.launched)}"
            self.launched.append(params)
            self.sessions[sid] = "idle"
            return {"thread": {"id": sid}}
        return await super().call(method, params)


def test_launch_is_async_idempotent_and_supplies_all_attachments(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = AgentRuntime()
        artifact = instance.protocol.artifacts.put({"name": "requirements.txt", "data": base64.b64encode(b"Every requirement here").decode()})
        body = {"prompt": "Implement the requirements", "cwd": str(tmp_path), "attachments": [artifact["id"]], "request_id": str(uuid.uuid4())}
        first = await instance.protocol.dispatch("agents.launch", body)
        second = await instance.protocol.dispatch("agents.launch", body)
        assert first["thread_id"] == second["thread_id"]
        assert len(instance.rpc.launched) == 1
        assert "Every requirement here" in str(instance.rpc.starts[0][1]["input"])
    asyncio.run(check())


def test_children_inherit_limits_and_cannot_elevate_sandbox(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        parent = await instance.protocol.dispatch("agents.launch", {"prompt": "Parent", "cwd": str(tmp_path), "sandbox": "read-only", "limits": {"max_children": 1, "max_depth": 1}})
        with pytest.raises(ValueError, match="sandbox"):
            await instance.protocol.dispatch("agents.launch", {"prompt": "Child", "parent_id": parent["thread_id"], "sandbox": "workspace-write"})
        child = await instance.protocol.dispatch("agents.launch", {"prompt": "Child", "parent_id": parent["thread_id"]})
        children = await instance.protocol.dispatch("agents.children", {"thread_id": parent["thread_id"]})
        assert children[0]["thread_id"] == child["thread_id"]
        assert instance.rpc.launched[1]["sandbox"] == "read-only"
        with pytest.raises(ValueError, match="child limit"):
            await instance.protocol.dispatch("agents.launch", {"prompt": "Another", "parent_id": parent["thread_id"]})
        with pytest.raises(ValueError, match="depth"):
            await instance.protocol.dispatch("agents.launch", {"prompt": "Grandchild", "parent_id": child["thread_id"]})
    asyncio.run(check())


def test_a_child_can_disable_its_own_delegation_without_preventing_its_launch(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        parent = await instance.protocol.launch({"prompt": "Parent", "cwd": str(tmp_path), "limits": {"max_depth": 1}})
        child = await instance.protocol.launch({"prompt": "Leaf", "parent_id": parent["thread_id"], "limits": {"max_depth": 0, "max_children": 0}})
        assert child["parent_id"] == parent["thread_id"]
        with pytest.raises(ValueError, match="depth"):
            await instance.protocol.launch({"prompt": "Not allowed", "parent_id": child["thread_id"]})
    asyncio.run(check())


def test_send_then_wait_does_not_return_the_previous_answer(tmp_path):
    async def check():
        instance = service(tmp_path)
        original = instance.rpc.call
        async def call(method, params=None):
            if method == "thread/turns/list":
                return {"data": [{"id": "previous", "status": "completed", "items": [{"id": "reply", "type": "agentMessage", "text": "Previous answer"}]}]}
            return await original(method, params)
        instance.rpc.call = call
        sent = await instance.protocol.dispatch("agents.send", {"thread_id": "a", "text": "New task"})
        result = await instance.protocol.dispatch("agents.wait", {"thread_id": "a", "timeout_seconds": 0})
        assert result["status"] == "queued"
        assert result["message_id"] == sent["message_id"]
        assert not result["report"]
    asyncio.run(check())


def test_context_opt_out_is_inherited_by_children(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        parent = await instance.protocol.launch({"prompt": "Isolated parent", "cwd": str(tmp_path), "inherit_context": False})
        child = await instance.protocol.launch({"prompt": "Isolated child", "parent_id": parent["thread_id"]})
        assert child["inherited_context"]["instruction_files"] == 0
        assert child["inherited_context"]["memory_files"] == 0
        assert child["inherited_context"]["skills"] == 0
    asyncio.run(check())


def test_exported_context_does_not_gain_server_home_instructions(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        caller = {"provider": "caller", "instructions": [{"source": "caller-only", "text": "Only caller instructions"}], "skills": [], "memories": [], "settings": {}}
        launched = await instance.protocol.launch({"prompt": "Task", "cwd": str(tmp_path), "context": caller})
        assert launched["inherited_context"]["sources"] == ["caller-only"]
        assert launched["inherited_context"]["skills"] == 0
    asyncio.run(check())


def test_gateway_defaults_to_high_but_preserves_explicit_parent_effort(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        launched = await instance.protocol.launch({"prompt": "Default", "cwd": str(tmp_path), "inherit_context": False})
        assert launched["effort"] == "high"
        assert instance.rpc.starts[-1][1]["effort"] == "high"
        parent = await instance.protocol.launch({"prompt": "Fast", "cwd": str(tmp_path), "effort": "low", "inherit_context": False})
        child = await instance.protocol.launch({"prompt": "Child", "parent_id": parent["thread_id"]})
        assert child["effort"] == "low"
        assert instance.rpc.starts[-1][1]["effort"] == "low"
    asyncio.run(check())
