import json
import sqlite3
import re
from contextlib import closing
from pathlib import Path


class History:
    def __init__(self, home: Path):
        self.home = home
        self.cache = {}

    def sessions(self):
        candidates = [path for directory in (self.home, self.home / "sqlite") for path in directory.glob("state_*.sqlite") if re.fullmatch(r"state_\d+\.sqlite", path.name)]
        if not candidates:
            return []
        db = max(candidates, key=lambda path: (int(path.stem.split("_")[1]), path.parent == self.home / "sqlite"))
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM threads ORDER BY updated_at DESC")]

    def find(self, session_id):
        row = next((s for s in self.sessions() if s["id"] == session_id), None)
        if row is None:
            raise KeyError("Session not found")
        return row

    def records(self, row):
        path = Path(row.get("rollout_path") or "").resolve()
        if not any(path.is_relative_to((self.home / d).resolve()) for d in ["sessions", "archived_sessions"]):
            return []
        if not path.is_file():
            return []
        stat = path.stat()
        key = str(path)
        signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        cached = self.cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        append = cached and cached[0][0] == stat.st_ino and stat.st_size > cached[0][2]
        records = cached[1] if append else []
        offset = cached[2] if append else 0
        with path.open("rb") as stream:
            stream.seek(offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                offset = stream.tell()
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
                except (ValueError, UnicodeError):
                    continue
        self.cache[key] = (signature, records, offset)
        return records

    def revision(self, row):
        if row.get("history_mode") == "paginated":
            return f"paginated:{row.get('updated_at_ms', row.get('updated_at', ''))}"
        path = Path(row.get("rollout_path") or "")
        try:
            stat = path.stat()
            return f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            return "missing"

    def external_busy(self, row):
        active = False
        for record in self.records(row):
            if record.get("type") != "event_msg":
                continue
            kind = record.get("payload", {}).get("type")
            if kind in ("task_started", "turn_started"):
                active = True
            elif kind in ("task_complete", "turn_complete", "turn_aborted"):
                active = False
        return active

    def page(self, row, before=None, limit=40):
        if row.get("history_mode") == "paginated":
            raise ValueError("Connect to the Codex daemon to read this session's paginated history.")
        records = [r for r in self.records(row) if r.get("type") in ("response_item", "compacted")]
        end = min(int(before), len(records)) if before is not None else len(records)
        start = max(0, end - limit)
        items = []
        for i, record in enumerate(records[start:end], start):
            payload = record.get("payload", {})
            if payload.get("role") in ("developer", "system"):
                continue
            items.append({"id": f"record-{i}", "type": "record", "record": payload, "timestamp": record.get("timestamp")})
        return {"items": items, "next_cursor": str(start) if start else None, "source": "recorded", "warning": None}
