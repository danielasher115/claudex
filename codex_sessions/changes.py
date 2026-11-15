"""Observe Git workspace changes independently of the tools an agent uses."""
import asyncio
import difflib
import json
import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .platform import quiet_process_options

MAX_FILE = 1024 * 1024
MAX_BYTES = 16 * MAX_FILE
MAX_PATHS = 2000
OMITTED = object()


def git(root, *args):
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-C", str(root), *args],
                            capture_output=True, timeout=10, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
                            **quiet_process_options())
    if result.returncode:
        raise ValueError("Git workspace is unavailable")
    if len(result.stdout) > MAX_BYTES:
        raise ValueError("Git workspace exceeds the capture budget")
    return result.stdout


def dirty_paths(root):
    entries = iter(git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0"))
    paths = set()
    for entry in entries:
        if not entry:
            continue
        paths.add(os.fsdecode(entry[3:]))
        if b"R" in entry[:2] or b"C" in entry[:2]:
            paths.add(os.fsdecode(next(entries)))
    if len(paths) > MAX_PATHS:
        raise ValueError("Git workspace exceeds the capture path budget")
    return paths


def contents(root, path):
    parts = Path(path).parts
    if not parts or Path(path).is_absolute() or any(part == ".." or part.casefold() == ".git" for part in parts):
        return OMITTED
    descriptors = []
    try:
        path.encode("utf-8")
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(descriptor)
            for part in parts[:-1]:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                descriptors.append(descriptor)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            descriptors.append(descriptor)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return OMITTED
            data = os.read(descriptor, MAX_FILE + 1)
        else:
            parent = root
            for part in parts:
                parent = parent / part
                if parent.is_symlink():
                    return OMITTED
            if not parent.resolve().is_relative_to(root) or not parent.is_file():
                return None if not parent.exists() else OMITTED
            with parent.open("rb") as stream:
                data = stream.read(MAX_FILE + 1)
        return data if len(data) <= MAX_FILE else OMITTED
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        return OMITTED
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def file_mode(root, path):
    try:
        mode = (root / path).stat(follow_symlinks=False).st_mode
        return "100755" if mode & stat.S_IXUSR else "100644"
    except OSError:
        return None


@dataclass
class Baseline:
    root: Path
    head: str | None
    dirty: dict
    warning: str = ""
    modes: dict = field(default_factory=dict)


def baseline(cwd):
    root = Path(os.fsdecode(git(cwd, "rev-parse", "--show-toplevel")).strip()).resolve()
    try:
        head = git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    except ValueError:
        head = None
    dirty, modes, size = {}, {}, 0
    try:
        for path in sorted(dirty_paths(root)):
            data = contents(root, path)
            if isinstance(data, bytes):
                size += len(data)
            if size > MAX_BYTES:
                raise ValueError("Git dirty baseline exceeds the capture byte budget")
            dirty[path] = data
            modes[path] = file_mode(root, path) if isinstance(data, bytes) else None
    except ValueError as exc:
        return Baseline(root, head, {}, f"Starting workspace baseline was not captured: {exc}")
    return Baseline(root, head, dirty, modes=modes)


def original(base, path):
    if path in base.dirty:
        return base.dirty[path]
    if not base.head:
        return None
    try:
        spec = f"{base.head}:{path}"
        if int(git(base.root, "cat-file", "-s", spec)) > MAX_FILE:
            return OMITTED
        return git(base.root, "cat-file", "blob", spec)
    except ValueError:
        return None


def original_mode(base, path):
    if path in base.dirty:
        return base.modes.get(path)
    if not base.head:
        return None
    entry = git(base.root, "ls-tree", "-z", base.head, "--", ":(literal)" + path)
    return entry[:6].decode() if entry else None


def patch(path, before, after, before_mode="100644", after_mode="100644"):
    def label(prefix):
        value = prefix + path
        return json.dumps(value, ensure_ascii=False) if any(c.isspace() or c in '\\"' for c in value) else value
    old, new = label("a/"), label("b/")
    result = f"diff --git {old} {new}\n"
    if before is None:
        result += f"new file mode {after_mode}\n"
    if after is None:
        result += f"deleted file mode {before_mode}\n"
    if before is not None and after is not None and before_mode != after_mode:
        result += f"old mode {before_mode}\nnew mode {after_mode}\n"
    if b"\0" in (before or b"") or b"\0" in (after or b""):
        return result + f"Binary files {old if before is not None else '/dev/null'} and {new if after is not None else '/dev/null'} differ\n"
    try:
        a, b = (before or b"").decode("utf-8"), (after or b"").decode("utf-8")
    except UnicodeError:
        return result + f"Binary files {old} and {new} differ\n"
    for line in difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                     fromfile=old if before is not None else "/dev/null",
                                     tofile=new if after is not None else "/dev/null"):
        result += line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
    return result


