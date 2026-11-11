"""The shared tool contract for MCP clients and Codex's native dynamic tools."""

PROPERTIES = {
    "prompt": {"type": "string", "description": "The complete task for the agent."},
    "name": {"type": "string"}, "cwd": {"type": "string"}, "model": {"type": "string", "description": "Exact ID from models_list, or a task-tier alias: sonnet, opus, haiku, fable. Aliases select OpenAI models, not Claude. Omit for the latest available coding workhorse."},
    "sandbox": {"type": "string", "enum": ["read-only", "workspace-write", "danger-full-access"]},
    "approval-policy": {"type": "string"}, "developer-instructions": {"type": "string"}, "base-instructions": {"type": "string"},
    "config": {"type": "object"}, "effort": {"type": "string", "description": "Defaults to high. Use a supported effort from models_list when overriding; children inherit an explicit parent effort."}, "output_schema": {"type": "object"},
    "attachments": {"type": "array", "items": {"type": "string"}, "description": "Artifact IDs returned by artifacts_put; all are supplied before generation."},
    "files": {"type": "array", "items": {"type": "string"}, "description": "MCP/CLI only: local file paths to upload and prepare."},
    "parent_id": {"type": "string"}, "request_id": {"type": "string", "description": "UUID used to deduplicate a retried request."},
    "inherit_context": {"type": "boolean", "description": "Default true: inherit caller instructions, saved memory, skill inventory and compatible settings. Children inherit the parent's bundle."},
    "context": {"type": "object", "description": "Caller context export: instructions/memories source+text arrays, skills, settings, caller identity and public conversation messages or a digest-checked snapshot. The installed Claude PreToolUse hook fills this automatically."},
    "session_summary": {"type": "string", "description": "Optional task handoff. Claude's installed context hook exports the exact public caller history automatically; without it, supply the current goals, decisions and constraints here."},
    "limits": {"type": "object", "properties": {"max_children": {"type": "integer", "minimum": 0, "maximum": 32}, "max_depth": {"type": "integer", "minimum": 0, "maximum": 8}}, "additionalProperties": False},
}

SEND_MODE = {"type": "string", "enum": ["steer", "queue"], "description": "Omit to steer an active agent immediately, or start a new turn when idle. Set queue to defer until the current turn ends. Explicit steer requires an active turn and never falls back to queue."}
SEND_TARGET = {"type": "string", "description": "Optional exact active turn to steer. If omitted, the bridge binds the current active turn. A stale target is rejected without queueing or retargeting."}


