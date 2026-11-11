# Development

Use Python 3.11+ and a Node.js version supported by the Vite version in `ui/package.json`.
The Python and frontend dependencies are installed independently.

```bash
python -m venv .venv
# Activate .venv for your shell.
python -m pip install -e '.[dev]'
```

In `ui/`:

```bash
npm ci
npm test
npm run build
```

The build writes the dashboard into `codex_sessions/static/`, which is included in the Python package.
Run Python checks from the repository root:

```bash
python -m pytest -q
```

The native lifecycle test is opt-in and requires a compatible Codex binary. It starts an isolated app-server, a local model fixture,
and a sentinel MCP child. It does not use your normal Codex home or account credentials:

```bash
CODEX_NATIVE_TEST_BIN=/absolute/path/to/codex python -m pytest -q tests/test_idle_native.py
```

For a foreground development gateway, select a separate data directory and an explicit unused port:

```bash
claudex serve --port 8878 --data-dir /absolute/path/to/dev-state
```

Add `--socket` or `--codex-url` for your chosen app-server. This is a real connection: actions can launch agents and incur normal
Codex usage. Keep real gateway state out of the repository.

## Demo screenshots

The documentation screenshots use the built frontend with fictional projects and conversations. The renderer serves static files
on an OS-selected loopback port, intercepts API requests with fixtures, and blocks requests to other origins. It never imports the
backend, opens a Codex connection, or starts a model turn. Page errors and missing API fixtures fail the run.

Install the optional screenshot tool in your development environment:

```bash
python -m pip install playwright
python -m playwright install chromium
```

Build the UI, then run from the repository root:

```bash
python scripts/demo.py
```

For an existing compatible Chromium installation:

```bash
python scripts/demo.py --browser /absolute/path/to/chromium
```

The six PNGs in `docs/images/` show the overview in light and dark themes, a conversation, split diffs, agent settings,
and inherited context. All displayed paths and messages come from `scripts/demo.py`; never substitute real user history.
Review every generated image before publishing it.


## Headless subprocess tests

Native Windows tests suppress console creation for every test subprocess and reject requests for a new console window.
The Windows-only regression checks that a spawned Python child has no console-window handle. Noninteractive application
commands also use the shared platform policy. Test runners must not launch user-visible terminals.
