import asyncio
import base64
import ipaddress
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from .artifacts import MAX_FILE
from .lifecycle import identity, runtime
from .rpc import RpcError
from .platform import local_path as host_path


class FileLinkError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def local_path(href, cwd, origin, aliases=()):
    if not isinstance(href, str) or not href or any(ord(c) < 32 for c in href):
        raise FileLinkError("A local file link is required")
    value = href
    if value.startswith(("//", "\\\\")):
        raise FileLinkError("Remote file links cannot be previewed")
    windows = bool(re.match(r"^[A-Za-z]:(?:[/\\]|%2f|%5c)", value, re.I))
    parsed = urlsplit(value) if not windows else None
    if parsed and parsed.scheme:
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise FileLinkError("Remote file links cannot be previewed")
        elif parsed.scheme in ("http", "https"):
            bases = [urlsplit(value) for value in (origin, *aliases)]
            try:
                loopback = parsed.hostname in {"localhost", "codex.localhost"} or ipaddress.ip_address(parsed.hostname or "").is_loopback
            except ValueError:
                loopback = False
            if not loopback or (parsed.scheme, parsed.hostname, parsed.port) not in [(base.scheme, base.hostname, base.port) for base in bases] or parsed.username or parsed.password:
                raise FileLinkError("Only local files and same-origin file links can be previewed")
        else:
            raise FileLinkError("This link is not a local file")
        value = parsed.path
    elif parsed:
        value = parsed.path
    else:
        value = value.split("#", 1)[0].split("?", 1)[0]
    value = unquote(value)
    if "\x00" in value:
        raise FileLinkError("Invalid file path")
    value = re.sub(r":\d+(?::\d+)?$", "", value)
    value = value.replace("\\", "/")
    if value.startswith("//"):
        raise FileLinkError("Remote file links cannot be previewed")
    drive = re.match(r"^/?([A-Za-z]):/(.*)$", value)
    if drive:
        value = f"{drive[1]}:/{drive[2]}"
    path = host_path(value)
    if not path.is_absolute():
        if not cwd:
            raise FileLinkError("This session has no working directory for relative links")
        base = str(cwd).replace("\\", "/")
        path = host_path(base) / path
    return path.resolve()


def markdown_links(text):
    parser = MarkdownIt("commonmark")
    validate = parser.validateLink
    parser.validateLink = lambda value: value.lower().startswith("file:") or validate(value)
    for block in parser.parse(text):
        linked = False
        for token in block.children or []:
            if token.type == "link_open":
                linked = True
                href = token.attrGet("href")
                if href:
                    yield href
            elif token.type == "link_close":
                linked = False
            elif token.type == "text" and not linked:
                for match in re.finditer(r"https?://[^\s<>\"']+", token.content):
                    href = match[0].rstrip(".,;!?")
                    while href.endswith(")") and href.count(")") > href.count("("):
                        href = href[:-1]
                    yield href


def _texts(item):
    if isinstance(item, str):
        yield item
    elif isinstance(item, list):
        for value in item:
            yield from _texts(value)
    elif isinstance(item, dict):
        for key in ("record", "text", "content", "summary", "output", "result", "aggregatedOutput", "message", "contentItems", "structuredContent"):
            if key in item:
                yield from _texts(item[key])


