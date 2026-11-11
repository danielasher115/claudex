"""Render the real UI with fictional fixtures, without a Codex connection."""

import argparse
import difflib
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
NOW = 1794387600
WORKSPACE = "/workspace/weather-kit"
BRIEF = "Add a friendly empty state to the forecast card. Keep the change small and cover it with a test."
BEFORE = '''export function ForecastCard({ forecast }: Props) {
  return (
    <section className="forecast-card">
      <h2>Today's forecast</h2>
      <ForecastDetails forecast={forecast} />
    </section>
  );
}
'''
AFTER = '''export function ForecastCard({ forecast }: Props) {
  return (
    <section className="forecast-card">
      <h2>Today's forecast</h2>
      {forecast ? (
        <ForecastDetails forecast={forecast} />
      ) : (
        <p className="empty-state">Choose a city to see the forecast.</p>
      )}
    </section>
  );
}
'''
PATCH = "".join(difflib.unified_diff(BEFORE.splitlines(True), AFTER.splitlines(True),
                                     fromfile="a/src/ForecastCard.tsx", tofile="b/src/ForecastCard.tsx"))
PATCH += "".join(difflib.unified_diff([], ['test("shows an empty state before a city is selected", () => {\n',
    '  render(<ForecastCard forecast={null} />);\n', '  expect(screen.getByText("Choose a city to see the forecast.")).toBeVisible();\n', '});\n'],
    fromfile="/dev/null", tofile="b/src/ForecastCard.test.tsx"))


def session(identifier, title, status="idle", project="weather-kit", age=120, tokens=8400):
    return dict(id=identifier, title=title, preview=title, cwd=f"/workspace/{project}", model="gpt-6.1-sol",
                source="claude", origin="agents", status=status, flags=[], created_at=NOW - 3600,
                updated_at=NOW - age, archived=False, can_send=True, reason=None, tokens=tokens, messages=[],
                activity=dict(phase="tool", label="Running tests", detail="npm test -- ForecastCard", turn_id="demo-turn",
                              turn_started_at=NOW - 120, phase_started_at=NOW - 25, last_event_at=NOW - 3,
                              last_update="Checking the empty-state behavior", source="runtime", tool_count=4))


SESSIONS = [session("forecast", "Build a thoughtful forecast card", "active", age=3, tokens=18420),
            session("accessibility", "Review keyboard navigation", "active", "field-notes", age=12, tokens=12560),
            session("tests", "Cover the city picker edge cases", age=180, tokens=9720),
            session("docs", "Polish the getting started guide", "saved", "field-notes", age=900, tokens=6340),
            session("tokens", "Simplify the color tokens", "saved", "weather-kit", age=2100, tokens=11200)]
SNAPSHOT = dict(sessions=SESSIONS, connection=dict(connected=True, error=None, home="Demo workspace", socket="Demo connection"))
ITEMS = [dict(id="prompt", type="userMessage", text=BRIEF, turn_id="demo-turn"),
         dict(id="intro", type="agentMessage", text="I'll keep the existing card layout and add a clear next step when no city is selected.", turn_id="demo-turn"),
         dict(id="edit", type="fileChange", status="completed", changes=[dict(path="src/ForecastCard.tsx", diff=PATCH)], turn_id="demo-turn"),
         dict(id="test", type="commandExecution", command="npm test -- ForecastCard", status="completed", exitCode=0,
              aggregatedOutput="PASS  src/ForecastCard.test.tsx\n  ✓ renders a selected city's forecast\n  ✓ shows an empty state before a city is selected\n\n2 tests passed", durationMs=1240, turn_id="demo-turn"),
         dict(id="answer", type="agentMessage", text="The empty state is ready.\n\n- A short prompt helps people choose their first city.\n- The existing forecast view stays the same.\n- Both card states are covered by the component tests.\n\nI'm checking keyboard navigation next.", turn_id="demo-turn"),
         dict(id="demo-turn", type="turn", status="inProgress", diff=PATCH)]
