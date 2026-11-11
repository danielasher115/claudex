"""Loopback listeners and a streaming alias for an already running dashboard."""
import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import psutil
import uvicorn
from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .lifecycle import describe, identity, operation, owner, ready, runtime, save_runtime
from .platform import detached_process_options, file_lock, loopback_listener, spawned_server


def public_url(port, data_dir=None):
    if data_dir is not None:
        try:
            config = json.loads((Path(data_dir) / "address.json").read_text(encoding="utf-8"))
            if config.get("listen_port") == port and config.get("public_port") == 80:
                port = 80
        except (OSError, ValueError, AttributeError):
            pass
    return "http://codex.localhost" + (f":{port}" if port != 80 else "")


def listener(port, named=False):
    try:
        return loopback_listener(port)
    except OSError as exc:
        if named:
            raise OSError(f"Local address port {port} is unavailable; choose another with --address-port: {exc}") from exc
        raise


def proxy_app(port):
    upstream = f"http://127.0.0.1:{port}"

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(base_url=upstream, timeout=None, trust_env=False) as client:
            app.state.client = client
            yield

    async def forward(request):
        origin = request.headers.get("origin")
        if origin is not None and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "Same-origin request required"}, 403)
        headers = {name: value for name, value in request.headers.items()
                   if name not in {"host", "connection", "transfer-encoding", "forwarded", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-for"}}
        if origin is not None:
            headers["origin"] = upstream
        path = request.url.path + ("?" + request.url.query if request.url.query else "")
        outgoing = request.app.state.client.build_request(request.method, path, headers=headers, content=request.stream())
        try:
            response = await request.app.state.client.send(outgoing, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"error": "Dashboard is unavailable"}, 502)

        async def chunks():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()

        response_headers = {name: value for name, value in response.headers.items()
                            if name not in {"connection", "transfer-encoding"}}
        return StreamingResponse(chunks(), status_code=response.status_code, headers=response_headers)

    app = Starlette(routes=[Route("/{path:path}", forward, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])], lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["codex.localhost", "127.0.0.1", "localhost", "[::1]"])
    return app


def start_route(args):
    data_dir = args.data_dir.expanduser().resolve()
    with operation(data_dir):
        state = owner(data_dir)
        if not state:
            raise RuntimeError("Start the dashboard before attaching its local address.")
        if state.get("address_port"):
            if args.address_port is not None and args.address_port != state["address_port"]:
                raise RuntimeError(f"The local address is already attached at {state['public_url']}. Use restart --address-port {args.address_port} to change it.")
            describe(state, data_dir, "Already running")
            return
        address_port = args.address_port or 8765
        command = [sys.executable, "-m", "codex_sessions.address", str(data_dir), str(state["port"]), str(address_port),
                   str(state["pid"]), state["identity"]]
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")]))}
        log = data_dir / "address.log"
        with log.open("ab") as output:
            process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       **detached_process_options(), close_fds=True)
        try:
            launcher = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            launcher = None
        launched_route = None
        deadline = time.monotonic() + 8
        while process.poll() is None and time.monotonic() < deadline:
            route = runtime(data_dir, "route.json")
            if route and launcher is not None and spawned_server(launcher, route, data_dir, "codex_sessions.address") is not None:
                launched_route = route
                if ready(address_port):
                    describe(owner(data_dir), data_dir, "Address attached to")
                    return
            time.sleep(.1)
        if launched_route and launcher is not None:
            route_process = spawned_server(launcher, launched_route, data_dir, "codex_sessions.address")
            if route_process is not None:
                try:
                    route_process.terminate()
                except psutil.NoSuchProcess:
                    pass
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise RuntimeError(f"Local address failed; choose another port with --address-port. Check {log}\n{log.read_text(encoding='utf-8')[-2000:]}")


async def serve_route(data_dir, port, address_port, backend_pid, backend_identity):
    with file_lock(data_dir / "route.lock"):
        sock = listener(address_port, named=True)
        try:
            save_runtime(data_dir, {"pid": os.getpid(), "identity": identity(os.getpid()), "port": port, "backend_pid": backend_pid,
                         "backend_identity": backend_identity, "address_port": address_port, "public_url": public_url(address_port, data_dir)}, "route.json")
            server = uvicorn.Server(uvicorn.Config(proxy_app(port), proxy_headers=False, timeout_graceful_shutdown=2))

            async def monitor():
                while not server.should_exit:
                    await asyncio.sleep(.2)
                    try:
                        current = owner(data_dir)
                        if current and (current["pid"], current["identity"]) == (backend_pid, backend_identity):
                            continue
                    except (OSError, RuntimeError):
                        pass
                    server.should_exit = True

            watcher = asyncio.create_task(monitor())
            try:
                await server.serve(sockets=[sock])
            finally:
                watcher.cancel()
        finally:
            sock.close()


if __name__ == "__main__":
    asyncio.run(serve_route(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]))
