import asyncio
import json
import os
import sys
from pathlib import Path

import pytest


def test_state_directory_uses_platform_conventions_and_override(monkeypatch, tmp_path):
    from codex_sessions.platform import default_data_dir
    monkeypatch.delenv("CLAUDEX_DATA_DIR", raising=False)
    monkeypatch.delenv("CODEX_SESSIONS_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert default_data_dir("linux") == tmp_path / "state" / "claudex"
    assert default_data_dir("win32") == tmp_path / "local" / "Claudex"
    assert default_data_dir("darwin") == Path.home() / "Library" / "Application Support" / "Claudex"
    monkeypatch.setenv("CLAUDEX_DATA_DIR", str(tmp_path / "override"))
    assert default_data_dir("win32") == tmp_path / "override"


def test_process_identity_and_runtime_reject_stale_pid(tmp_path):
    from codex_sessions.lifecycle import identity, runtime, save_runtime
    state = {"pid": os.getpid(), "identity": identity(os.getpid()), "port": 1234}
    save_runtime(tmp_path, state)
    assert runtime(tmp_path) == state
    save_runtime(tmp_path, {**state, "identity": "different-creation-time"})
    assert runtime(tmp_path) is None


@pytest.mark.parametrize("url", ["ws://example.com:1234", "ws://user:secret@localhost:1234", "http://127.0.0.1:1234", "ws://localhost:1234/#token"])
def test_rpc_rejects_remote_and_credential_urls(tmp_path, url):
    from codex_sessions.rpc import Rpc
    with pytest.raises(ValueError, match="loopback WebSocket"):
        Rpc(tmp_path, url)


def test_rpc_supports_authenticated_loopback_websocket(tmp_path):
    from codex_sessions.rpc import Rpc
    from websockets.asyncio.server import serve
    token_file = tmp_path / "token"
    token_file.write_text("test-local-secret\n")

    async def exercise():
        async def handler(ws):
            assert ws.request.headers["Authorization"] == "Bearer test-local-secret"
            request = json.loads(await ws.recv())
            assert request["method"] == "initialize"
            await ws.send(json.dumps({"id": request["id"], "result": {"codexHome": str(tmp_path)}}))
            assert json.loads(await ws.recv())["method"] == "initialized"
            request = json.loads(await ws.recv())
            await ws.send(json.dumps({"id": request["id"], "result": {"data": []}}))

        async with serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            rpc = Rpc(tmp_path, url, token_file=token_file)
            try:
                assert await rpc.call("model/list") == {"data": []}
                assert rpc.socket == url
            finally:
                await rpc.close()
    asyncio.run(exercise())


def test_stop_rechecks_creation_time_and_never_signals_reused_pid(monkeypatch, tmp_path):
    from codex_sessions import lifecycle
    state = {"pid": os.getpid(), "identity": "stale"}
    monkeypatch.setattr(lifecycle, "owner", lambda _: state)
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("Never signal a reused PID"))
    with pytest.raises(RuntimeError, match="changed"):
        lifecycle.stop(state, tmp_path)


def test_stop_waits_for_verified_process_and_lock_after_owner_metadata_expires(monkeypatch, tmp_path, capsys):
    from codex_sessions import lifecycle
    from types import SimpleNamespace
    state = {"pid": 1234, "identity": "verified"}
    process = SimpleNamespace(terminated=False)

    def terminate():
        process.terminated = True

    def owner(_):
        if process.terminated:
            raise RuntimeError("Server lock is held by an unrecognized process. No process was stopped.")
        return state

    process.terminate = terminate
    process.is_running = lambda: not process.terminated
    lock_states = iter([True, False])
    monkeypatch.setattr(lifecycle, "owner", owner)
    monkeypatch.setattr(lifecycle, "verified_process", lambda *_: process)
    monkeypatch.setattr(lifecycle, "lock_held", lambda _: next(lock_states))
    lifecycle.stop(state, tmp_path)
    assert "Stopped Claudex" in capsys.readouterr().out
    assert list(lock_states) == []


def test_lock_owner_requires_recognized_command(monkeypatch, tmp_path):
    from codex_sessions.lifecycle import identity, owner, save_runtime
    from codex_sessions.platform import file_lock
    save_runtime(tmp_path, {"pid": os.getpid(), "identity": identity(os.getpid())}, "server.owner.json")
    with file_lock(tmp_path / "server.lock"):
        with pytest.raises(RuntimeError, match="unrecognized"):
            owner(tmp_path)


@pytest.mark.skipif(sys.platform != "linux", reason="Windows drive translation is specific to WSL")
def test_wsl_attachment_paths_accept_windows_drive_syntax(monkeypatch, tmp_path):
    from codex_sessions.platform import local_path
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert local_path(r"C:\Users\someone\file.txt") == Path("/mnt/c/Users/someone/file.txt")
    assert local_path("D:/work/file.txt") == Path("/mnt/d/work/file.txt")
    assert local_path(tmp_path / "ordinary file") == tmp_path / "ordinary file"
    monkeypatch.delenv("WSL_DISTRO_NAME")
    assert local_path(r"C:\Users\someone\file.txt") == Path(r"C:\Users\someone\file.txt")


