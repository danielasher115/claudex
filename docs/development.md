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

The two PNGs in `docs/images/` show the light overview and dark split diff. All displayed paths and messages come from `scripts/demo.py`; never substitute real user history.
Review both generated images before publishing them. Keep full video recordings outside Git; use release assets for downloads.


## Headless subprocess tests

Native Windows tests suppress console creation for every test subprocess and reject requests for a new console window.
The Windows-only regression checks that a spawned Python child has no console-window handle. Noninteractive application
commands also use the shared platform policy. Test runners must not launch user-visible terminals.

## Direct screen recording

`scripts/record_screen.py` captures actual pixels from an explicitly selected local X11 display. Run a Linux terminal emulator
and a browser on a dedicated hidden Xvfb display, then switch between those real windows while recording:

```bash
python scripts/record_screen.py --display :97 --output /absolute/path/to/walkthrough.webm \
  --fps 15 --duration 180 --cursor --stop-file /tmp/demo/stop \
  --ready-file /tmp/demo/ready.json --qa-dir /tmp/demo/private-qa \
  --ffmpeg /absolute/path/to/ffmpeg
```

The recorder uses Pillow's XCB screen capture and ffmpeg with MJPEG decoding and VP8/WebM encoding. The optional `--cursor` flag
requires `python-xlib` (`python -m pip install python-xlib`). It adds a white pointer at the observed X11 position and a subtle
pulse for observed button presses. Only the pointer highlight is added; the application and terminal pixels come from the display.
The private QA PNGs remain unmodified, and `pointer.jsonl` records sampled pointer coordinates and button masks when highlighting is enabled.

Move the actual pointer smoothly through the X11 input system for smooth movement in the video. The recorder does not invent motion
or clicks. Create the stop file to finish early; the duration sets an upper bound. The ready file appears after the first captured frame.
Review the private screen grabs for account banners, personal paths, and unrelated windows before publishing a recording.
