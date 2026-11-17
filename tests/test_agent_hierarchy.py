import asyncio
import json
import sqlite3

from codex_sessions.history import History
from test_smoke import service


def test_native_spawn_edges_and_source_metadata_survive_restart(tmp_path):
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads (id TEXT, updated_at INTEGER, source TEXT)")
        db.executemany("INSERT INTO threads VALUES (?,1,?)", [
            ("parent", "vscode"), ("child", "vscode"),
            ("grandchild", json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "child", "depth": 2}}})),
        ])
        db.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
        db.execute("INSERT INTO thread_spawn_edges VALUES ('parent','child','closed')")
    rows = {row["id"]: row for row in History(tmp_path).sessions()}
    assert rows["child"]["native_parent_id"] == "parent"


def test_native_descendants_display_under_gateway_parent_without_control_access(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.db.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                            ("a", None, "Delegated work", 0, 3, 4, "read-only", "/test", "accepted", 1, "{}"))
        instance.db.commit()
        instance.history.sessions = lambda: [
            {"id": "a", "source": "appServer"},
            {"id": "child", "source": "vscode", "native_parent_id": "a", "agent_path": "/root/review"},
            {"id": "grandchild", "source": json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "child", "depth": 2}}})},
            {"id": "unrelated", "source": "vscode", "cwd": "/test"},
        ]
        snapshot = await instance.sessions(fresh=True)
        sessions = {row["id"]: row for row in snapshot["sessions"]}
        assert sessions["child"]["parent_id"] == "a"
        assert sessions["grandchild"]["parent_id"] == "child"
        assert sessions["grandchild"]["root_id"] == "a"
        assert sessions["grandchild"]["depth"] == 2
        assert sessions["grandchild"]["source"] == "agents"
        assert sessions["unrelated"]["parent_id"] is None
        children = instance.display_children("a")
        assert [(row["thread_id"], row["parent_id"], row["depth"], row["origin"]) for row in children] == [
            ("child", "a", 1, "native"), ("grandchild", "child", 2, "native")]
        assert instance.protocol.children("a") == []
        assert instance.protocol.agent("child") is None
        revision = sessions["a"]["revision"]
        instance.rpc.sessions["child"] = "active"
        refreshed = {row["id"]: row for row in (await instance.sessions(fresh=True))["sessions"]}
        assert refreshed["a"]["revision"] != revision
        assert instance.display_children("a")[0]["status"] == "active"
        instance.db.close()
    asyncio.run(check())


def test_live_native_source_cycles_and_orphans_are_safe(tmp_path):
    async def check():
        instance = service(tmp_path)
        original = instance.rpc.call
        async def call(method, params=None):
            result = await original(method, params)
            if method == "thread/read" and params["threadId"] == "b":
                result["thread"]["source"] = {"subAgent": {"thread_spawn": {"parent_thread_id": "a"}}}
            return result
        instance.rpc.call = call
        instance.history.sessions = lambda: [
            {"id": "cycle-one", "native_parent_id": "cycle-two"},
            {"id": "cycle-two", "native_parent_id": "cycle-one"},
            {"id": "orphan", "native_parent_id": "missing"},
        ]
        sessions = {row["id"]: row for row in (await instance.sessions(fresh=True))["sessions"]}
        assert sessions["b"]["parent_id"] == "a"
        assert sessions["b"]["depth"] == 1
        assert sessions["orphan"]["parent_id"] == "missing"
        assert sessions["cycle-one"]["parent_id"] is None
        assert sessions["cycle-two"]["parent_id"] is None
        assert instance.display_children("missing") == [
            {"thread_id": "orphan", "parent_id": "missing", "name": "Untitled session", "depth": 1,
             "cwd": "", "status": "saved", "model": "", "origin": "native", "agent_path": None}]
        instance.db.close()
    asyncio.run(check())


def test_gateway_child_of_native_root_keeps_its_agent_classification(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.db.execute("INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                            ("b", "a", "Gateway review", 1, 3, 4, "read-only", "/test", "accepted", 1, "{}"))
        instance.db.commit()
        instance.history.sessions = lambda: [{"id": "native-child", "native_parent_id": "b"}]
        sessions = {row["id"]: row for row in (await instance.sessions(fresh=True))["sessions"]}
        assert sessions["a"]["source"] == "codex"
        assert sessions["b"]["source"] == "agents"
        assert sessions["native-child"]["source"] == "agents"
        assert sessions["native-child"]["root_id"] == "a"
        assert sessions["native-child"]["depth"] == 2
        instance.db.close()
    asyncio.run(check())
