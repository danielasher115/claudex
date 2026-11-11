import os
import json
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

from codex_sessions.lifecycle import runtime
from codex_sessions.platform import spawned_server

ROOT = Path(__file__).resolve().parents[1]


def cli(tmp_path, command, *options):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    transport = ["--codex-url", "ws://127.0.0.1:9"] if sys.platform == "win32" and command in {"start", "serve"} else []
    return subprocess.run([sys.executable, "-m", "codex_sessions", command, "--data-dir", str(tmp_path / "data"), *transport, *map(str, options)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_background_lifecycle_works_outside_checkout_and_preserves_restart_settings(tmp_path):
    port = free_port()
    options = ["--port", port, "--home", tmp_path / "codex-home"]
    try:
        started = cli(tmp_path, "start", *options)
        assert started.returncode == 0, started.stderr
        assert f"http://localhost:{port}" in started.stdout
        assert httpx.get(f"http://127.0.0.1:{port}").status_code == 200
        first = cli(tmp_path, "status")
        assert first.returncode == 0 and "PID" in first.stdout
        assert str(port) in first.stdout
        assert cli(tmp_path, "start", *options).returncode == 0
        assert cli(tmp_path, "status").stdout == first.stdout
        assert cli(tmp_path, "restart", "--port", -1).returncode != 0
        assert cli(tmp_path, "status").stdout == first.stdout
        restarted = cli(tmp_path, "restart")
        assert restarted.returncode == 0, restarted.stderr
        assert str(port) in restarted.stdout
        assert cli(tmp_path, "status").stdout != first.stdout
        assert httpx.get(f"http://127.0.0.1:{port}").status_code == 200
        assert (tmp_path / "data" / "dashboard.log").is_file()
    finally:
        cli(tmp_path, "stop")
    assert "stopped" in cli(tmp_path, "status").stdout.lower()
    assert cli(tmp_path, "stop").returncode == 0


def test_stop_refuses_to_signal_an_unrelated_lock_holder(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    script = "import sys,time; from filelock import FileLock; lock=FileLock(sys.argv[1]); lock.acquire(); print('ready',flush=True); time.sleep(30)"
    process = subprocess.Popen([sys.executable, "-c", script, str(data / "server.lock")], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        stopped = cli(tmp_path, "stop")
        assert stopped.returncode != 0
        assert process.poll() is None
    finally:
        process.terminate(); process.wait(timeout=5)


def test_failed_start_reports_port_conflict_without_stopping_its_owner(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen()
        port = listener.getsockname()[1]
        started = cli(tmp_path, "start", "--port", port, "--home", tmp_path / "home")
        assert started.returncode != 0
        assert "unavailable" in started.stderr
        assert "stopped" in cli(tmp_path, "status").stdout.lower()
        assert listener.getsockname()[1] == port


def test_concurrent_starts_keep_one_server_and_foreground_servers_are_manageable(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    port = free_port()
    command = [sys.executable, "-m", "codex_sessions", "start", "--data-dir", str(tmp_path / "data"), "--port", str(port), "--home", str(tmp_path / "home")]
    if sys.platform == "win32":
        command += ["--codex-url", "ws://127.0.0.1:9"]
    try:
        calls = [subprocess.Popen(command, cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [process.communicate(timeout=20) for process in calls]
        assert all(process.returncode == 0 for process in calls), outputs
        assert sum("Started Claudex" in stdout for stdout, _ in outputs) == 1
        assert sum("Already running" in stdout for stdout, _ in outputs) == 1
    finally:
        cli(tmp_path, "stop")
    command[3] = "serve"
    with (tmp_path / "foreground.log").open("w") as output:
        foreground = subprocess.Popen(command, cwd=tmp_path, env=env, stdout=output, stderr=output)
        launcher = psutil.Process(foreground.pid)
        try:
            import time
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = cli(tmp_path, "status")
                state = runtime(tmp_path / "data")
                if state and spawned_server(launcher, state, tmp_path / "data") is not None:
                    try:
                        if httpx.get(f"http://127.0.0.1:{port}", timeout=.3).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                time.sleep(.05)
            assert state and f"PID {state['pid']}" in status.stdout
            assert cli(tmp_path, "stop").returncode == 0
            assert abs(foreground.wait(timeout=5)) in (0, 1, signal.SIGTERM)
        finally:
            if foreground.poll() is None:
                foreground.terminate(); foreground.wait(timeout=5)


def test_named_address_uses_available_backend_and_clients_discover_it_after_restart(tmp_path):
    address_port = free_port()
    try:
        started = cli(tmp_path, "start", "--address-port", address_port, "--home", tmp_path / "home")
        assert started.returncode == 0, started.stderr
        assert f"http://codex.localhost:{address_port}" in started.stdout
        state = json.loads((tmp_path / "data" / "server.json").read_text())
        backend_port = state["port"]
        assert backend_port != address_port
        with httpx.Client(base_url=f"http://127.0.0.1:{address_port}", headers={"host": f"codex.localhost:{address_port}"}, trust_env=False) as browser:
            assert browser.get("/").status_code == 200
            setup = browser.get("/api/setup").json()
            assert "--port" not in setup["config"]["mcpServers"]["codex"]["args"]
            rejected = browser.post("/api/sessions/missing/stop", headers={"origin": "http://foreign.example"}, json={})
            assert rejected.status_code == 403
        # Exercise the same client used by old MCP configurations with a stale explicit port.
        from codex_sessions.client import call
        result = call("protocol.guide", {}, 8766, tmp_path / "data")
        assert result
        restarted = cli(tmp_path, "restart")
        assert restarted.returncode == 0, restarted.stderr
        assert f"http://codex.localhost:{address_port}" in restarted.stdout
        assert httpx.get(f"http://127.0.0.1:{address_port}").status_code == 200
    finally:
        cli(tmp_path, "stop")


def test_named_address_conflict_does_not_fall_back_to_an_unstable_url(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen()
        port = listener.getsockname()[1]
        started = cli(tmp_path, "start", "--address-port", port, "--home", tmp_path / "home")
        assert started.returncode != 0
        assert "--address-port" in started.stderr
        assert "unavailable" in started.stderr
        assert "stopped" in cli(tmp_path, "status").stdout.lower()


def test_alias_attaches_to_running_server_streams_events_and_stops_with_it(tmp_path):
    backend_port, address_port = free_port(), free_port()
    try:
        started = cli(tmp_path, "start", "--port", backend_port, "--home", tmp_path / "home")
        assert started.returncode == 0, started.stderr
        (tmp_path / "data" / "server.json").unlink()
        first = cli(tmp_path, "status").stdout.split("PID ")[1].split(")")[0]
        attached = cli(tmp_path, "route", "--address-port", address_port)
        assert attached.returncode == 0, attached.stderr
        assert f"http://codex.localhost:{address_port}" in attached.stdout
        assert f"PID {first}" in cli(tmp_path, "status").stdout
        from codex_sessions.client import call
        assert call("protocol.guide", {}, 8766, tmp_path / "data")
        with httpx.Client(base_url=f"http://127.0.0.1:{address_port}", headers={"host": f"codex.localhost:{address_port}"}, trust_env=False, timeout=3) as browser:
            assert browser.get("/").status_code == 200
            assert browser.get("/", headers={"host": "attacker.example"}).status_code == 400
            assert browser.post("/api/artifacts", json={}, headers={"origin": "http://attacker.example"}).status_code == 403
            assert browser.post("/api/artifacts", json={}, headers={"origin": f"http://codex.localhost:{address_port}"}).status_code == 400
            with browser.stream("GET", "/api/events") as events:
                assert events.status_code == 200
                assert next(events.iter_lines()).startswith("data:")
        assert cli(tmp_path, "route", "--address-port", address_port).returncode == 0
        moved = cli(tmp_path, "route", "--address-port", free_port())
        assert moved.returncode != 0
        assert f"already attached at http://codex.localhost:{address_port}" in moved.stderr
        restarted = cli(tmp_path, "restart")
        assert restarted.returncode == 0, restarted.stderr
        assert f"http://codex.localhost:{address_port}" in restarted.stdout
        assert json.loads((tmp_path / "data" / "server.json").read_text())["requested_port"] == 0
        assert httpx.get(f"http://127.0.0.1:{address_port}", trust_env=False).status_code == 200
    finally:
        cli(tmp_path, "stop")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{address_port}", timeout=.2, trust_env=False)
        except httpx.ConnectError:
            break
        time.sleep(.1)
    else:
        raise AssertionError("Local address listener remained running after its backend stopped")


def test_singular_foreground_console_script_is_manageable(tmp_path):
    entrypoint = tmp_path / "codex-session"
    entrypoint.write_text("from codex_sessions.__main__ import main\nmain()\n")
    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    command = [sys.executable, str(entrypoint), "serve", "--data-dir", str(tmp_path / "data"), "--port", str(port), "--home", str(tmp_path / "home")]
    if sys.platform == "win32":
        command += ["--codex-url", "ws://127.0.0.1:9"]
    with (tmp_path / "foreground.log").open("w") as output:
        foreground = subprocess.Popen(command, cwd=tmp_path, env=env, stdout=output, stderr=output)
        launcher = psutil.Process(foreground.pid)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}", timeout=.2, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.05)
            status = cli(tmp_path, "status")
            assert status.returncode == 0, status.stderr
            state = runtime(tmp_path / "data")
            assert state and spawned_server(launcher, state, tmp_path / "data") is not None
            assert f"PID {state['pid']}" in status.stdout
            assert cli(tmp_path, "stop").returncode == 0
            assert abs(foreground.wait(timeout=5)) in (0, 1, signal.SIGTERM)
        finally:
            if foreground.poll() is None:
                foreground.terminate(); foreground.wait(timeout=5)
