"""Compact public observations with explicit, lossless retrieval of omitted details."""

OBSERVATIONS = frozenset({"agents.status", "agents.attach", "agents.wait", "agents.result"})
SELECTABLE_FIELDS = frozenset({
    "thread_id", "status", "active_turn_id", "name", "parent_id", "cwd", "model", "flags", "children", "messages",
    "inherited_context", "can_send", "reason", "active_root_turn_id", "cancellation_requested", "cancellation_error",
    "attachment_mode", "gateway_managed", "launch_turn_id", "task_handles", "dashboard_url", "guidance", "turn_id",
    "root_turn_id", "message_id", "error", "report", "structured_result", "started_at", "completed_at", "changed_files",
    "workspace_changes", "commands", "attachments",
})
HANDLES = frozenset({
    "thread_id", "status", "error", "turn_id", "root_turn_id", "message_id", "active_turn_id", "active_root_turn_id",
    "launch_turn_id", "task_handles", "cancellation_requested", "cancellation_error",
})
CONTEXT_FIELDS = frozenset({
    "enabled", "disabled_reason", "provider", "instruction_files", "memory_files", "skills", "has_session_summary",
    "conditional_rules", "imported_files", "caller_session_id", "conversation_messages", "conversation_sha256",
    "conversation_snapshot", "export_id", "warnings",
})
MESSAGE_FIELDS = frozenset({"id", "mode", "status", "error", "turn_id", "created_at"})


def validate_options(detail, fields):
    if detail not in ("summary", "full"):
        raise ValueError("detail must be summary or full")
    if fields is not None and (
        not isinstance(fields, list) or len(fields) > 32
        or any(not isinstance(field, str) or not field.strip() or field not in SELECTABLE_FIELDS for field in fields)
    ):
        raise ValueError("fields must contain at most 32 known output field names")


def observation(data, detail="summary", fields=None):
    if fields is not None:
        return {key: value for key, value in data.items() if key in HANDLES or key in fields}
    if detail == "full":
        return data
    result = dict(data)
    if isinstance(data.get("inherited_context"), dict):
        result["inherited_context"] = {key: value for key, value in data["inherited_context"].items() if key in CONTEXT_FIELDS}
    if "messages" in data:
        result["messages"] = [{key: value for key, value in message.items() if key in MESSAGE_FIELDS} for message in data["messages"]]
    if "commands" in data:
        result.pop("commands")
        result["command_count"] = len(data["commands"])
    if isinstance(data.get("workspace_changes"), dict):
        workspace = data["workspace_changes"]
        result["workspace_changes"] = {key: value for key, value in workspace.items() if key != "diff"}
        result["workspace_changes"]["diff_available"] = bool(workspace.get("diff"))
    if "changed_files" in data:
        result["changed_files"] = [
            {**{key: value for key, value in change.items() if key != "diff"}, "diff_available": bool(change.get("diff"))}
            for change in data["changed_files"]
        ]
    omitted = [key for key in data if key not in result or result[key] != data[key]]
    if omitted:
        result["details_available"] = omitted
    return result
