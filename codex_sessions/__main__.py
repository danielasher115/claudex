import argparse
import json
import sys
from contextlib import ExitStack
from pathlib import Path

import uvicorn

from . import __version__
from .platform import default_data_dir, private_directory, configure_stdio


def main():
    configure_stdio()
    parser = argparse.ArgumentParser(description="Local Claudex dashboard")
    parser.add_argument("--version", action="version", version=f"Claudex {__version__}")
    parser.add_argument("command", nargs="?", choices=["start", "stop", "restart", "status", "serve", "route", "mcp", "call", "models", "completion", "claude-hook", "install-claude-context"], default="serve")
    parser.add_argument("method", nargs="?", help="Protocol operation (for call) or shell name (for completion)")
    parser.add_argument("--json", dest="json_file", default="-", help="JSON parameter file, or - for stdin")
    parser.add_argument("--home", type=Path, help="Codex home used by Claude, independent of CODEX_HOME (default: ~/.codex)")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--socket", type=Path, help="Codex Unix control socket")
    transport.add_argument("--codex-url", help="Explicit loopback Codex WebSocket URL, such as ws://127.0.0.1:4500")
    parser.add_argument("--codex-token-file", type=Path, help="File containing the Codex WebSocket bearer token")
    parser.add_argument("--port", type=int, help="Backend port; 0 selects an available port (default: automatic with a stable local address)")
    parser.add_argument("--address-port", type=int, help="Stable codex.localhost listener port (default: 8765); also enables it with an explicit backend port")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="Local state directory (or CLAUDEX_DATA_DIR)")
    args = parser.parse_args()
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.address_port is not None and not 1 <= args.address_port <= 65535:
        parser.error("--address-port must be between 1 and 65535")
    if args.codex_url:
        from .rpc import validate_url
        try:
            validate_url(args.codex_url)
        except ValueError as exc:
            parser.error(str(exc))
    if args.codex_token_file and not args.codex_url and args.command not in ("restart", "stop", "status"):
        parser.error("--codex-token-file requires --codex-url")
    if args.command == "claude-hook":
        from .claude_hook import run
        run(args)
        return
    if args.command == "install-claude-context":
        from .claude_setup import install
        try:
            result = install(args.data_dir)
            print(f"{'Installed' if result['changed'] else 'Already installed'} Claude caller-context hook: {result['settings']}")
            print("Restart Claude and resume the same conversation to reliably load the hook, then reconnect Codex MCP.")
        except (OSError, ValueError, TypeError) as exc:
            parser.exit(1, str(exc) + "\n")
        return
    if args.command == "completion":
        if args.method not in (None, "bash"):
            parser.error("The supported completion shell is bash")
        print((Path(__file__).parent / "completions" / "codex-sessions.bash").read_text(encoding="utf-8"), end="")
        return
    if args.command in ("start", "stop", "restart", "status"):
        if args.method is not None:
            parser.error(f"{args.command} does not accept a protocol method; use --data-dir to select an instance")
        from .lifecycle import run
        try:
            run(args)
        except (ValueError, OSError, RuntimeError) as exc:
            parser.exit(1, str(exc) + "\n")
        return
    if args.command == "route":
        from .address import start_route
        try:
            start_route(args)
        except (ValueError, OSError, RuntimeError) as exc:
            parser.exit(1, str(exc) + "\n")
        return
    args.home = args.home or Path.home() / ".codex"
    if args.command in ("call", "models"):
        from .client import call
        try:
            params = {} if args.command == "models" else json.loads(sys.stdin.read() if args.json_file == "-" else Path(args.json_file).read_text(encoding="utf-8"))
            print(json.dumps(call("models.list" if args.command == "models" else args.method, params, args.port, args.data_dir), indent=2))
        except (ValueError, OSError) as exc:
            parser.exit(1, str(exc) + "\n")
    elif args.command == "mcp":
        from .mcp import run
        run(args.port, args.data_dir)
    else:
        import os
        from .address import listener, public_url
        from .lifecycle import identity, save_runtime
        from .server import create_app
        from .platform import file_lock
        requested_port = args.port if args.port is not None else 0
        address_port = args.address_port if args.address_port is not None else (8765 if requested_port == 0 else None)
        if sys.platform == "win32" and not args.codex_url:
            parser.error("Native Windows requires --codex-url ws://127.0.0.1:PORT for a local Codex app-server")
        args.home = args.home.expanduser().resolve()
        socket_path = args.codex_url or args.socket or args.home / "app-server-control" / "app-server-control.sock"
        args.data_dir = args.data_dir.expanduser().resolve()
        private_directory(args.data_dir)
        with ExitStack() as resources:
            try:
                resources.enter_context(file_lock(args.data_dir / "server.lock"))
                backend = resources.enter_context(listener(requested_port))
                sockets = [backend]
                port = backend.getsockname()[1]
                if address_port is not None:
                    sockets.append(resources.enter_context(listener(address_port, named=True)))
            except OSError as exc:
                parser.exit(1, f"Claudex is already running or a port is unavailable: {exc}\n")
            url = public_url(address_port, args.data_dir) if address_port else f"http://localhost:{port}"
            state = {"pid": os.getpid(), "identity": identity(os.getpid()), "port": port, "home": str(args.home),
                     "socket": str(socket_path) if not args.codex_url else None, "codex_url": args.codex_url,
                     "codex_token_file": str(args.codex_token_file.expanduser().resolve()) if args.codex_token_file else None,
                     "background": os.environ.get("CLAUDEX_BACKGROUND") == "1", "requested_port": requested_port,
                     "address_port": address_port, "public_url": url}
            save_runtime(args.data_dir, state, "server.owner.json")
            save_runtime(args.data_dir, state)
            config = uvicorn.Config(create_app(args.home, socket_path, args.data_dir, port, codex_token_file=args.codex_token_file),
                                    host="127.0.0.1", port=port, proxy_headers=False, timeout_graceful_shutdown=3)
            uvicorn.Server(config).run(sockets=sockets)


if __name__ == "__main__":
    main()