class FileLinks:
    def __init__(self, service):
        self.service = service
        service.db.execute("CREATE TABLE IF NOT EXISTS file_previews (session_id TEXT, item_id TEXT, href TEXT, artifact_id TEXT NOT NULL, PRIMARY KEY(session_id, item_id, href))")
        service.db.commit()

    async def item(self, session_id, item_id):
        service = self.service
        for key, (_, page) in reversed(service.pages.items()):
            if key[0] == session_id:
                for item in page.get("items", []):
                    if item.get("id") == item_id:
                        return item
        turn_id, separator, native_id = item_id.partition(":")
        if separator and turn_id and native_id:
            try:
                result = await service.rpc.call("thread/items/read", {"threadId": session_id, "turnId": turn_id, "itemIds": [native_id]})
                for entry in result.get("data", []):
                    if entry.get("turnId") == turn_id and entry.get("item", {}).get("id") == native_id:
                        return entry["item"]
                raise FileLinkError("Message item not found", 404)
            except RpcError as exc:
                if exc.code != -32601:
                    raise FileLinkError("Message item is unavailable", 404) from exc
        return await asyncio.to_thread(self.recorded_item, session_id, item_id)

    def recorded_item(self, session_id, item_id):
        history = self.service.history
        try:
            row = history.find(session_id)
        except KeyError:
            raise FileLinkError("Message item not found", 404) from None
        path = Path(row.get("rollout_path") or "").resolve()
        if not any(path.is_relative_to((history.home / directory).resolve()) for directory in ("sessions", "archived_sessions")) or not path.is_file():
            raise FileLinkError("Message item not found", 404)
        index, turn_id = -1, None
        target = re.fullmatch(r"record-(\d+)", item_id)
        with path.open("rb") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                if record.get("type") == "turn_context":
                    turn_id = payload.get("turn_id", turn_id)
                if record.get("type") not in ("response_item", "compacted"):
                    continue
                index += 1
                if (target and index == int(target[1])) or (not target and f"{turn_id}:{payload.get('id')}" == item_id):
                    return payload
        raise FileLinkError("Message item not found", 404)

    async def preview(self, session_id, body, origin):
        service = self.service
        snapshot = await service.sessions()
        session = next((s for s in snapshot["sessions"] if s["id"] == session_id), None)
        if session is None:
            raise KeyError("Session not found")
        item_id, href = body.get("item_id"), body.get("href")
        if not isinstance(item_id, str) or not item_id or len(item_id) > 512:
            raise FileLinkError("A message item ID is required")
        cwd = session.get("cwd")
        aliases = ()
        route = runtime(service.protocol.data_dir, "route.json")
        if (route and (route.get("backend_pid"), route.get("backend_identity")) == (os.getpid(), identity(os.getpid()))
                and origin == f"http://127.0.0.1:{route['port']}"):
            aliases = (route["public_url"],)
        document = "document_id" in body or "document_path" in body
        if document:
            document_id = body.get("document_id")
            if not isinstance(document_id, str):
                raise FileLinkError("This document is not linked from the selected message", 403)
            try:
                document_path = local_path(body.get("document_path"), cwd, origin, aliases)
            except (ValueError, OSError) as exc:
                raise FileLinkError("This document is not linked from the selected message", 403) from exc
            rows = service.db.execute("SELECT href FROM file_previews WHERE session_id=? AND item_id=? AND artifact_id=?", (session_id, item_id, document_id)).fetchall()
            for row in rows:
                try:
                    if local_path(row[0], cwd, origin, aliases) == document_path:
                        break
                except (ValueError, OSError):
                    continue
            else:
                raise FileLinkError("This document is not linked from the selected message", 403)
            texts = [service.protocol.artifacts.get(document_id, internal=True)["text"]]
            cwd = document_path.parent
        requested = local_path(href, cwd, origin, aliases)
        cache_key = str(requested) if document else href
        previous = service.db.execute("SELECT artifact_id FROM file_previews WHERE session_id=? AND item_id=? AND href=?", (session_id, item_id, cache_key)).fetchone()
        if previous and not document:
            return {**service.protocol.artifacts.get(previous[0]), "source_path": str(requested)}
        if not document:
            texts = _texts(await self.item(session_id, item_id))
        path = None
        for text in texts:
            for candidate in markdown_links(text):
                try:
                    recorded = local_path(candidate, cwd, origin, aliases)
                except (ValueError, OSError):
                    continue
                if recorded == requested:
                    path = recorded
                    break
            if path is not None:
                break
        if path is None:
            raise FileLinkError("This file is not linked from the selected message", 403)
        if previous:
            return {**service.protocol.artifacts.get(previous[0]), "source_path": str(path)}
        artifact = await asyncio.to_thread(self.prepare, path)
        service.db.execute("INSERT OR IGNORE INTO file_previews VALUES (?, ?, ?, ?)", (session_id, item_id, cache_key, artifact["id"]))
        service.db.commit()
        previous = service.db.execute("SELECT artifact_id FROM file_previews WHERE session_id=? AND item_id=? AND href=?", (session_id, item_id, cache_key)).fetchone()
        return {**service.protocol.artifacts.get(previous[0]), "source_path": str(path)}

    def prepare(self, path):
        try:
            if not path.is_file():
                if not path.exists():
                    raise FileLinkError("Linked file no longer exists", 404)
                raise FileLinkError("The linked path is not a regular file")
            with path.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise FileLinkError("The linked path is not a regular file")
                data = stream.read(MAX_FILE + 1)
        except FileNotFoundError as exc:
            raise FileLinkError("Linked file no longer exists", 404) from exc
        except OSError as exc:
            raise FileLinkError("The linked file could not be read") from exc
        if len(data) > MAX_FILE:
            raise FileLinkError("Files must be no larger than 25 MB")
        return self.service.protocol.artifacts.put({"name": path.name, "data": base64.b64encode(data).decode(), "kind": "document"})
