import sqlite3

from codex_sessions.activity import Activity
from codex_sessions.history import History


def test_current_read_command_is_visible_and_completion_does_not_stay_working():
    activity = Activity()
    turn = {"id": "turn", "status": "inProgress", "startedAt": 100, "items": [{"id": "cmd", "type": "commandExecution", "status": "inProgress", "command": "cat src/startup.cs", "commandActions": [{"type": "read", "name": "startup.cs", "path": "/repo/src/startup.cs"}]}]}
    activity.hydrate("a", turn)
    state = activity.snapshot("a", "active", [])
    assert state["phase"] == "reading"
    assert "startup.cs" in state["label"]
    assert state["turn_started_at"] == 100
    activity.hydrate("a", {**turn, "status": "completed", "completedAt": 200})
    assert activity.snapshot("a", "idle", [])["phase"] == "completed"


def test_empty_reasoning_still_reports_real_activity_without_fabricating_text():
    activity = Activity()
    activity.observe({"method": "item/started", "params": {"threadId": "a", "turnId": "t", "item": {"id": "r", "type": "reasoning", "summary": [], "content": []}}})
    state = activity.snapshot("a", "active", [])
    assert state["phase"] == "thinking"
    assert "no readable" in state["detail"].lower()
    activity.observe({"method": "item/reasoning/summaryTextDelta", "params": {"threadId": "a", "turnId": "t", "itemId": "r", "delta": "Inspecting shutdown handlers"}})
    assert "Inspecting shutdown handlers" in activity.snapshot("a", "active", [])["detail"]


def test_approval_and_parallel_tools_are_not_mistaken_for_thinking():
    activity = Activity()
    for name in ("first", "second"):
        activity.observe({"method": "item/started", "params": {"threadId": "a", "turnId": "t", "item": {"id": name, "type": "commandExecution", "status": "inProgress", "command": name}}})
    activity.observe({"method": "item/completed", "params": {"threadId": "a", "turnId": "t", "item": {"id": "second", "type": "commandExecution", "status": "completed"}}})
    assert activity.snapshot("a", "active", [])["detail"] == "first"
    assert activity.snapshot("a", "active", ["waitingOnApproval"])["phase"] == "waiting_approval"


def test_repeated_identical_snapshots_do_not_fake_progress():
    activity = Activity()
    turn = {"id": "t", "status": "inProgress", "items": [{"id": "r", "type": "reasoning", "summary": [], "content": []}]}
    activity.hydrate("a", turn)
    first = activity.snapshot("a", "active", [])
    activity.hydrate("a", turn)
    assert activity.snapshot("a", "active", [])["last_event_at"] == first["last_event_at"]


def test_late_events_from_the_previous_turn_do_not_replace_current_work():
    activity = Activity()
    activity.observe({"method": "turn/started", "params": {"threadId": "a", "turn": {"id": "new", "status": "inProgress"}}})
    activity.observe({"method": "item/started", "params": {"threadId": "a", "turnId": "new", "item": {"id": "read", "type": "commandExecution", "status": "inProgress", "command": "cat current.py"}}})
    activity.observe({"method": "turn/completed", "params": {"threadId": "a", "turn": {"id": "old", "status": "completed"}}})
    assert activity.snapshot("a", "active", [])["turn_id"] == "new"
    assert activity.snapshot("a", "active", [])["detail"] == "cat current.py"


def test_stateless_search_or_image_records_are_not_assumed_to_be_running():
    for kind in ("webSearch", "imageView"):
        activity = Activity()
        activity.hydrate("a", {"id": "t", "status": "inProgress", "items": [{"id": "old", "type": kind, "query": "old query"}, {"id": "r", "type": "reasoning", "summary": ["Analyzing the results"], "content": []}]})
        state = activity.snapshot("a", "active", [])
        assert state["phase"] == "thinking"
        assert state["tool_count"] == 0


def test_resolved_approval_restores_the_quiet_running_command():
    activity = Activity()
    activity.hydrate("a", {"id": "t", "status": "inProgress", "items": [{"id": "c", "type": "commandExecution", "status": "inProgress", "command": "sleep 600"}]})
    activity.observe({"method": "item/commandExecution/requestApproval", "params": {"threadId": "a", "turnId": "t"}})
    assert activity.snapshot("a", "active", ["waitingOnApproval"])["phase"] == "waiting_approval"
    resumed = activity.snapshot("a", "active", [])
    assert resumed["phase"] == "running_command"
    assert resumed["detail"] == "sleep 600"


def test_metadata_uses_current_sqlite_directory_instead_of_stale_legacy_file(tmp_path):
    (tmp_path / "sqlite").mkdir()
    for path, identity in [(tmp_path / "state_5.sqlite", "stale"), (tmp_path / "sqlite/state_5.sqlite", "current")]:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE threads (id TEXT, updated_at INTEGER)")
            db.execute("INSERT INTO threads VALUES (?,1)", (identity,))
    assert [row["id"] for row in History(tmp_path).sessions()] == ["current"]
