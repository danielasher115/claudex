"""Agent gateway: persistent sessions, queued delivery, native events and owned task lifetimes."""
import asyncio
import contextlib
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from .platform import private_directory
from .history import History
from .rpc import Rpc, RpcError
from .protocol import Protocol
from .models import Models
from .context import report as context_report
from .activity import Activity
from .media import Media
from .reasoning import Reasoning
from .file_links import FileLinks
from .thread_lifecycle import ThreadLifecycle
from .session_details import SessionDetails

SOURCES = ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview", "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"]


class Gateway:
    def __init__(self, home, socket, data_dir, *, codex_token_file=None):
        self.home = Path(home).resolve()
        self.history = History(self.home)
        self.rpc = Rpc(self.home, socket, token_file=codex_token_file)
        private_directory(data_dir)
        token_file = data_dir / "mcp.token"
        try:
            token_fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(token_fd, "w") as stream:
                stream.write(secrets.token_urlsafe(32))
        self.token = token_file.read_text(encoding="utf-8").strip()
        self.db = sqlite3.connect(data_dir / "dashboard.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, session_id TEXT, text TEXT, mode TEXT, status TEXT, error TEXT, turn_id TEXT, created_at REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS managed (id TEXT PRIMARY KEY)")
        self.db.execute("CREATE TABLE IF NOT EXISTS turn_diffs (session_id TEXT, turn_id TEXT, diff TEXT NOT NULL, PRIMARY KEY(session_id, turn_id))")
        self.db.execute("UPDATE messages SET status='uncertain', error='The dashboard restarted during delivery. Check the conversation before resending.' WHERE status='sending'")
        self.db.commit()
        self.cached = None
        self.cache_at = 0
        self.snapshot_lock = asyncio.Lock()
        self.pages = OrderedDict()
        self.revisions = {}
        self.known_threads = {}
        self.discovery_at = 0
        self.turn_locks = defaultdict(asyncio.Lock)
        self.page_locks = defaultdict(asyncio.Lock)
        self.item_paging = True
        self.protocol = Protocol(self, data_dir)
        self.thread_lifecycle = ThreadLifecycle(self)
        self.file_links = FileLinks(self)
        self.models = Models(self)
        self.details = SessionDetails(self)
        self.activity = Activity()
        self.reasoning = Reasoning(self.db)
        self.activity_polls = {}
        self.media = Media(self.protocol.artifacts)
        self.media_jobs = set()
        self.rpc.on_request = self.protocol.native_call
        self.rpc.on_notification = self.notification

    def notification(self, message):
        self.thread_lifecycle.observe(message)
        self.protocol.turns.observe(message)
        self.details.observe(message)
        self.activity.observe(message)
        self.reasoning.observe(message)
        params = message.get("params") or {}
        session_id = params.get("threadId") or params.get("thread_id")
        if session_id:
            if message.get("method") == "turn/diff/updated" and isinstance(params.get("turnId"), str) and isinstance(params.get("diff"), str):
                self.db.execute("INSERT INTO turn_diffs VALUES (?, ?, ?) ON CONFLICT(session_id, turn_id) DO UPDATE SET diff=excluded.diff", (session_id, params["turnId"], params["diff"]))
                self.db.commit()
            self.changed(session_id)
            item = params.get("item") or {}
            if message.get("method") == "item/completed" and item.get("type") in ("imageView", "imageGeneration"):
                task = asyncio.create_task(self.capture_image(session_id, params.get("turnId"), item))
                self.media_jobs.add(task)
                task.add_done_callback(self.media_jobs.discard)

    async def capture_image(self, session_id, turn_id, item):
        agent = self.protocol.agent(session_id)
        cwd = agent["cwd"] if agent else self.known_threads.get(session_id, {}).get("cwd")
        await self.media.enrich(item, cwd, f"{session_id}:{turn_id}:{item.get('id')}")
        self.changed(session_id)

    def changed(self, session_id):
        self.revisions[session_id] = self.revisions.get(session_id, 0) + 1
        self.cache_at = 0

    def live_reasoning_ids(self, session_id, turn):
        activity = self.activity.states.get(session_id, {})
        reasoning_id = activity.get("reasoning_id")
        if (turn.get("status") == "inProgress" and activity.get("turn_id") == turn.get("id")
            and activity.get("phase") == "thinking" and reasoning_id):
            return {reasoning_id}
        return set()

    def hydrate_activity(self, session_id, turn):
        self.protocol.turns.track(session_id, turn)
        items = self.reasoning.items(session_id, turn["id"], turn.get("items", []),
                                     missing_ids=self.live_reasoning_ids(session_id, turn))
        self.activity.hydrate(session_id, {**turn, "items": items})

    async def sessions(self, fresh=False):
        async with self.snapshot_lock:
            return await self._sessions(fresh)

    async def _sessions(self, fresh=False):
        if not fresh and self.cached and time.monotonic() - self.cache_at < 1:
            return self.cached
        rows = {r["id"]: r for r in self.history.sessions()}
        live = {}
        online = False
        try:
            if not self.discovery_at or time.monotonic() - self.discovery_at > 60:
                discovered = dict(rows)
                cursor = None
                while True:
                    result = await self.rpc.call("thread/list", {"limit": 100, "sortKey": "updated_at", "useStateDbOnly": True, "sourceKinds": SOURCES, "cursor": cursor})
                    discovered.update({r["id"]: r for r in result.get("data", [])})
                    cursor = result.get("nextCursor")
                    if not cursor:
                        break
                self.known_threads = discovered
                self.discovery_at = time.monotonic()
                rows = {r["id"]: r for r in self.history.sessions()}
            live = {key: {**thread, "status": {"type": "notLoaded"}} for key, thread in self.known_threads.items()}
            cursor = None
            while True:
                loaded = await self.rpc.call("thread/loaded/list", {"cursor": cursor})
                for session_id in loaded.get("data", []):
                    try:
                        result = await self.rpc.call("thread/read", {"threadId": session_id})
                    except RpcError as exc:
                        if exc.code is None:
                            raise
                        live[session_id] = {"id": session_id, "status": {"type": "unavailable"}}
                        logging.getLogger(__name__).warning("Loaded thread metadata unavailable for %s: %s", session_id, exc)
                        continue
                    live[session_id] = result["thread"]
                    state = self.activity.states.get(session_id, {})
                    if result["thread"].get("status", {}).get("type") == "active" and time.time() - state.get("live_at", 0) > 3 and time.monotonic() - self.activity_polls.get(session_id, 0) > 4:
                        self.activity_polls[session_id] = time.monotonic()
                        try:
                            turns = await self.rpc.call("thread/turns/list", {"threadId": session_id, "limit": 1, "itemsView": "full"})
                            if turns.get("data"):
                                self.hydrate_activity(session_id, turns["data"][0])
                        except RpcError:
                            pass
                cursor = loaded.get("nextCursor")
                if not cursor:
                    break
            online = True
            self.rpc.error = None
        except (RpcError, OSError, TimeoutError) as exc:
            self.rpc.error = str(exc)
        managed = {r[0] for r in self.db.execute("SELECT id FROM managed")}
        agent_rows = {r["session_id"]: dict(r) for r in self.db.execute("SELECT * FROM agents")}
        sessions = []
        for session_id in rows.keys() | live.keys() | agent_rows.keys():
            row, thread = rows.get(session_id, {}), live.get(session_id, {})
            status = thread.get("status", {}).get("type", "notLoaded") if online else "unavailable"
            if session_id in agent_rows and not row and not thread:
                agent = agent_rows[session_id]
                row = {"cwd": agent["cwd"], "title": agent["name"], "created_at": agent["created_at"], "updated_at": agent["created_at"]}
                status = self.thread_lifecycle.missing(agent)[0] if online else "unavailable"
            if online and status in ("idle", "notLoaded") and session_id in agent_rows and not agent_rows[session_id]["turn_id"] and self.thread_lifecycle.failure(session_id):
                status = "failed"
            external = status in ("notLoaded", "unavailable") and row.get("source") == "mcp" and self.history.external_busy(row)
            source = "claude" if session_id in managed or row.get("source") == "mcp" else "codex"
            sessions.append({
                "id": session_id,
                "title": (thread.get("name") or row.get("name") or row.get("title") or thread.get("preview") or "Untitled session").splitlines()[0][:180],
                "preview": (thread.get("preview") or row.get("preview") or row.get("first_user_message") or "").replace("\n", " ")[:240],
                "cwd": thread.get("cwd") or row.get("cwd") or "",
                "model": thread.get("model") or row.get("model") or "",
                "source": source, "origin": row.get("source") or thread.get("source") or "unknown",
                "status": "external" if external else "saved" if status == "notLoaded" else status,
                "flags": thread.get("status", {}).get("activeFlags", []),
                "updated_at": max(thread.get("updatedAt") or 0, row.get("updated_at") or 0),
                "created_at": thread.get("createdAt") or row.get("created_at") or 0,
                "archived": bool(row.get("archived")),
                "tokens": row.get("tokens_used") or 0,
                "revision": f"{thread.get('updatedAt', '')}:{row.get('updated_at_ms', '')}:{self.history.revision(row)}:{self.revisions.get(session_id, 0)}",
                "can_send": online and not external and status not in ("starting", "failed", "unavailable") and not row.get("archived"),
                "can_steer_queued": True,
                "reason": "This session belongs to a separate Codex process. Use the Claude bridge for live messaging." if external else self.rpc.error if not online else self.thread_lifecycle.failure(session_id) if status == "failed" else "Thread metadata is temporarily unavailable." if status == "unavailable" else None,
                "activity": self.activity.snapshot(session_id, status, thread.get("status", {}).get("activeFlags", [])),
            })
        sessions.sort(key=lambda s: (s["status"] == "active", s["updated_at"]), reverse=True)
        agents = {r["session_id"]: dict(r) for r in self.db.execute("SELECT session_id,parent_id,name,depth,sandbox FROM agents")}
        for session in sessions:
            if session["id"] in agents:
                agent = agents[session["id"]]
                session.update({"title": agent["name"], "parent_id": agent["parent_id"], "depth": agent["depth"], "sandbox": agent["sandbox"], "source": "agents"})
        self.cached = {"sessions": sessions, "connection": {"connected": self.rpc.ws is not None and not self.rpc.error, "error": self.rpc.error, "home": str(self.home), "socket": str(self.rpc.socket)}}
        self.cache_at = time.monotonic()
        return self.cached

    async def session(self, session_id, fresh=False):
        result = await self.sessions(fresh)
        session = next((s.copy() for s in result["sessions"] if s["id"] == session_id), None)
        if session is None and self.protocol.agent(session_id):
            result = await self.sessions(fresh=True)
            session = next((s.copy() for s in result["sessions"] if s["id"] == session_id), None)
        if session is None:
            raise KeyError("Session not found")
        session["active_turn_id"] = None
        session["active_root_turn_id"] = None
        if session["status"] == "active":
            try:
                result = await self.rpc.call("thread/turns/list", {"threadId": session_id, "limit": 1, "itemsView": "notLoaded"})
                for turn in result.get("data", []):
                    if turn.get("status") == "inProgress":
                        session["active_turn_id"] = turn["id"]
                        session["active_root_turn_id"] = self.protocol.turns.root(session_id, turn)
                        self.protocol.turns.track(session_id, turn)
            except RpcError as exc:
                agent = self.protocol.agent(session_id)
                if not agent or not any(text in str(exc) for text in ("is empty", "not found", "not supported yet")):
                    raise
                session["active_turn_id"] = agent["turn_id"]
                session["active_root_turn_id"] = agent["turn_id"]
        session["cancellation_requested"] = self.protocol.turns.cancelled(session_id, session["active_root_turn_id"])
        session["cancellation_error"] = self.protocol.turns.errors.get((session_id, session["active_root_turn_id"])) or (self.protocol.turns.poll_error if session["cancellation_requested"] else None)
        session["messages"] = [dict(r) for r in self.db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY created_at", (session_id,))]
        agent = self.protocol.agent(session_id)
        if agent:
            session["inherited_context"] = context_report(json.loads(agent["request_json"]).get("context", {}))
        return session

    async def item_turns(self, session_id, cursor):
        params = {"threadId": session_id, "limit": 60, "sortDirection": "desc"}
        if cursor:
            params["cursor"] = cursor.removeprefix("items:")
        result = await self.rpc.call("thread/items/list", params)
        wanted = {entry["turnId"] for entry in result.get("data", [])}
        metadata, next_turn = {}, None
        while wanted - metadata.keys():
            page = await self.rpc.call("thread/turns/list", {"threadId": session_id, "itemsView": "notLoaded", "limit": 100, "sortDirection": "desc", "cursor": next_turn})
            metadata.update({turn["id"]: turn for turn in page.get("data", [])})
            next_turn = page.get("nextCursor")
            if not next_turn:
                break
        turns = {}
        for entry in reversed(result.get("data", [])):
            turn_id = entry["turnId"]
            if turn_id not in turns:
                turns[turn_id] = {**metadata.get(turn_id, {}), "id": turn_id, "items": []}
            item = dict(entry["item"])
            for native, public in (("startedAtMs", "_started_at"), ("completedAtMs", "_completed_at")):
                if entry.get(native) is not None:
                    item[public] = entry[native] / 1000
            turns[turn_id]["items"].append(item)
        return {"data": list(reversed(list(turns.values()))), "nextCursor": "items:" + result["nextCursor"] if result.get("nextCursor") else None}

    async def page(self, session_id, cursor=None, *, compact=False):
        async with self.page_locks[session_id]:
            return await self._page(session_id, cursor, compact=compact or bool(cursor and cursor.startswith("items:")))

    async def _page(self, session_id, cursor=None, *, compact=False):
        snapshot = await self.sessions()
        session = next((s for s in snapshot["sessions"] if s["id"] == session_id), None)
        if session is None:
            raise KeyError("Session not found")
        key = (session_id, cursor, session["revision"], bool(self.rpc.ws), compact)
        cached = self.pages.get(key)
        if cached and time.monotonic() - cached[0] < (1 if session["status"] == "active" else 60):
            return cached[1]
        if not cursor or not cursor.startswith("record:"):
            try:
                result = None
                if compact and self.item_paging:
                    try:
                        result = await self.item_turns(session_id, cursor)
                    except RpcError as exc:
                        if exc.code != -32601:
                            raise
                        self.item_paging = False
                if result is None:
                    if cursor and cursor.startswith("items:"):
                        raise RpcError("Item pagination is unavailable. Refresh this conversation.")
                    params = {"threadId": session_id, "limit": 10, "itemsView": "full", "sortDirection": "desc"}
                    if cursor:
                        params["cursor"] = cursor
                    result = await self.rpc.call("thread/turns/list", params)
                if not cursor and result.get("data"):
                    self.hydrate_activity(session_id, result["data"][0])
                    self.cached = None
                items = []
                for turn in reversed(result.get("data", [])):
                    marker = {"id": f"turn-{turn['id']}", "type": "turn", "status": turn.get("status"), "error": turn.get("error"), "turn_id": turn["id"]}
                    marker.update(started_at=turn.get("startedAt"), completed_at=turn.get("completedAt"), duration_ms=turn.get("durationMs"))
                    diff = self.db.execute("SELECT diff FROM turn_diffs WHERE session_id=? AND turn_id=?", (session_id, turn["id"])).fetchone()
                    if diff is not None:
                        marker["diff"] = diff["diff"]
                    items.append(marker)
                    agent = self.protocol.agent(session_id)
                    original_request = json.loads(agent["request_json"]) if agent else {}
                    messages = list(self.db.execute("SELECT m.text,i.artifacts FROM message_inputs i JOIN messages m ON m.id=i.message_id WHERE m.session_id=? AND m.turn_id=?", (session_id, turn["id"])))
                    missing_ids = None
                    if compact:
                        missing_ids = self.live_reasoning_ids(session_id, turn) if not cursor else set()
                    for item in self.reasoning.items(session_id, turn["id"], turn.get("items", []), missing_ids=missing_ids):
                        normalized = {**item, "id": f"{turn['id']}:{item.get('id', len(items))}", "turn_id": turn["id"]}
                        if item.get("type") == "userMessage":
                            content = item.get("content") or []
                            first_text = content[0].get("text", "") if content else ""
                            ids = None
                            if agent and agent["turn_id"] == turn["id"] and "\n\nLocal agent gateway context:" in first_text:
                                first_text = original_request.get("prompt", first_text)
                                ids = original_request.get("attachments", [])
                            else:
                                sent = next((m for m in messages if m["text"] == first_text), None)
                                if sent:
                                    ids = json.loads(sent["artifacts"])
                            if ids is not None:
                                normalized["content"] = [{"type": "text", "text": first_text}]
                                normalized["attachments"] = [self.protocol.artifacts.get(a) for a in ids]
                        normalized = await self.media.enrich(normalized, session.get("cwd"), f"{session_id}:{turn['id']}:{item.get('id')}")
                        items.append(normalized)
                page = {"items": items, "next_cursor": result.get("nextCursor"), "source": "codex", "warning": None}
                self.pages[key] = (time.monotonic(), page)
                while len(self.pages) > 64:
                    self.pages.popitem(last=False)
                return page
            except (RpcError, OSError, TimeoutError) as exc:
                if cursor:
                    raise RpcError(f"Cannot load this history page: {exc}") from exc
        result = self.history.page(self.history.find(session_id), cursor.removeprefix("record:") if cursor else None)
        for entry in result["items"]:
            record = entry.get("record", entry)
            kind = record.get("type")
            if kind in ("function_call_output", "image_generation_call"):
                record = {**record, "type": "functionCallOutput" if kind == "function_call_output" else "imageGeneration"}
            enriched = await self.media.enrich(record, session.get("cwd"), f"{session_id}:recorded:{entry['id']}")
            if "record" in entry:
                entry["record"] = enriched
                for field in ("images", "media_error"):
                    if field in enriched:
                        entry[field] = enriched[field]
            else:
                entry.update(enriched)
        if result["next_cursor"]:
            result["next_cursor"] = "record:" + result["next_cursor"]
        result["warning"] = "Showing recorded history. Live history is currently unavailable."
        return result

    async def send(self, session_id, body):
        text = body.get("text", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 100_000:
            raise ValueError("Enter a message of up to 100,000 characters.")
        mode = body.get("mode", "queue")
        if mode not in ("queue", "steer"):
            raise ValueError("Choose Queue or Steer.")
        ids = self.protocol.attachment_ids(body)
        input_items = [{"type": "text", "text": text}] + self.protocol.artifacts.inputs(ids)
        request_id = str(uuid.UUID(body.get("request_id", "")))
        existing = self.db.execute("SELECT * FROM messages WHERE id=?", (request_id,)).fetchone()
        if existing:
            if existing["session_id"] != session_id or existing["text"] != text or existing["mode"] != mode:
                raise ValueError("This message ID has already been used.")
            original = self.db.execute("SELECT artifacts FROM message_inputs WHERE message_id=?", (request_id,)).fetchone()
            if (json.loads(original[0]) if original else []) != ids:
                raise ValueError("This message ID has already been used with different attachments.")
            return dict(existing)
        session = await self.session(session_id, fresh=True)
        if not session["can_send"]:
            raise ValueError(session["reason"] or "Connect to the Codex daemon to send messages.")
        turn_id = body.get("expected_turn_id")
        if mode == "steer" and (not turn_id or turn_id != session["active_turn_id"]):
            raise ValueError("The active turn changed or finished. Review the conversation before sending again.")
        self.db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", (request_id, session_id, text, mode, "queued", None, turn_id, time.time()))
        self.db.execute("INSERT INTO message_inputs VALUES (?,?,?)", (request_id, json.dumps(input_items), json.dumps(ids)))
        self.db.commit()
        self.changed(session_id)
        if mode == "steer":
            await self.deliver(request_id, "turn/steer", {"threadId": session_id, "expectedTurnId": turn_id, "input": input_items})
        return dict(self.db.execute("SELECT * FROM messages WHERE id=?", (request_id,)).fetchone())

    async def steer_queued(self, session_id, message_id, expected_turn_id):
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("message_id is required")
        if not isinstance(expected_turn_id, str) or not expected_turn_id:
            raise ValueError("expected_turn_id is required for steering")
        async with self.turn_locks[session_id]:
            row = self.db.execute("SELECT * FROM messages WHERE id=? AND session_id=?", (message_id, session_id)).fetchone()
            if row is None:
                raise ValueError("Queued message not found in this session")
            if row["mode"] == "steer" and row["turn_id"] == expected_turn_id:
                return dict(row)
            if row["mode"] != "queue" or row["status"] != "queued":
                raise ValueError("This message is no longer queued; inspect its delivery before sending anything else")
            session = await self.session(session_id, fresh=True)
            if not session["can_send"]:
                raise ValueError(session["reason"] or "Connect to the Codex daemon to send messages")
            if expected_turn_id != session.get("active_turn_id"):
                raise ValueError("The active turn changed or finished. The message remains queued.")
            saved = self.db.execute("SELECT input_json FROM message_inputs WHERE message_id=?", (message_id,)).fetchone()
            inputs = json.loads(saved[0]) if saved else [{"type": "text", "text": row["text"]}]
            changed = self.db.execute("UPDATE messages SET mode='steer',turn_id=? WHERE id=? AND session_id=? AND mode='queue' AND status='queued'", (expected_turn_id, message_id, session_id))
            self.db.commit()
            if not changed.rowcount:
                raise ValueError("This message is no longer queued")
            self.changed(session_id)
            await self.deliver(message_id, "turn/steer", {"threadId": session_id, "expectedTurnId": expected_turn_id, "input": inputs})
            return dict(self.db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    async def deliver(self, message_id, method, params):
        claimed = self.db.execute("UPDATE messages SET status='sending' WHERE id=? AND status='queued'", (message_id,))
        self.db.commit()
        if not claimed.rowcount:
            return
        try:
            result = await self.rpc.call(method, params)
            turn_id = result.get("turnId") or result.get("turn", {}).get("id")
            self.db.execute("UPDATE messages SET status='delivered',turn_id=? WHERE id=?", (turn_id, message_id))
        except (RpcError, OSError, TimeoutError) as exc:
            # A missing acknowledgment cannot prove whether Codex accepted a message.
            status = "failed" if isinstance(exc, RpcError) and exc.code is not None else "uncertain"
            self.db.execute("UPDATE messages SET status=?,error=? WHERE id=?", (status, str(exc), message_id))
        self.db.commit()
        self.cache_at = 0
        self.changed(params["threadId"])

    def cancel(self, session_id, message_id):
        result = self.db.execute("UPDATE messages SET status='cancelled' WHERE id=? AND session_id=? AND status='queued' AND mode='queue'", (message_id, session_id))
        self.db.commit()
        if not result.rowcount:
            raise ValueError("This message has already been delivered or is being sent.")
        self.changed(session_id)
        return {"status": "cancelled"}

    async def stop(self, session_id):
        async with self.turn_locks[session_id]:
            self.db.execute("UPDATE messages SET status='cancelled' WHERE session_id=? AND status='queued'", (session_id,))
            self.db.commit()
            self.changed(session_id)
            session = await self.session(session_id, fresh=True)
            if session["status"] == "external":
                raise ValueError("This agent belongs to a separate process. Stop it in its original launcher.")
            turn_id = session.get("active_turn_id")
            if session["status"] == "active" and not turn_id:
                raise ValueError("The active turn could not be identified. Refresh and retry Stop.")
            if turn_id:
                root = session.get("active_root_turn_id") or turn_id
                self.protocol.turns.mark_cancelled(session_id, root)
                await self.protocol.turns.interrupt_exact(session_id, turn_id, root)
            return {"threadId": session_id, "turnId": turn_id, "status": "stopped", "was_running": bool(turn_id)}

    async def start_queued(self, message):
        async with self.turn_locks[message["session_id"]]:
            current = self.db.execute("SELECT status FROM messages WHERE id=?", (message["id"],)).fetchone()
            if not current or current[0] != "queued":
                return
            session = await self.session(message["session_id"], fresh=True)
            if not session["can_send"] or session["status"] == "active":
                return
            await self.rpc.call("thread/resume", {"threadId": message["session_id"], "excludeTurns": True,
                                                  "config": self.thread_lifecycle.runtime_config()})
            self.thread_lifecycle.acquired(message["session_id"])
            saved = self.db.execute("SELECT input_json FROM message_inputs WHERE message_id=?", (message["id"],)).fetchone()
            self.db.execute("UPDATE agents SET stopped=0 WHERE session_id=?", (message["session_id"],)); self.db.commit()
            await self.deliver(message["id"], "turn/start", {"threadId": message["session_id"], "input": json.loads(saved[0]) if saved else [{"type": "text", "text": message["text"]}]})

    async def worker(self):
        while True:
            pending = list(self.db.execute("SELECT * FROM messages WHERE status='queued' AND mode='queue' ORDER BY created_at"))
            for message in pending:
                try:
                    await self.start_queued(message)
                except KeyError:
                    self.db.execute("UPDATE messages SET status='failed',error='Session no longer exists' WHERE id=?", (message["id"],))
                    self.db.commit()
                    self.changed(message["session_id"])
                except RpcError as exc:
                    if exc.code is not None:
                        self.db.execute("UPDATE messages SET status='failed',error=? WHERE id=?", (str(exc), message["id"]))
                        self.db.commit()
                        self.changed(message["session_id"])
                except (OSError, TimeoutError):
                    pass
            await asyncio.sleep(1)

    async def launch(self, body):
        launched = await self.protocol.launch(body)
        return await self.wait_turn(launched["thread_id"], launched["turn_id"])

    async def wait_turn(self, session_id, turn_id):
        deadline = time.monotonic() + 20
        while True:
            try:
                result = await self.rpc.call("thread/turns/list", {"threadId": session_id, "limit": 20, "itemsView": "full"})
            except RpcError as exc:
                if time.monotonic() < deadline and any(text in str(exc) for text in ("is empty", "not found", "not materialized", "not supported yet")):
                    await asyncio.sleep(.5)
                    continue
                raise
            turn = next((t for t in result.get("data", []) if t["id"] == turn_id), None)
            if turn and turn.get("status") != "inProgress":
                text = "\n\n".join(i.get("text", "") for i in turn.get("items", []) if i.get("type") == "agentMessage")
                return {"threadId": session_id, "turnId": turn_id, "status": turn["status"], "content": text, "error": turn.get("error")}
            await asyncio.sleep(1)


    @asynccontextmanager
    async def running(self):
        jobs = [asyncio.create_task(work) for work in (self.worker(), self.protocol.turns.run(), self.thread_lifecycle.run())]
        try:
            yield self
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            for job in list(self.media_jobs):
                job.cancel()
            if self.media_jobs:
                await asyncio.gather(*list(self.media_jobs), return_exceptions=True)
            await self.protocol.turns.close()
            await self.rpc.close()
            self.reasoning.flush()
            self.db.close()

    async def reply(self, session_id, prompt):
        sent = await self.send(session_id, {"text": prompt, "request_id": str(uuid.uuid4())})
        while sent["status"] in ("queued", "sending"):
            await asyncio.sleep(.5)
            sent = dict(self.db.execute("SELECT * FROM messages WHERE id=?", (sent["id"],)).fetchone())
        if sent["status"] != "delivered":
            raise ValueError(sent["error"] or "Message was not delivered")
        return await self.wait_turn(session_id, sent["turn_id"])
