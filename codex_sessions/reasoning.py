"""Preserve public native reasoning parts that are absent from history snapshots."""
from collections import OrderedDict


class Reasoning:
    def __init__(self, db, *, max_items=128, max_buffer_chars=2_000_000):
        self.db = db
        self.max_items = max_items
        self.max_buffer_chars = max_buffer_chars
        self.pending = OrderedDict()
        self.buffer_size = 0
        self.db.execute("CREATE TABLE IF NOT EXISTS reasoning_parts (session_id TEXT, turn_id TEXT, item_id TEXT, field TEXT, part_index INTEGER, text TEXT NOT NULL, PRIMARY KEY(session_id, turn_id, item_id, field, part_index))")
        self.db.commit()

    @staticmethod
    def readable(parts):
        return isinstance(parts, list) and any(isinstance(part, str) and part.strip() for part in parts)

    def observe(self, message):
        params = message.get("params") or {}
        session_id = params.get("threadId") or params.get("thread_id")
        turn = params.get("turn") or {}
        turn_id = params.get("turnId") or turn.get("id")
        if not isinstance(session_id, str) or not isinstance(turn_id, str):
            return
        method = message.get("method")
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "reasoning" and isinstance(item.get("id"), str):
                self.complete((session_id, turn_id, item["id"]), item)
        elif method == "turn/completed":
            for item in turn.get("items") or []:
                if item.get("type") == "reasoning" and isinstance(item.get("id"), str):
                    self.complete((session_id, turn_id, item["id"]), item)
            for key in list(self.pending):
                if key[:2] == (session_id, turn_id):
                    self.flush(key)
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded", "item/reasoning/textDelta"):
            item_id = params.get("itemId")
            field = "content" if method == "item/reasoning/textDelta" else "summary"
            index = params.get("contentIndex" if field == "content" else "summaryIndex", 0)
            delta = params.get("delta", "")
            if not isinstance(item_id, str) or type(index) is not int or not 0 <= index < 10_000 or not isinstance(delta, str):
                return
            key = (session_id, turn_id, item_id)
            parts = self.pending.setdefault(key, {}).setdefault(field, {})
            self.buffer_size += len(delta) + (64 if index not in parts else 0)
            parts[index] = parts.get(index, "") + delta
            while len(self.pending) > self.max_items or self.buffer_size > self.max_buffer_chars:
                self.flush(next(iter(self.pending)))

    def flush(self, key=None):
        keys = list(self.pending) if key is None else [key]
        with self.db:
            for identity in keys:
                for field, parts in self.pending.get(identity, {}).items():
                    for index, value in parts.items():
                        self.db.execute("INSERT INTO reasoning_parts VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_id, turn_id, item_id, field, part_index) DO UPDATE SET text=reasoning_parts.text || excluded.text", (*identity, field, index, value))
        for identity in keys:
            fields = self.pending.pop(identity, {})
            self.buffer_size -= sum(len(value) + 64 for parts in fields.values() for value in parts.values())

    def complete(self, key, item):
        self.flush(key)
        with self.db:
            for field in ("summary", "content"):
                if self.readable(item.get(field)):
                    self.db.execute("DELETE FROM reasoning_parts WHERE session_id=? AND turn_id=? AND item_id=? AND field=?", (*key, field))
                    self.db.executemany("INSERT INTO reasoning_parts VALUES (?, ?, ?, ?, ?, ?)",
                                        [(*key, field, index, value) for index, value in enumerate(item[field]) if isinstance(value, str)])

    def items(self, session_id, turn_id, native, *, missing_ids=None):
        saved = OrderedDict()
        wanted = None if missing_ids is None else {item.get("id") for item in native if item.get("type") == "reasoning"} | set(missing_ids)
        query = "SELECT item_id, field, part_index, text FROM reasoning_parts WHERE session_id=? AND turn_id=?"
        params = [session_id, turn_id]
        if wanted is not None:
            query += " AND item_id IN (" + ",".join("?" for _ in wanted) + ")"
            params.extend(wanted)
        for row in self.db.execute(query + " ORDER BY rowid", params):
            item_id, field, index, value = row
            saved.setdefault(item_id, {}).setdefault(field, {})[index] = value
        for (session, turn, item_id), fields in self.pending.items():
            if (session, turn) == (session_id, turn_id) and (wanted is None or item_id in wanted):
                for field, parts in fields.items():
                    target = saved.setdefault(item_id, {}).setdefault(field, {})
                    for index, value in parts.items():
                        target[index] = target.get(index, "") + value
        result = []
        present = {item.get("id") for item in native}
        missing = [{"id": item_id, "type": "reasoning"} for item_id in saved if item_id not in present]
        final_index = next((index for index, item in enumerate(native)
                            if item.get("type") == "agentMessage" and item.get("phase") == "final_answer"
                            and item.get("delivery") != "async" and not item.get("questions")), len(native))
        for item in [*native[:final_index], *missing, *native[final_index:]]:
            projected = dict(item)
            if item.get("type") == "reasoning":
                for field, parts in saved.get(item.get("id"), {}).items():
                    if not self.readable(item.get(field)) and parts:
                        projected[field] = [parts.get(index, "") for index in range(max(parts) + 1)]
            result.append(projected)
        return result
