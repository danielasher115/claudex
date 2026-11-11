"""Loopback HTTP adapter for the agent gateway and bundled dashboard."""
import asyncio
import json
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from .gateway import Gateway
from .rpc import RpcError
from .file_links import FileLinkError


def create_app(home, socket, data_dir, port=8766, *, codex_token_file=None):
    service = Gateway(Path(home), socket, Path(data_dir), codex_token_file=codex_token_file)
    service.protocol.port = port

    @asynccontextmanager
    async def lifespan(app):
        async with service.running():
            yield

    async def api(request: Request):
        try:
            path = request.url.path
            session_id = request.path_params.get("session_id")
            if path == "/api/setup":
                return JSONResponse({"default_cwd": str(Path.home()), "config": {"mcpServers": {"codex": {"type": "stdio", "command": sys.executable,
                    "args": ["-m", "codex_sessions", "mcp", "--data-dir", str(Path(data_dir).resolve())],
                    "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}}}}})
            if path == "/api/models":
                return JSONResponse(await service.models.get(refresh=request.query_params.get("refresh") == "1"))
            if request.method == "POST":
                if int(request.headers.get("content-length", "0")) > 36 * 1024 * 1024:
                    return JSONResponse({"error": "Message too large"}, 413)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"error": "JSON required"}, 415)
                if path.startswith("/api/mcp/") or path == "/api/protocol":
                    if not secrets.compare_digest(request.headers.get("x-codex-token", ""), service.token):
                        return JSONResponse({"error": "Bridge authentication required"}, 403)
                elif request.headers.get("origin") != str(request.base_url).rstrip("/"):
                    return JSONResponse({"error": "Same-origin request required"}, 403)
                body = json.loads(await request.body())
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                if path == "/api/protocol":
                    if body.get("protocol_version") != "1":
                        raise ValueError("Unsupported protocol_version; expected '1'")
                    return JSONResponse({"protocol_version": "1", "result": await service.protocol.dispatch(body.get("method"), body.get("params", {}))})
                if path == "/api/artifacts":
                    return JSONResponse(await service.protocol.dispatch("artifacts.put", body))
                if path.endswith("/files/preview"):
                    return JSONResponse(await service.file_links.preview(session_id, body, str(request.base_url).rstrip("/")))
                if path == "/api/agents":
                    return JSONResponse(await service.protocol.launch(body))
                if path == "/api/mcp/launch":
                    return JSONResponse(await service.launch(body))
                if path == "/api/mcp/reply":
                    session_id = body.get("threadId") or body.get("conversationId")
                    return JSONResponse(await service.reply(session_id, body.get("prompt")))
                if request.path_params.get("message_id"):
                    if path.endswith("/steer"):
                        return JSONResponse(await service.steer_queued(session_id, request.path_params["message_id"], body.get("expected_turn_id")))
                    return JSONResponse(service.cancel(session_id, request.path_params["message_id"]))
                if path.endswith("/stop"):
                    return JSONResponse(await service.protocol.dispatch("agents.stop", {"thread_id": session_id, "include_children": body.get("include_children", True)}))
                return JSONResponse(await service.send(session_id, body))
            if path == "/api/sessions":
                return JSONResponse(await service.sessions())
            if path.endswith("/details"):
                return JSONResponse(await service.details.get(session_id, refresh=request.query_params.get("refresh") == "1",
                                                              section=request.query_params.get("section", "all")))
            if path.endswith("/history"):
                return JSONResponse(await service.page(session_id, request.query_params.get("cursor")))
            if path.endswith("/view"):
                history = await service.page(session_id, compact=True)
                return JSONResponse({"session": await service.session(session_id), "history": history, "children": service.protocol.children(session_id)})
            return JSONResponse(await service.session(session_id))
        except FileLinkError as exc:
            return JSONResponse({"error": str(exc)}, exc.status)
        except KeyError:
            return JSONResponse({"error": "Session not found"}, 404)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, 400)
        except (RpcError, OSError, TimeoutError) as exc:
            return JSONResponse({"error": str(exc)}, 503)

    async def events(request):
        async def stream():
            previous = None
            heartbeat = time.monotonic()
            while not await request.is_disconnected():
                snapshot = json.dumps(await service.sessions(), separators=(",", ":"))
                if snapshot != previous:
                    yield f"data: {snapshot}\n\n"
                    previous = snapshot
                    heartbeat = time.monotonic()
                elif time.monotonic() - heartbeat > 15:
                    yield ": heartbeat\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    static = Path(__file__).parent / "static"

    async def index(request):
        return FileResponse(static / "index.html")

    async def artifact_content(request):
        try:
            artifact = service.protocol.artifacts.get(request.path_params["artifact_id"], internal=True)
            return FileResponse(artifact["path"], media_type=artifact["mime"], filename=artifact["name"], content_disposition_type="inline" if artifact["kind"] == "image" else "attachment", headers={"X-Content-Type-Options": "nosniff"})
        except ValueError:
            return JSONResponse({"error": "Attachment not found"}, 404)

    async def artifact_preview(request):
        try:
            artifact = service.protocol.artifacts.get(request.path_params["artifact_id"], internal=True)
            return JSONResponse({"name": artifact["name"], "mime": artifact["mime"], "text": artifact["text"], "summary": artifact.get("summary", ""),
                                 "images": [f"/api/artifacts/{artifact['id']}/pages/{index}" for index in range(len(artifact["image_paths"]))]})
        except ValueError:
            return JSONResponse({"error": "Attachment not found"}, 404)

    async def artifact_page(request):
        try:
            artifact = service.protocol.artifacts.get(request.path_params["artifact_id"], internal=True)
            index = int(request.path_params["index"])
            if not 0 <= index < len(artifact["image_paths"]):
                raise ValueError("Unknown page")
            page = Path(artifact["image_paths"][index]).resolve()
            if not page.is_relative_to((service.protocol.artifacts.root / artifact["id"]).resolve()):
                raise ValueError("Unknown page")
            return FileResponse(page, headers={"X-Content-Type-Options": "nosniff"})
        except (ValueError, IndexError):
            return JSONResponse({"error": "Attachment page not found"}, 404)

    routes = [Route("/api/protocol", api, methods=["POST"]), Route("/api/artifacts", api, methods=["POST"]),
              Route("/api/artifacts/{artifact_id}/content", artifact_content), Route("/api/agents", api, methods=["POST"]),
              Route("/api/artifacts/{artifact_id}/preview", artifact_preview), Route("/api/artifacts/{artifact_id}/pages/{index}", artifact_page),
              Route("/api/models", api), Route("/api/setup", api), Route("/api/sessions", api), Route("/api/sessions/{session_id}", api),
              Route("/api/sessions/{session_id}/view", api), Route("/api/sessions/{session_id}/details", api),
              Route("/api/sessions/{session_id}/files/preview", api, methods=["POST"]),
              Route("/api/sessions/{session_id}/history", api), Route("/api/sessions/{session_id}/messages", api, methods=["POST"]),
              Route("/api/sessions/{session_id}/stop", api, methods=["POST"]),
              Route("/api/sessions/{session_id}/messages/{message_id}/cancel", api, methods=["POST"]),
              Route("/api/sessions/{session_id}/messages/{message_id}/steer", api, methods=["POST"]),
              Route("/api/mcp/launch", api, methods=["POST"]), Route("/api/mcp/reply", api, methods=["POST"]), Route("/api/events", events)]
    if (static / "assets").exists():
        routes.append(Mount("/assets", StaticFiles(directory=static / "assets")))
    routes.append(Route("/{path:path}", index))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "codex.localhost"])
    app.state.service = service
    return app