def compare(base):
    if base.warning:
        return "", base.warning
    paths = dirty_paths(base.root) | base.dirty.keys()
    if base.head:
        paths.update(os.fsdecode(p) for p in git(base.root, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", base.head, "--").split(b"\0") if p)
    else:
        paths.update(os.fsdecode(p) for p in git(base.root, "ls-files", "--cached", "-z").split(b"\0") if p)
    if len(paths) > MAX_PATHS:
        raise ValueError("Git workspace exceeds the capture path budget")
    patches, skipped, size = [], 0, 0
    for index, path in enumerate(sorted(paths)):
        if size >= MAX_BYTES:
            skipped += len(paths) - index
            break
        before, after = original(base, path), contents(base.root, path)
        if before is OMITTED or after is OMITTED:
            skipped += 1
            continue
        size += len(before or b"") + len(after or b"")
        if size > MAX_BYTES:
            skipped += 1
            continue
        before_mode = original_mode(base, path) if before is not None else None
        after_mode = file_mode(base.root, path) if after is not None else None
        if before != after or before_mode != after_mode:
            patches.append(patch(path, before, after, before_mode, after_mode))
    warning = f"{skipped} file(s) omitted because they are large, unreadable, linked, or exceed the capture budget." if skipped else ""
    return "".join(patches), warning


class Changes:
    def __init__(self, service):
        self.service = service
        self.baselines = {}
        self.pending = {}
        self.jobs = set()
        self.locks = {}
        service.db.execute("CREATE TABLE IF NOT EXISTS workspace_diffs (session_id TEXT, turn_id TEXT, diff TEXT NOT NULL, warning TEXT NOT NULL, PRIMARY KEY(session_id, turn_id))")
        service.db.commit()

    async def begin(self, session_id, cwd):
        await self.flush()
        if not Path(cwd).is_dir():
            return None
        try:
            base = await asyncio.to_thread(baseline, cwd)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        self.pending[session_id] = base
        return base

    def bind(self, session_id, turn_id):
        base = self.pending.pop(session_id, None)
        if base and turn_id:
            self.baselines.setdefault((session_id, turn_id), base)

    def saved(self, session_id, turn_id):
        row = self.service.db.execute("SELECT diff,warning FROM workspace_diffs WHERE session_id=? AND turn_id=?", (session_id, turn_id)).fetchone()
        return {"diff": row["diff"], "warning": row["warning"], "source": "observed-workspace"} if row else None

    async def finish(self, session_id, turn):
        key = (session_id, turn["id"])
        if key not in self.baselines or turn.get("status") in (None, "inProgress"):
            return self.saved(*key)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            base = self.baselines.get(key)
            if base is None or turn.get("status") in (None, "inProgress"):
                return self.saved(*key)
            try:
                diff, warning = await asyncio.to_thread(compare, base)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                diff, warning = "", f"Workspace changes could not be captured: {exc}"
            self.service.db.execute("INSERT OR REPLACE INTO workspace_diffs VALUES (?,?,?,?)", (*key, diff, warning))
            self.service.db.commit()
            self.baselines.pop(key, None)
            self.locks.pop(key, None)
            self.service.changed(session_id)
            return self.saved(*key)

    def observe(self, message):
        params = message.get("params") or {}
        session_id, turn = params.get("threadId"), params.get("turn") or {}
        if not session_id or not turn.get("id"):
            return
        if message.get("method") == "turn/started":
            self.bind(session_id, turn["id"])
        if message.get("method") == "turn/completed" and (session_id, turn["id"]) in self.baselines:
            job = asyncio.create_task(self.finish(session_id, turn))
            self.jobs.add(job)
            job.add_done_callback(self.jobs.discard)

    async def flush(self):
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)

    async def close(self):
        await self.flush()
        self.pending.clear()
        self.baselines.clear()
        self.locks.clear()
