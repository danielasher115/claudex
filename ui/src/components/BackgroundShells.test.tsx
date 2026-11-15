import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { BackgroundShells } from "./BackgroundShells";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const respond = (data: unknown) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } })));

test("shows native background shells separately from foreground work with scoped process details", async () => {
  respond({ status: "available", partial: false, scope: "Background terminals reported by the connected Codex runtime for this session.", items: [
    { processId: "p1", command: "npm run dev", cwd: "/repo", osPid: 123, cpuPercent: 0, rssKb: 2048 },
  ] });
  render(<BackgroundShells sessionId="a" />);
  fireEvent.click(await screen.findByText("1 background shell"));
  expect(screen.getByText("npm run dev")).toBeTruthy();
  expect(screen.getByText("/repo")).toBeTruthy();
  expect(screen.getByText(/PID 123/)).toBeTruthy();
  expect(screen.getByText(/CPU 0%/)).toBeTruthy();
  expect(screen.getByText(/2 MB/)).toBeTruthy();
  expect(screen.getByText(/connected Codex runtime/)).toBeTruthy();
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/sessions/a/background-shells");
});

test("available empty inventories stay hidden and unavailable inventories never report zero", async () => {
  respond({ status: "available", items: [], partial: false });
  const { container, unmount } = render(<BackgroundShells sessionId="a" />);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(container.textContent).toBe("");
  unmount();
  respond({ status: "unavailable", items: [], message: "The runtime could not list terminals.", scope: "This session only." });
  render(<BackgroundShells sessionId="b" />);
  fireEvent.click(await screen.findByText("Background shells unavailable"));
  expect(screen.getByText("The runtime could not list terminals.")).toBeTruthy();
  expect(screen.queryByText("0 background shells")).toBeNull();
});

test("changing sessions never shows the previous session's running shells", async () => {
  respond({ status: "available", items: [{ processId: "p1", command: "sleep 600", cwd: "/a" }] });
  const { rerender } = render(<BackgroundShells sessionId="a" />);
  await screen.findByText("1 background shell");
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  rerender(<BackgroundShells sessionId="b" />);
  expect(screen.queryByText("1 background shell")).toBeNull();
  expect(screen.queryByText("sleep 600")).toBeNull();
});
