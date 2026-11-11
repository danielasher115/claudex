"""Current agent activity from observable Codex events and persisted turn snapshots."""
import json
import time
from pathlib import PurePosixPath

TOOLS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall", "webSearch", "imageView"}
TERMINAL = {"completed", "failed", "interrupted", "cancelled", "declined"}


def text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (text(item) for item in value)))
    if isinstance(value, dict):
        return text(value.get("text") or value.get("content") or value.get("summary"))
    return ""


def describe(item):
    kind = item.get("type")
    if kind == "reasoning":
        summary = text(item.get("summary")).strip() or text(item.get("content")).strip()
        return {"phase": "thinking", "label": "Thinking", "detail": summary[:700] or "Codex is reasoning; no readable summary was provided."}
    if kind == "commandExecution":
        actions = item.get("commandActions") or []
        reads = [a for a in actions if a.get("type") == "read"]
        searches = [a for a in actions if a.get("type") == "search"]
        if reads and not searches:
            names = [a.get("name") or PurePosixPath(a.get("path") or "file").name for a in reads]
            return {"phase": "reading", "label": "Reading " + ", ".join(names[:2]) + (f" (+{len(names)-2})" if len(names) > 2 else ""), "detail": ", ".join(a.get("path", "") for a in reads)[:700]}
        if searches and not reads:
            return {"phase": "searching", "label": "Searching files", "detail": " · ".join(filter(None, (a.get("query") or a.get("path") for a in searches)))[:700]}
        if searches and reads:
            return {"phase": "searching", "label": "Reading and searching files", "detail": (item.get("command") or "")[:700]}
        return {"phase": "running_command", "label": "Running command", "detail": (item.get("command") or "Command output is pending")[:700]}
    if kind == "fileChange":
        return {"phase": "editing", "label": "Editing files", "detail": ", ".join(c.get("path", "") for c in item.get("changes", []))[:700]}
    tool = item.get("tool") or item.get("name") or ""
    if tool in ("agents_wait", "wait", "wait_agent"):
        return {"phase": "waiting_agents", "label": "Waiting for subagents", "detail": "Delegated work is still running"}
    if tool in ("agents_launch", "spawn_agent"):
        return {"phase": "delegating", "label": "Launching a subagent", "detail": "Preparing delegated work"}
    if kind == "webSearch":
        return {"phase": "searching", "label": "Searching the web", "detail": str(item.get("query") or "")[:700]}
    return {"phase": "calling_tool", "label": "Using " + str(tool or kind or "a tool"), "detail": str(item.get("server") or "")}