def tools(native=False):
    launch = {k: v for k, v in PROPERTIES.items() if not native or k != "files"}
    thread = {"thread_id": {"type": "string"}}
    operations = [
        ("protocol_guide", "Read the bundled codex-agents skill before delegating through this gateway: launch fields, High effort default, attachments, context inheritance, waiting, exact-task cancellation and group Stop.", {}, []),
        ("models_list", "List live Codex model IDs, recommended default, reasoning efforts, image support, and approximate Claude task-tier aliases. Call before choosing a delegated worker.", {"refresh": {"type": "boolean"}}, []),
        ("agents_launch", "Launch an asynchronous Codex agent. Returns a persistent thread_id immediately. Native calls create a child of the calling agent; attachments and sandbox constraints are inherited.", launch, ["prompt"]),
        ("agents_send", "Steer new guidance into an active agent by default; an idle agent starts a new turn. Choose mode=queue to wait until its current turn ends. Attach artifact IDs or local files.", {**thread, "text": {"type": "string"}, "mode": SEND_MODE, "expected_turn_id": SEND_TARGET, "attachments": PROPERTIES["attachments"], **({} if native else {"files": PROPERTIES["files"]}), "request_id": PROPERTIES["request_id"]}, ["thread_id", "text"]),
        ("agents_steer", "Send an existing queued message to the active turn now, preserving its message_id, attachments and original task handle. Read agents_status for active_turn_id and supply it as expected_turn_id. Does not create or resend a message.", {**thread, "message_id": {"type": "string"}, "expected_turn_id": {"type": "string"}}, ["thread_id", "message_id", "expected_turn_id"]),
        ("agents_status", "Inspect an agent's runtime, queued messages and children.", thread, ["thread_id"]),
        ("agents_wait", "Wait up to 60 seconds for the most recently submitted gateway message, or the latest native turn. A specific message_id from agents_send or turn_id can be targeted. Returns queued/running if unfinished.", {**thread, "turn_id": {"type": "string"}, "message_id": {"type": "string"}, "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 60}}, ["thread_id"]),
        ("agents_result", "Get the latest final report, changed files, commands, attachments and child references. Optionally target message_id or turn_id.", {**thread, "turn_id": {"type": "string"}, "message_id": {"type": "string"}}, ["thread_id"]),
        ("agents_stop", "Interrupt the agent and, by default, all its gateway descendants. Removes queued follow-ups.", {**thread, "include_children": {"type": "boolean"}}, ["thread_id"]),
        ("agents_children", "List the full gateway delegation tree below an agent.", thread, ["thread_id"]),
        ("agents_list", "List compact local agents and connection state. Defaults to live active/idle/starting agents, excluding archived sessions. Use filter for history and next_cursor for another page, ordered by thread_id.",
         {"filter": {"type": "string", "enum": ["live", "saved", "archived", "all"], "default": "live"},
          "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
          "cursor": {"type": "string", "minLength": 1, "maxLength": 4096, "description": "Opaque next_cursor from the previous page; keep the same filter."}}, []),
        ("artifacts_put", "Prepare an image, document, source file or transcript summary. Documents are extracted fully; unreadable inputs fail before launch.", {"name": {"type": "string"}, "data": {"type": "string", "description": "Base64 file data"}, **({} if native else {"path": {"type": "string", "description": "Local source file path; read by this MCP/CLI process"}}), "kind": {"type": "string", "enum": ["document", "image", "transcript", "reference"]}, "summary": {"type": "string", "description": "An explicit transcript summary to supply with the full text."}}, ["name", "data"] if native else []),
        ("artifacts_get", "Inspect an artifact's preparation status, SHA-256, page count and character count.", {"artifact_id": {"type": "string"}}, ["artifact_id"]),
    ]
    result = [{"name": name, "description": description, "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}} for name, description, properties, required in operations]
    if not native:
        result.append({"name": "agents_cancel", "description": "Cancel only the exact original turn_id or queued message_id. Does not stop newer work or clear other queued messages. Use agents_stop for a whole agent/group.",
                       "inputSchema": {"type": "object", "properties": {**thread, "turn_id": {"type": "string"}, "message_id": {"type": "string"}}, "required": ["thread_id"], "additionalProperties": False}})
        result.append({"name": "agents_transcript", "description": "Read a page of recorded agent history. Pass next_cursor as cursor to load older turns.",
                       "inputSchema": {"type": "object", "properties": {**thread, "cursor": {"type": "string"}}, "required": ["thread_id"], "additionalProperties": False}})
        result += [{"name": "codex", "description": "Compatibility tool: launch Codex and wait for its answer. Use agents_launch for asynchronous delegation.", "inputSchema": {"type": "object", "properties": launch, "required": ["prompt"]}},
                   {"name": "codex-reply", "description": "Compatibility tool: steer an active Codex agent by default, otherwise start a new turn, and wait for its answer. Use mode=queue to defer.", "inputSchema": {"type": "object", "properties": {"threadId": {"type": "string"}, "conversationId": {"type": "string"}, "prompt": {"type": "string"}, "mode": SEND_MODE, "expected_turn_id": SEND_TARGET, "attachments": PROPERTIES["attachments"], "files": PROPERTIES["files"]}, "required": ["prompt"]}}]
    return result
