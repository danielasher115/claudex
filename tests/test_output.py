import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from codex_sessions.protocol import Protocol


def large_result():
    return {
        "thread_id": "worker", "turn_id": "turn", "root_turn_id": "root", "status": "running", "error": None,
        "report": "Complete report", "structured_result": {"answer": 42}, "started_at": 1, "completed_at": None,
        "inherited_context": {"enabled": True, "provider": "caller", "instruction_files": 2, "memory_files": 1,
                              "skills": 70, "caller_session_id": "caller", "conversation_sha256": "digest", "export_id": "export",
                              "warnings": ["A source could not be read"], "sources": ["source" * 20000],
                              "settings": ["setting" * 20000], "settings_sources": ["settings" * 20000],
                              "memory_roots": ["memory" * 20000]},
        "messages": [{"id": "message", "mode": "queue", "status": "queued", "error": None, "turn_id": None,
                      "created_at": 1, "text": "Follow-up " * 20000}],
        "commands": [{"command": "command" * 20000, "exitCode": 0, "status": "completed"}],
        "changed_files": [{"path": "a.py", "kind": "update", "move_path": "b.py", "diff": "patch" * 20000}],
        "workspace_changes": {"source": "git", "warning": "An unreadable file was omitted", "diff": "patch" * 20000},
    }


def protocol_with(data):
    protocol = Protocol.__new__(Protocol)
    for name in ("status", "result", "attach"):
        setattr(protocol, name, AsyncMock(side_effect=lambda *args: copy.deepcopy(data)))
    return protocol


@pytest.mark.parametrize("method", ["agents.status", "agents.attach", "agents.wait", "agents.result"])
def test_dispatch_compacts_bulk_fields_and_preserves_report_warnings_and_handles(method):
    async def check():
        original = large_result()
        protocol = protocol_with(original)
        params = {"thread_id": "worker"}
        if method in ("agents.wait", "agents.result"):
            params.update(turn_id="turn", timeout_seconds=0)
        summary = await protocol.dispatch(method, params)
        assert len(json.dumps(summary).encode()) < 2000
        for key in ("thread_id", "turn_id", "root_turn_id", "status", "error", "report", "structured_result", "started_at", "completed_at"):
            assert summary[key] == original[key]
        assert summary["inherited_context"]["warnings"] == original["inherited_context"]["warnings"]
        assert summary["inherited_context"]["skills"] == 70
        assert "sources" not in summary["inherited_context"]
        assert "settings" not in summary["inherited_context"]
        assert "text" not in summary["messages"][0]
        assert summary["messages"][0]["id"] == "message"
        assert summary["command_count"] == 1 and "commands" not in summary
        assert summary["changed_files"] == [{"path": "a.py", "kind": "update", "move_path": "b.py", "diff_available": True}]
        assert summary["workspace_changes"] == {"source": "git", "warning": "An unreadable file was omitted", "diff_available": True}
        assert {"commands", "messages", "inherited_context", "changed_files", "workspace_changes"} <= set(summary["details_available"])
        assert original == large_result()
    asyncio.run(check())


@pytest.mark.parametrize("method", ["agents.status", "agents.attach", "agents.wait", "agents.result"])
def test_full_detail_and_selected_fields_recover_exact_values(method):
    async def check():
        data = large_result()
        protocol = protocol_with(data)
        params = {"thread_id": "worker"}
        if method in ("agents.wait", "agents.result"):
            params.update(turn_id="turn", timeout_seconds=0)
        assert await protocol.dispatch(method, {**params, "detail": "full"}) == data
        fields = ["commands", "inherited_context", "workspace_changes"]
        selected = await protocol.dispatch(method, {**params, "fields": fields})
        for field in fields:
            assert selected[field] == data[field]
        for field in ("thread_id", "turn_id", "root_turn_id", "status", "error"):
            assert selected[field] == data[field]
        assert "report" not in selected
        assert "fields" not in params and "detail" not in params
    asyncio.run(check())


@pytest.mark.parametrize("option", [
    {"detail": "large"}, {"detail": None}, {"fields": "report"}, {"fields": None}, {"fields": [""]},
    {"fields": [" "]}, {"fields": [1]}, {"fields": ["typo"]}, {"fields": ["report"] * 33},
])
def test_invalid_output_options_fail_before_reading_runtime(option):
    async def check():
        protocol = protocol_with(large_result())
        with pytest.raises(ValueError):
            await protocol.dispatch("agents.status", {"thread_id": "worker", **option})
        protocol.status.assert_not_called()
    asyncio.run(check())


def test_selected_optional_fields_are_valid_before_a_turn_exists():
    async def check():
        protocol = protocol_with({"thread_id": "worker", "status": "starting", "report": "", "error": None})
        selected = await protocol.dispatch("agents.result", {"thread_id": "worker", "fields": ["status", "report", "turn_id"]})
        assert selected == {"thread_id": "worker", "status": "starting", "report": "", "error": None}
        assert await protocol.dispatch("agents.status", {"thread_id": "worker", "fields": []}) == {
            "thread_id": "worker", "status": "starting", "error": None}
    asyncio.run(check())


def test_complete_large_reports_and_structured_results_are_never_truncated():
    async def check():
        data = large_result()
        data.update(status="completed", report="Final answer " * 20000, structured_result={"evidence": "evidence" * 20000})
        result = await protocol_with(data).dispatch("agents.result", {"thread_id": "worker"})
        assert result["report"] == data["report"]
        assert result["structured_result"] == data["structured_result"]
    asyncio.run(check())


def test_native_observation_uses_the_same_projection_and_explicit_fields():
    async def check():
        data = large_result()
        protocol = protocol_with(data)
        protocol.agent = lambda caller: {"session_id": caller}
        for options in ({}, {"fields": ["commands"]}):
            reply = await protocol.native_call({"method": "item/tool/call", "params": {
                "threadId": "parent", "tool": "agents_status", "arguments": {"thread_id": "worker", **options}}})
            assert reply["success"]
            value = json.loads(reply["contentItems"][0]["text"])
            assert value["thread_id"] == "worker"
            if options:
                assert value["commands"] == data["commands"]
            else:
                assert value["command_count"] == 1 and "commands" not in value
        assert protocol.status.call_args.args == ("worker",)
    asyncio.run(check())