class Activity:
    def __init__(self):
        self.states = {}

    def state(self, session_id, turn_id=None):
        state = self.states.get(session_id)
        if state is None or turn_id and state.get("turn_id") != turn_id:
            now = time.time()
            state = {"phase": "working", "label": "Working", "detail": "Waiting for the next observable Codex update", "turn_id": turn_id,
                     "turn_started_at": None, "phase_started_at": now, "last_event_at": None, "last_update": "", "source": "snapshot", "tools": {}, "live_at": 0, "fingerprint": None}
            self.states[session_id] = state
        return state

    @staticmethod
    def update(state, description, *, live=False, at=None):
        now = at if at is not None else time.time()
        changed = any(state.get(key) != value for key, value in description.items())
        if state.get("phase") != description.get("phase", state.get("phase")):
            state["phase_started_at"] = now
        state.update(description)
        if live or changed or state["last_event_at"] is None:
            state["last_event_at"] = now
        if live:
            state["live_at"] = time.time()
            state["source"] = "live"
        elif state["source"] != "live":
            state["source"] = "snapshot"

    def hydrate(self, session_id, turn):
        existing = self.states.get(session_id)
        if existing and existing.get("turn_id") != turn.get("id") and time.time() - existing["live_at"] < 3:
            return
        state = self.state(session_id, turn.get("id"))
        state["turn_started_at"] = turn.get("startedAt") or state["turn_started_at"]
        status = turn.get("status")
        if status in TERMINAL:
            state["tools"].clear()
            self.update(state, {"phase": status, "label": {"completed": "Completed", "interrupted": "Stopped", "failed": "Failed", "cancelled": "Stopped"}.get(status, status.title()), "detail": ""}, at=turn.get("completedAt"))
            return
        if time.time() - state["live_at"] < 3:
            return
        items = turn.get("items") or []
        active = [item for item in items if item.get("type") in TOOLS and item.get("status") in ("inProgress", "running")]
        fingerprint = json.dumps([status, len(items), active, items[-1] if items else None], sort_keys=True)
        if fingerprint == state["fingerprint"]:
            return
        state["fingerprint"] = fingerprint
        state["tools"] = {i["id"]: i for i in active if i.get("id")}
        updates = [item for item in items if item.get("type") == "agentMessage" and item.get("text")]
        if updates:
            state["last_update"] = updates[-1]["text"][:700]
        last = items[-1] if items else {}
        if active:
            description = describe(active[-1])
        elif last.get("type") == "reasoning":
            description = describe(last)
        elif last.get("type") == "agentMessage":
            description = {"phase": "responding", "label": "Writing a response", "detail": text(last.get("text"))[:700]}
        elif last.get("type") in TOOLS:
            description = {"phase": "processing", "label": "Processing tool results", "detail": "The last tool finished; Codex is continuing the turn"} if last.get("status") in TERMINAL else {"phase": "working", "label": "Working", "detail": "Last recorded action: " + describe(last)["label"]}
        else:
            description = {"phase": "working", "label": "Working", "detail": "No current tool or readable reasoning update has been recorded"}
        self.update(state, description)

    def observe(self, message):
        params = message.get("params") or {}
        session_id = params.get("threadId") or params.get("thread_id")
        if not session_id:
            return
        method = message.get("method", "")
        turn = params.get("turn") or {}
        turn_id = params.get("turnId") or turn.get("id")
        existing = self.states.get(session_id)
        if turn_id and existing and existing.get("turn_id") and existing["turn_id"] != turn_id and method != "turn/started" and existing["phase"] not in TERMINAL:
            return
        state = self.state(session_id, turn_id)
        if method == "turn/started":
            state["tools"].clear()
            state["turn_started_at"] = turn.get("startedAt") or time.time()
            self.update(state, {"phase": "working", "label": "Starting turn", "detail": "Codex accepted the task"}, live=True)
        elif method == "turn/completed":
            self.hydrate(session_id, turn)
        elif "requestApproval" in method or "requestUserInput" in method:
            approval = "requestApproval" in method
            self.update(state, {"phase": "waiting_approval" if approval else "waiting_input", "label": "Waiting for approval" if approval else "Waiting for input", "detail": "Action is required in the original Codex client"}, live=True)
        elif method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            kind = item.get("type")
            if kind in TOOLS:
                if method == "item/started":
                    state["tools"][item.get("id", "tool")] = item
                else:
                    state["tools"].pop(item.get("id"), None)
                current = next(reversed(state["tools"].values()), None)
                self.update(state, describe(current) if current else {"phase": "processing", "label": "Processing tool results", "detail": "The last tool finished; Codex is continuing the turn"}, live=True)
            elif kind == "reasoning":
                self.update(state, describe(item), live=True)
            elif kind == "agentMessage":
                value = item.get("text", "")
                if value:
                    state["last_update"] = value[:700]
                self.update(state, {"phase": "responding", "label": "Writing a response", "detail": value[:700] or "Receiving model output"}, live=True)
        elif "/reasoning/" in method and "Delta" in method:
            if state.get("reasoning_id") != params.get("itemId"):
                state["reasoning_id"] = params.get("itemId"); state["reasoning_text"] = ""
            state["reasoning_text"] = (state.get("reasoning_text", "") + params.get("delta", ""))[-2000:]
            self.update(state, {"phase": "thinking", "label": "Thinking", "detail": state["reasoning_text"][-700:] or "Codex is reasoning; no readable summary was provided."}, live=True)
        elif method == "item/agentMessage/delta":
            if state.get("message_id") != params.get("itemId"):
                state["message_id"] = params.get("itemId"); state["message_text"] = ""
            state["message_text"] = (state.get("message_text", "") + params.get("delta", ""))[-700:]
            self.update(state, {"phase": "responding", "label": "Writing a response", "detail": state["message_text"]}, live=True)
        elif method == "item/commandExecution/outputDelta":
            current = state["tools"].get(params.get("itemId"))
            self.update(state, describe(current) if current else {"phase": "running_command", "label": "Receiving command output", "detail": "Command output is arriving"}, live=True)
        elif method == "thread/tokenUsage/updated":
            self.update(state, {}, live=True)

    def snapshot(self, session_id, status, flags):
        state = self.state(session_id)
        public = {k: state[k] for k in ("phase", "label", "detail", "turn_id", "turn_started_at", "phase_started_at", "last_event_at", "last_update", "source")}
        public["tool_count"] = len(state["tools"])
        if status == "unavailable":
            public.update(phase="offline", label="Disconnected", detail="Live activity is unavailable")
        elif status == "active":
            if "waitingOnApproval" in flags:
                public.update(phase="waiting_approval", label="Waiting for approval", detail="Action is required in the original Codex client")
            elif "waitingOnUserInput" in flags:
                public.update(phase="waiting_input", label="Waiting for input", detail="The agent needs an answer in Codex")
            elif public["phase"] in ("waiting_approval", "waiting_input"):
                current = next(reversed(state["tools"].values()), None)
                public.update(describe(current) if current else {"phase": "working", "label": "Working", "detail": "Codex resumed; waiting for its next update"})
            elif public["phase"] in TERMINAL or public["phase"] == "idle":
                public.update(phase="working", label="Working", detail="A new turn is active; waiting for its first update")
        elif public["phase"] not in TERMINAL:
            public.update(phase="idle", label="Idle" if status == "idle" else "Saved", detail="No turn is currently running")
        return public
