"""Manage a local dashboard after verifying its lock and process identity."""
import http.client
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import psutil

from .platform import detached_process_options, file_lock, identity, lock_held, verified_process, private_directory, spawned_server


def runtime(data_dir, filename="server.json"):
    try:
        state = json.loads((Path(data_dir) / filename).read_text(encoding="utf-8"))
        if state["identity"] == identity(state["pid"]):
            return state
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    return None


def save_runtime(data_dir, state, filename="server.json"):
    path = Path(data_dir) / filename
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def owner(data_dir):
    if not (data_dir / "server.lock").exists() or not lock_held(data_dir / "server.lock"):
        return None
    state = runtime(data_dir, "server.owner.json")
    if not state:
        if not lock_held(data_dir / "server.lock"):
            return None
        raise RuntimeError("Server lock is held by an unrecognized process. No process was stopped.")
    verified_process(state, data_dir)
    saved = runtime(data_dir)
    if saved and (saved["pid"], saved["identity"]) == (state["pid"], state["identity"]):
        state.update(saved)
    route = runtime(data_dir, "route.json")
    if route and (route.get("backend_pid"), route.get("backend_identity")) == (state["pid"], state["identity"]):
        state.update(public_url=route["public_url"], address_port=route["address_port"], requested_port=0)
    return state


@contextmanager
def operation(data_dir):
    private_directory(data_dir)
    try:
        with file_lock(data_dir / "launcher.lock", timeout=12):
            yield
    except BlockingIOError as exc:
        raise RuntimeError("Another dashboard start or stop command is still running. Try again shortly.") from exc


def describe(state, data_dir, prefix="Running"):
    url = state.get("public_url") or f"http://localhost:{state['port']}"
    print(f"{prefix} Claudex (PID {state['pid']}) at {url}")
    log = data_dir / "dashboard.log"
    print(f"Log: {log}" if state.get("background") else "Log: attached terminal")


def ready(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=.3)
    try:
        connection.request("GET", "/")
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def start(args, data_dir, previous=None):
    state = owner(data_dir)
    if state:
        describe(state, data_dir, "Already running")
        return
    previous = previous or {}
    home = (args.home or Path(previous.get("home", Path.home() / ".codex"))).expanduser().resolve()
    port = args.port if args.port is not None else previous.get("requested_port", previous.get("port", 0))
    address_port = args.address_port if args.address_port is not None else previous.get("address_port")
    socket = args.socket or (previous.get("socket") if args.home is None else None)
    codex_url = getattr(args, "codex_url", None) or (previous.get("codex_url") if args.home is None else None)
    token_file = getattr(args, "codex_token_file", None) or previous.get("codex_token_file")
    command = [sys.executable, "-m", "codex_sessions", "serve", "--home", str(home), "--port", str(port), "--data-dir", str(data_dir)]
    if codex_url:
        command += ["--codex-url", codex_url]
    elif socket:
        command += ["--socket", str(Path(socket).expanduser().resolve())]
    if token_file:
        command += ["--codex-token-file", str(Path(token_file).expanduser().resolve())]
    if address_port is not None:
        command += ["--address-port", str(address_port)]
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "CLAUDEX_BACKGROUND": "1", "PYTHONPATH": os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")]))}
    log = data_dir / "dashboard.log"
    with log.open("ab") as output:
        process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                   **detached_process_options(), close_fds=True)
    try:
        launcher = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        launcher = None
    launched_state = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process.poll() is None:
        try:
            state = owner(data_dir)
        except RuntimeError:
            time.sleep(.1)
            continue
        if state and launcher is not None and spawned_server(launcher, state, data_dir) is not None:
            launched_state = state
            if ready(state["port"]):
                describe(state, data_dir, "Started")
                return
        time.sleep(.1)
    if launched_state:
        stop(launched_state, data_dir)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Dashboard startup timed out; PID {process.pid} is still exiting. Check {log}")
    with log.open("rb") as output:
        output.seek(max(0, output.seek(0, 2) - 4000))
        detail = output.read().decode(errors="replace").strip()
    raise RuntimeError(f"Dashboard failed to start. Check {log}\n{detail}")


def stop(state, data_dir):
    if not state:
        print("Claudex is already stopped.")
        return
    route = runtime(data_dir, "route.json")
    current = owner(data_dir)
    if not current or (current["pid"], current["identity"]) != (state["pid"], state["identity"]):
        raise RuntimeError("The dashboard process changed. Run status before retrying.")
    process = verified_process(state, data_dir)
    try:
        process.terminate()
    except psutil.NoSuchProcess:
        pass
    except psutil.Error as exc:
        raise RuntimeError("The dashboard process could not be stopped.") from exc
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            exited = not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            exited = True
        if exited and not lock_held(data_dir / "server.lock"):
            if route and (route.get("backend_pid"), route.get("backend_identity")) == (state["pid"], state["identity"]):
                while runtime(data_dir, "route.json") == route and time.monotonic() < deadline:
                    time.sleep(.1)
                if runtime(data_dir, "route.json") == route:
                    raise RuntimeError(f"The local address is still stopping. Check {data_dir / 'address.log'}")
            print("Stopped Claudex.")
            return
        time.sleep(.1)
    raise RuntimeError(f"Dashboard PID {state['pid']} is still stopping. Check {data_dir / 'dashboard.log'}")


def run(args):
    data_dir = args.data_dir.expanduser().resolve()
    with operation(data_dir):
        state = owner(data_dir)
        if args.command == "status":
            describe(state, data_dir) if state else print("Claudex is stopped.")
        elif args.command == "stop":
            stop(state, data_dir)
        elif args.command == "restart":
            stop(state, data_dir)
            start(args, data_dir, state)
        else:
            start(args, data_dir)