DETAILS = dict(session_id="forecast", recorded=dict(status="available", source="Recorded demo session",
    settings=dict(model="gpt-6.1-sol", effort="high", sandbox="workspace-write", approval_policy="on-request"),
    metadata=dict(originator="Claude Code", source="MCP", git=dict(branch="feature/forecast-empty-state")),
    context=dict(tokens_used=18420, token_usage=dict(last=dict(input_tokens=18420), model_context_window=200000)),
    task=BRIEF, instructions=[dict(source="/workspace/weather-kit/AGENTS.md", text="Keep components small. Use the existing test runner. Preserve keyboard accessibility.")],
    memories=[dict(source="/workspace/weather-kit/notes.md", text="The forecast card should stay useful before a city is selected.")],
    skills=[dict(name="component-review", description="Review interface behavior and accessibility", path="/skills/component-review/SKILL.md")],
    sources={}, delegation=dict(depth=0, max_children=2, max_depth=1, children=[])),
    current=dict(scope="current_workspace_runtime", cwd=WORKSPACE,
        config=dict(status="available", source="Current workspace configuration", settings=dict(model_reasoning_effort="high"), layers=[]),
        skills=dict(status="available", source="skills/list", items=[dict(name="component-review", path="/skills/component-review/SKILL.md", description="Review interface behavior and accessibility", enabled=True, scope="user")]),
        mcp=dict(status="available", source="mcpServerStatus/list", items=[]),
        plugins=dict(status="available", source="plugin/list", items=[]),
        apps=dict(status="available", source="app/list", items=[])), warnings=[])


class StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Optional Chromium executable instead of the Playwright download")
    args = parser.parse_args()
    output = ROOT / "docs" / "images"
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticHandler, directory=str(ROOT / "codex_sessions" / "static")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    errors, unexpected = [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser)
            context = browser.new_context(viewport=dict(width=1440, height=1000), device_scale_factor=1, locale="en-US", timezone_id="UTC", color_scheme="light")
            context.add_init_script(f"""
                Date.now = () => {NOW * 1000};
                localStorage.setItem('session-source', 'all');
                window.EventSource = class {{
                    constructor() {{ setTimeout(() => this.onmessage?.({{data: {json.dumps(json.dumps(SNAPSHOT))}}}), 20); }}
                    close() {{}}
                }};
            """)

            def route(request):
                url = urlsplit(request.request.url)
                if not request.request.url.startswith(base + "/"):
                    unexpected.append(request.request.url)
                    return request.abort()
                path = url.path
                if not path.startswith("/api/"):
                    return request.continue_()
                if path == "/api/sessions":
                    payload = SNAPSHOT
                elif path.endswith("/view"):
                    payload = dict(session=SESSIONS[0], history=dict(items=ITEMS, next_cursor=None, source="native", warning=None), children=[])
                elif path.endswith("/details"):
                    payload = DETAILS
                else:
                    unexpected.append(path)
                    return request.fulfill(status=404, json=dict(error="No demo fixture"))
                request.fulfill(json=payload)

            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_role("heading", name="Your local workspace").wait_for()
            page.get_by_role("button", name="Claudex", exact=True).wait_for()
            page.get_by_role("button", name="Build a thoughtful forecast card").first.wait_for()
            page.screenshot(path=str(output / "overview-light.png"), animations="disabled")
            page.get_by_role("button", name="Switch theme").click()
            page.get_by_role("button", name="Build a thoughtful forecast card").first.click()
            page.get_by_label("Message Codex").wait_for()
            page.locator(".message-assistant ul").wait_for()
            page.get_by_role("button", name="Review changes", exact=True).click()
            page.get_by_role("table", name="Diff for src/ForecastCard.tsx").wait_for()
            page.get_by_role("button", name="Split", exact=True).click()
            page.get_by_label("Wrap lines").check()
            page.screenshot(path=str(output / "changes-dark.png"), animations="disabled")
            browser.close()
        assert not errors, errors
        assert not unexpected, unexpected
        print(f"Rendered 2 fictional demo screenshots in {output}")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
