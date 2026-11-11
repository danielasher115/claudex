import asyncio
import json
import contextlib
from pathlib import Path

from . import __version__
from urllib.parse import urlsplit

from websockets.asyncio.client import connect, unix_connect
from websockets.exceptions import ConnectionClosed


class RpcError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def validate_url(url):
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in {"ws", "wss"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                 and parsed.port is not None and 1 <= parsed.port <= 65535 and parsed.username is None
                 and parsed.password is None and not parsed.fragment and not parsed.query)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("--codex-url must be a loopback WebSocket URL with an explicit port and no credentials or query")
    return url


class Rpc:
    def __init__(self, home: Path, socket: Path | str, token_file=None):
        self.home, self.socket = home, socket
        self.url = validate_url(socket) if isinstance(socket, str) and "://" in socket else None
        self.token_file = Path(token_file).expanduser() if token_file else None
        self.ws = None
        self.pending = {}
        self.counter = 0
        self.lock = asyncio.Lock()
        self.reader = None
        self.error = "Connecting to Codex"
        self.on_notification = None
        self.on_request = None
        self.requests = set()

    async def connect(self):
        async with self.lock:
            if self.ws is not None:
                return
            try:
                options = {"open_timeout": 3, "max_size": 64 * 1024 * 1024}
                if self.url:
                    if self.token_file:
                        token = self.token_file.read_text(encoding="utf-8").strip()
                        if not token or any(character.isspace() for character in token):
                            raise RpcError("Codex WebSocket token file must contain one nonempty bearer token")
                        options["additional_headers"] = {"Authorization": f"Bearer {token}"}
                    self.ws = await connect(self.url, proxy=None, **options)
                else:
                    self.ws = await unix_connect(str(self.socket), uri="ws://localhost", **options)
                self.reader = asyncio.create_task(self._read(self.ws))
                result = await self._request("initialize", {
                    "clientInfo": {"name": "codex_sessions", "title": "Claudex", "version": __version__},
                    "capabilities": {"experimentalApi": True},
                })
                if Path(result.get("codexHome", "")).resolve() != self.home.resolve():
                    raise RpcError("The daemon belongs to a different Codex home. Check --home and the Codex endpoint.")
                await self.ws.send(json.dumps({"method": "initialized"}))
                self.error = None
            except Exception as exc:
                await self.close()
                self.error = str(exc)
                raise RpcError(str(exc)) from exc

    async def _read(self, ws):
        try:
            async for message in ws:
                data = json.loads(message)
                if "method" in data:
                    if "id" in data and self.on_request is not None:
                        task = asyncio.create_task(self._handle_request(ws, data))
                        self.requests.add(task)
                        task.add_done_callback(self.requests.discard)
                    if self.on_notification is not None:
                        self.on_notification(data)
                    continue
                future = self.pending.pop(data.get("id"), None)
                if future is not None and not future.done():
                    if "error" in data:
                        future.set_exception(RpcError(data["error"].get("message", "Codex request failed"), data["error"].get("code")))
                    else:
                        future.set_result(data.get("result", {}))
        except Exception as exc:
            self.error = str(exc)
        finally:
            if self.ws is ws:
                self.ws = None
            self.error = self.error or "Codex disconnected"
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RpcError("Codex disconnected; delivery may be unknown"))
            self.pending.clear()

    async def _handle_request(self, ws, data):
        try:
            result = await self.on_request(data)
            response = {"id": data["id"], "result": result}
        except Exception as exc:
            response = {"id": data["id"], "error": {"code": -32603, "message": str(exc)}}
        try:
            await ws.send(json.dumps(response))
        except ConnectionClosed:
            pass

    async def _request(self, method, params):
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            ws = self.ws
            if ws is None:
                raise RpcError("Codex disconnected; delivery may be unknown")
            await ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
            return await asyncio.wait_for(future, 15)
        except ConnectionClosed as exc:
            raise RpcError("Codex disconnected; delivery may be unknown") from exc
        finally:
            self.pending.pop(request_id, None)

    async def call(self, method, params=None):
        await self.connect()
        return await self._request(method, params or {})

    async def close(self):
        for request in list(self.requests):
            request.cancel()
        if self.requests:
            await asyncio.gather(*list(self.requests), return_exceptions=True)
        ws, self.ws = self.ws, None
        if ws is not None:
            await ws.close()
        if self.reader is not None and self.reader is not asyncio.current_task():
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader
            self.reader = None
