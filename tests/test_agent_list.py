import asyncio
import time

import pytest

from codex_sessions.interfaces import tools
from test_smoke import service


def snapshot_service(tmp_path, rows):
    instance = service(tmp_path)
    instance.cached = {"sessions": rows, "connection": {"connected": True, "error": None}}
    instance.cache_at = time.monotonic()
    return instance


def row(session_id, status="idle", **fields):
    return {"id": session_id, "title": f"Worker {session_id}", "status": status, "cwd": "/repo", "model": "model",
            "parent_id": "parent", "origin": "appServer", "updated_at": 42, "archived": False,
            "preview": "Private prompt", "activity": {"text": "Verbose activity"}, "tokens": 123, **fields}


def test_agent_list_defaults_to_compact_live_rows(tmp_path):
    instance = snapshot_service(tmp_path, [row("active", "active"), row("idle"), row("starting", "starting"),
                                           row("saved", "saved"), row("offline", "unavailable"), row("external", "external"),
                                           row("archived", "active", archived=True)])
    result = asyncio.run(instance.protocol.dispatch("agents.list", {}))
    assert [agent["thread_id"] for agent in result["agents"]] == ["active", "idle", "starting"]
    assert result["agents"][0] == {"thread_id": "active", "id": "active", "name": "Worker active", "status": "active",
                                    "cwd": "/repo", "model": "model", "parent_id": "parent", "origin": "appServer", "updated_at": 42}
    assert result["connection"] == {"connected": True, "error": None}
    assert result["count"] == 3
    assert result["next_cursor"] is None


@pytest.mark.parametrize(("selection", "expected"), [("live", ["live"]), ("saved", ["saved"]),
                                                     ("archived", ["archived"]), ("all", ["archived", "live", "offline", "saved"])])
def test_agent_list_filter_selects_history_only_when_requested(tmp_path, selection, expected):
    instance = snapshot_service(tmp_path, [row("live"), row("saved", "saved"), row("offline", "unavailable"),
                                           row("archived", "saved", archived=True)])
    result = asyncio.run(instance.protocol.dispatch("agents.list", {"filter": selection}))
    assert [agent["thread_id"] for agent in result["agents"]] == expected


def test_agent_list_pages_remain_stable_after_earlier_rows_are_removed_or_updated(tmp_path):
    instance = snapshot_service(tmp_path, [row("d"), row("b"), row("c"), row("a")])
    first = asyncio.run(instance.protocol.dispatch("agents.list", {"limit": 2}))
    assert [agent["thread_id"] for agent in first["agents"]] == ["a", "b"]
    assert isinstance(first["next_cursor"], str)
    instance.cached["sessions"] = [row("b", updated_at=999), row("d"), row("c")]
    second = asyncio.run(instance.protocol.dispatch("agents.list", {"limit": 2, "cursor": first["next_cursor"]}))
    assert [agent["thread_id"] for agent in second["agents"]] == ["c", "d"]
    assert second["count"] == 2
    assert second["next_cursor"] is None
    with pytest.raises(ValueError, match="cursor"):
        asyncio.run(instance.protocol.dispatch("agents.list", {"filter": "all", "cursor": first["next_cursor"]}))


def test_agent_list_default_and_maximum_page_sizes(tmp_path):
    instance = snapshot_service(tmp_path, [row(f"worker-{index:03}") for index in range(101)])
    first = asyncio.run(instance.protocol.dispatch("agents.list", {}))
    assert len(first["agents"]) == first["count"] == 25
    assert first["next_cursor"]
    maximum = asyncio.run(instance.protocol.dispatch("agents.list", {"limit": 100}))
    assert len(maximum["agents"]) == 100
    last = asyncio.run(instance.protocol.dispatch("agents.list", {"limit": 100, "cursor": maximum["next_cursor"]}))
    assert [agent["thread_id"] for agent in last["agents"]] == ["worker-100"]
    assert last["next_cursor"] is None


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": 2.5}, {"limit": "2"},
                                   {"filter": None}, {"filter": []}, {"filter": "active"}, {"cursor": 1},
                                   {"cursor": ""}, {"cursor": "invalid"}, {"cursor": "e30="}, {"cursor": "x" * 5000},
                                   {"unexpected": True}])
def test_agent_list_rejects_invalid_input(tmp_path, params):
    instance = snapshot_service(tmp_path, [])
    with pytest.raises(ValueError):
        asyncio.run(instance.protocol.dispatch("agents.list", params))


@pytest.mark.parametrize("native", [False, True])
def test_agent_list_tool_schema_exposes_filters_and_pagination(native):
    schema = next(tool["inputSchema"] for tool in tools(native=native) if tool["name"] == "agents_list")
    assert schema["properties"]["filter"]["enum"] == ["live", "saved", "archived", "all"]
    assert schema["properties"]["limit"]["maximum"] == 100
    assert schema["properties"]["cursor"]["type"] == "string"
