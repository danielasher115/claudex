"""Local state, process identity, and OS locks for all supported hosts."""
import os
import re
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import psutil
from filelock import FileLock, Timeout


def default_data_dir(platform=None):
    override = os.environ.get("CLAUDEX_DATA_DIR") or os.environ.get("CODEX_SESSIONS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    platform = platform or sys.platform
    if platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Claudex"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claudex"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "claudex"


def local_path(value):
    value = str(value)
    if value.replace("\\", "/").startswith("//"):
        raise ValueError("Only local filesystem paths are supported; network and device paths are not allowed")
    if sys.platform == "linux" and os.environ.get("WSL_DISTRO_NAME") and re.match(r"^[A-Za-z]:[\\/]", value):
        value = "/mnt/" + value[0].lower() + "/" + value[3:].replace("\\", "/")
    return Path(value).expanduser()


def loopback_listener(port):
    sock = socket.socket()
    try:
        if sys.platform == "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        return sock
    except OSError:
        sock.close()
        raise


@contextmanager
def file_lock(path, timeout=0):
    try:
        with FileLock(str(path), timeout=timeout):
            yield
    except Timeout as exc:
        raise BlockingIOError(f"Lock is held: {path}") from exc


def lock_held(path):
    try:
        with file_lock(path):
            return False
    except BlockingIOError:
        return True


def identity(pid):
    process = psutil.Process(pid)
    if process.status() == psutil.STATUS_ZOMBIE:
        raise ProcessLookupError(pid)
    return str(process.create_time())


def verified_process(state, data_dir, module="codex_sessions"):
    """Return one psutil handle so its PID reuse guard also protects terminate()."""
    try:
        process = psutil.Process(state["pid"])
        if identity(process.pid) != state["identity"]:
            raise RuntimeError("The dashboard process changed. Run status before retrying.")
        current = psutil.Process()
        same_user = process.uids().real == current.uids().real if hasattr(os, "getuid") else process.username().casefold() == current.username().casefold()
        argv = process.cmdline()
        module_index = next((index for index in range(len(argv) - 1) if argv[index:index + 2] == ["-m", module]), None)
        entrypoint = module == "codex_sessions" and any(Path(arg).name.lower() in {"claudex", "codex-session", "codex-sessions", "claudex.exe", "codex-session.exe", "codex-sessions.exe"} for arg in argv[:2])
        configured_dir = default_data_dir()
        if module == "codex_sessions.address" and module_index is not None:
            configured_dir = Path(argv[module_index + 2]).expanduser()
        for index, arg in enumerate(argv):
            if arg == "--data-dir" and index + 1 < len(argv):
                configured_dir = Path(argv[index + 1]).expanduser()
            elif arg.startswith("--data-dir="):
                configured_dir = Path(arg.split("=", 1)[1]).expanduser()
        if not configured_dir.is_absolute():
            configured_dir = Path(process.cwd()) / configured_dir
        if not same_user or not (module_index is not None or entrypoint) or configured_dir.resolve() != Path(data_dir).resolve():
            raise RuntimeError(f"Server lock is held by an unrecognized process (PID {process.pid}). No process was stopped.")
        return process
    except (psutil.Error, OSError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("The dashboard process cannot be verified. No process was stopped.") from exc


def spawned_server(launcher, state, data_dir, module="codex_sessions"):
    try:
        process = verified_process(state, data_dir, module)
        if launcher.is_running() and (process == launcher or launcher in process.parents()):
            return process
    except (RuntimeError, psutil.Error):
        pass
    return None


def detached_process_options():
    if sys.platform == "win32":
        options = quiet_process_options()
        options["creationflags"] |= subprocess.CREATE_NEW_PROCESS_GROUP
        return options
    return {"start_new_session": True}


def quiet_process_options(platform=None):
    if (platform or sys.platform) == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startup}
    return {}


def configure_stdio():
    for stream in (sys.stdin, sys.stdout):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        if path.stat().st_uid != os.getuid():
            raise PermissionError("The state directory must belong to the current user")
        path.chmod(0o700)
    return path
