import { act, cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { BackgroundShells } from "./BackgroundShells";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const response = (data: unknown) => new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });
const respond = (data: unknown) => vi.stubGlobal("fetch", vi.fn(async () => response(data)));
const running = { status: "available", items: [{ processId: "p1", command: "sleep 600", cwd: "/a" }] };

test("shows native background shells in the composer tray with scoped process details", async () => {
  respond({ status: "available", partial: false, scope: "Background terminals reported by the connected Codex runtime for this session.", items: [
    { processId: "p1", command: "npm run dev", cwd: "/repo", osPid: 123, cpuPercent: 0, rssKb: 2048 },
  ] });
  render(<BackgroundShells sessionId="a" />);
  const summary = await screen.findByText("1 background shell");
  fireEvent.click(summary);
  expect(summary.closest("details")?.classList.contains("background-shells")).toBe(true);
  expect(screen.getByText("npm run dev")).toBeTruthy();
  expect(screen.getByText("/repo")).toBeTruthy();
  expect(screen.getByText(/PID 123/)).toBeTruthy();
  expect(screen.getByText(/CPU 0%/)).toBeTruthy();
  expect(screen.getByText(/2 MB/)).toBeTruthy();
  expect(screen.getByText(/connected Codex runtime/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /stop/i })).toBeNull();
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/sessions/a/background-shells");
});

test.each([
  { status: "available", items: [], partial: false },
  { status: "available", items: [], partial: true },
  { status: "unavailable", items: [], message: "The runtime could not list terminals." },
  { status: "unsupported", items: [] },
  { status: "unavailable", items: running.items },
])("does not create a tray for an empty or unavailable inventory: %j", async data => {
  respond(data);
  const view = await act(async () => render(<BackgroundShells sessionId="a" />));
  expect(view.container.textContent).toBe("");
});

test("does not show a warning when an inventory request fails", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("Offline"); }));
  const view = await act(async () => render(<BackgroundShells sessionId="a" />));
  expect(view.container.textContent).toBe("");
});

test("partial inventories show only actual shells and indicate an incomplete count", async () => {
  respond({ ...running, partial: true, message: "The runtime returned an incomplete background terminal list." });
  render(<BackgroundShells sessionId="a" />);
  fireEvent.click(await screen.findByText("1+ background shells"));
  expect(screen.getByText("sleep 600")).toBeTruthy();
  expect(screen.getByText("The runtime returned an incomplete background terminal list.")).toBeTruthy();
});

test.each(["complete", "partial empty", "unavailable", "failed"])("removes stale shells after the next inventory is %s", async outcome => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async () => response(running));
  vi.stubGlobal("fetch", fetcher);
  const view = await act(async () => render(<BackgroundShells sessionId="a" />));
  expect(screen.getByText("1 background shell")).toBeTruthy();
  if (outcome === "failed") fetcher.mockRejectedValueOnce(new Error("Offline"));
  else fetcher.mockResolvedValueOnce(response({ status: outcome === "unavailable" ? "unavailable" : "available", items: [], partial: outcome === "partial empty" }));
  await act(async () => vi.advanceTimersByTimeAsync(4999));
  expect(screen.getByText("1 background shell")).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(view.container.textContent).toBe("");
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  expect(screen.getByText("1 background shell")).toBeTruthy();
});

test("changing sessions never shows the previous session's running shells or a late response", async () => {
  respond(running);
  const { rerender } = render(<BackgroundShells sessionId="a" />);
  await screen.findByText("1 background shell");
  let finish: (value: Response) => void = () => {};
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
  rerender(<BackgroundShells sessionId="b" />);
  expect(screen.queryByText("1 background shell")).toBeNull();
  const finishOld = finish;
  rerender(<BackgroundShells sessionId="c" />);
  await act(async () => finishOld(response(running)));
  expect(screen.queryByText("sleep 600")).toBeNull();
});

test("inactive session tabs do not poll and resume with a fresh inventory", async () => {
  vi.useFakeTimers();
  respond(running);
  const view = render(<BackgroundShells sessionId="a" enabled={false} />);
  expect(fetch).not.toHaveBeenCalled();
  await act(async () => view.rerender(<BackgroundShells sessionId="a" enabled />));
  expect(screen.getByText("1 background shell")).toBeTruthy();
  view.rerender(<BackgroundShells sessionId="a" enabled={false} />);
  expect(view.container.textContent).toBe("");
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  expect(fetch).toHaveBeenCalledTimes(1);
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  await act(async () => view.rerender(<BackgroundShells sessionId="a" enabled />));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(view.container.textContent).toBe("");
});