def test_background_command_flags_never_request_a_windows_console(monkeypatch):
    from types import SimpleNamespace
    from codex_sessions import platform
    from codex_sessions.platform import quiet_process_options
    monkeypatch.setattr(platform.subprocess, "STARTUPINFO", lambda: SimpleNamespace(dwFlags=0, wShowWindow=1), raising=False)
    for name, value in {"CREATE_NO_WINDOW": 0x08000000, "CREATE_NEW_PROCESS_GROUP": 0x200, "STARTF_USESHOWWINDOW": 1, "SW_HIDE": 0}.items():
        monkeypatch.setattr(platform.subprocess, name, value, raising=False)
    options = quiet_process_options("win32")
    assert options["creationflags"] == 0x08000000
    assert options["startupinfo"].dwFlags == 1
    assert options["startupinfo"].wShowWindow == 0
    assert quiet_process_options("linux") == {}
    assert quiet_process_options("darwin") == {}
    monkeypatch.setattr(platform.sys, "platform", "win32")
    background = platform.detached_process_options()
    assert background["creationflags"] == 0x08000200
    assert not background["creationflags"] & (0x8 | 0x10)
    assert background["startupinfo"].dwFlags == 1
    assert background["startupinfo"].wShowWindow == 0
    monkeypatch.setattr(platform.sys, "platform", "linux")
    assert platform.detached_process_options() == {"start_new_session": True}


@pytest.mark.parametrize('value', [r'\\server\share\file.png', '//server/share/file.png', r'\\?\C:\private\file'])
def test_network_and_device_paths_are_rejected_before_resolution(value):
    from codex_sessions.platform import local_path
    with pytest.raises(ValueError, match='local filesystem'):
        local_path(value)


@pytest.mark.skipif(sys.platform == 'win32', reason='Windows uses user-profile ACL inheritance')
def test_state_directory_is_private_before_runtime_files_are_written(tmp_path):
    import stat
    from codex_sessions.lifecycle import operation
    folder = tmp_path / 'state'
    folder.mkdir(mode=0o755)
    with operation(folder):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_start_accepts_verified_server_beneath_python_redirector(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    from codex_sessions import lifecycle, platform
    launcher = SimpleNamespace(pid=100, is_running=lambda: True)
    server = SimpleNamespace(pid=101, parents=lambda: [launcher])
    state = {"pid": 101, "identity": "server-created", "port": 4321}
    owners = iter([None, state])
    monkeypatch.setattr(lifecycle, "owner", lambda _: next(owners))
    monkeypatch.setattr(platform, "verified_process", lambda *_: server)
    monkeypatch.setattr(lifecycle.psutil, "Process", lambda _: launcher)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=100, poll=lambda: None))
    monkeypatch.setattr(lifecycle, "ready", lambda _: True)
    args = SimpleNamespace(home=tmp_path, port=4321, address_port=None, socket=None)
    lifecycle.start(args, tmp_path)
    assert "Started Claudex (PID 101)" in capsys.readouterr().out


@pytest.mark.parametrize("launcher_alive,related", [(True, False), (False, True)])
def test_spawned_server_refuses_unrelated_or_reused_launcher(monkeypatch, tmp_path, launcher_alive, related):
    from types import SimpleNamespace
    from codex_sessions import platform
    launcher = SimpleNamespace(pid=100, is_running=lambda: launcher_alive)
    server = SimpleNamespace(pid=101, parents=lambda: [launcher] if related else [])
    monkeypatch.setattr(platform, "verified_process", lambda *_: server)
    assert platform.spawned_server(launcher, {"pid": 101, "identity": "server-created"}, tmp_path) is None


def test_route_accepts_verified_descendant_beneath_python_redirector(monkeypatch, tmp_path, capsys):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from codex_sessions import address, platform
    launcher = SimpleNamespace(pid=100, is_running=lambda: True)
    route_process = SimpleNamespace(pid=101, parents=lambda: [launcher])
    state = {"pid": 80, "identity": "backend-created", "port": 4321, "public_url": "http://localhost:4321"}
    route = {"pid": 101, "identity": "route-created"}
    monkeypatch.setattr(address, "operation", lambda _: nullcontext())
    monkeypatch.setattr(address, "owner", lambda _: state)
    monkeypatch.setattr(address, "runtime", lambda *_: route)
    monkeypatch.setattr(platform, "verified_process", lambda *_: route_process)
    monkeypatch.setattr(address.psutil, "Process", lambda _: launcher)
    monkeypatch.setattr(address.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=100, poll=lambda: None))
    monkeypatch.setattr(address, "ready", lambda _: True)
    args = SimpleNamespace(data_dir=tmp_path, address_port=8765)
    address.start_route(args)
    assert "Address attached to Claudex" in capsys.readouterr().out
