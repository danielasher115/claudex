# Setup and troubleshooting

Claudex needs a local, authenticated Codex app-server. It uses your existing Codex account and configuration.
The gateway and MCP adapter run under your user account. Do not run them as administrator or root for normal use.

## Platform paths

| Platform | Codex transport | Status |
| --- | --- | --- |
| Linux | Local Unix socket or loopback WebSocket | Exercised locally |
| WSL | Local Unix socket or loopback WebSocket inside WSL | Setup documented |
| Native Windows | Loopback WebSocket | Setup documented |
| macOS | Local Unix socket or loopback WebSocket | Untested |

Use a Codex CLI version that supports `codex app-server --listen`. Check `codex app-server --help` on your installation.
Claudex connects to an app-server; it does not start, stop, or authenticate Codex for you.

## Native Windows

Install Claudex in your Windows Python environment. Start Codex in a PowerShell terminal:

```powershell
codex app-server --listen ws://127.0.0.1:4500
```

Keep that terminal open. In a second PowerShell terminal:

```powershell
claudex start --codex-url ws://127.0.0.1:4500
claudex status
```

The WebSocket address must be a loopback address. Keep Codex and Claudex on the same machine.
Use `claudex serve --codex-url ws://127.0.0.1:4500` to run the gateway in the foreground for troubleshooting.
If your app-server requires bearer authentication, pass `--codex-token-file /absolute/path/to/token-file`; keep that file private.

## Linux and WSL

Claudex uses `~/.codex/app-server-control/app-server-control.sock` by default, under the selected Codex home.
When that socket already exists, start the gateway:

```bash
claudex start
```

You can also start an independent app-server in another terminal:

```bash
codex app-server --listen unix:///tmp/claudex-codex.sock
```

Then connect explicitly:

```bash
claudex start --socket /tmp/claudex-codex.sock
```

Use `--home /absolute/path/to/codex-home` to read another Codex home. A selected socket must belong to the app-server you intend to use.
For WSL, install both the Python package and Codex inside the same distribution. Use Linux project paths for agents launched there.

## Connect Claude through MCP

The gateway must be running before Claude connects. Add this server to your Claude Code configuration:

```json
{
  "mcpServers": {
    "codex": {
      "command": "claudex",
      "args": ["mcp"]
    }
  }
}
```

The stdio MCP process calls the same gateway as the dashboard. It does not create an independent Codex connection.
Use the same user state directory for `start`, `mcp`, and CLI calls. With a custom directory, include it explicitly:

```json
{
  "mcpServers": {
    "codex": {
      "command": "claudex",
      "args": ["mcp", "--data-dir", "/absolute/path/to/claudex-state"]
    }
  }
}
```

### Windows Claude with a WSL gateway

If Claude runs on Windows and Claudex runs inside WSL, launch the MCP adapter through `wsl.exe`:

```json
{
  "mcpServers": {
    "codex": {
      "command": "wsl.exe",
      "args": ["--distribution", "Ubuntu", "--exec", "/home/YOUR_USER/.local/bin/claudex", "mcp"]
    }
  }
}
```

Replace `Ubuntu` with your distribution and the executable path with `command -v claudex` from that distribution.
For a custom data directory, append `--data-dir` and its Linux path to `args`. This avoids relying on the Windows or WSL shell PATH.

### Caller conversation hook

Run `claudex install-claude-context` in the environment where Claude Code runs. The installer adds a narrow PreToolUse hook
for the `codex` MCP server and preserves the original user settings in a backup. Restart or resume Claude Code and reconnect MCP.
The installer uses Claude Code's current exec-form hook support, so paths and arguments are passed directly without a shell.
The hook exports the exact calling conversation's public messages, including a subagent caller. It excludes private reasoning
and reports unavailable binary content. See [context inheritance](agent-protocol.md#caller-context) for the trust boundary.

## Data and instances

State belongs to the user, separate from the installed Python package:

| Platform | Default state directory |
| --- | --- |
| Linux / WSL | `$XDG_STATE_HOME/claudex`, or `~/.local/state/claudex` |
| Windows | `%LOCALAPPDATA%\Claudex` |
| macOS | `~/Library/Application Support/Claudex` |

`CLAUDEX_DATA_DIR` overrides the default; `--data-dir` selects a directory for one command. Keep this value consistent across
the dashboard, MCP adapter, hook, and CLI. State can contain prompts, exported caller context, attachments, and runtime authentication data.
Do not commit it or copy it into public bug reports.

Use `claudex status` to inspect the running instance. `claudex stop` stops the gateway. `claudex restart` restarts it.
Codex's own home and saved conversations remain managed by Codex.

## Ports and a stable address

The default is **http://codex.localhost:8765**. Claudex picks a free backend port and keeps the browser address stable with a local listener.
The stable listener and backend port are independent.

```bash
# Pick a free backend port and use a different stable listener.
claudex start --address-port 8877

# Use a fixed backend port directly.
claudex start --port 8878

# Use a fixed backend and a separate stable listener.
claudex start --port 8878 --address-port 8877
```

If you prefer a port-free address, configure your own local reverse proxy from `http://codex.localhost` on port 80 to the
stable loopback listener. Claudex does not automatically modify system proxy, hosts, or privileged-port settings.
Keep the proxy bound to loopback. See [security](../SECURITY.md) before exposing either listener beyond your machine.

## Common problems

**The dashboard shows saved conversations but Codex is disconnected.** Check that the app-server is running and that the selected
socket or `--codex-url` matches it. `--home` should refer to the intended Codex configuration and history.

**The stable address is already in use.** Stop the existing instance using its data directory, or choose another `--address-port`.
Run `claudex status` to find the current backend address.

**MCP cannot find the gateway.** Confirm `claudex status` works under the same account and data directory as the MCP process.
Use an absolute executable path when Claude does not inherit your terminal PATH. For WSL, use the configuration above.

**A saved session cannot receive a message.** Read its connection reason in the dashboard. A conversation owned by another
process can remain readable without being controllable through this gateway.

**Attachments fail before launch.** Read the preparation error. PDF support is included through the portable PDFium dependency; unsupported or
oversized inputs fail explicitly. Details and limits are in the [protocol guide](agent-protocol.md#files-and-attachments).
