import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { PlanUsage } from "./PlanUsage";

const usage = (count: number | null = 1) => ({ status: "available", plan_type: "pro", checked_at: 1794816000,
  limits: [{ id: "codex", name: "Codex", primary: { used_percent: 70, window_minutes: 10080, resets_at: 1795097147 }, secondary: null, credits: null }],
  reset_credits: count === null ? null : { available_count: count, credits: null } });
let request: ReturnType<typeof vi.fn>;
beforeEach(() => {
  sessionStorage.clear();
  request = vi.fn(async () => new Response(JSON.stringify(usage()), { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", request);
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("shows the actual remaining allowance and earned resets without spending one", async () => {
  render(<PlanUsage />);
  fireEvent.click(await screen.findByRole("button", { name: /Codex plan usage: 30% remaining/ }));
  expect(await screen.findByText("Weekly allowance")).toBeTruthy();
  expect(screen.getByRole("progressbar", { name: "Codex Weekly allowance" }).getAttribute("aria-valuenow")).toBe("70");
  expect(screen.getByText("1 reset available")).toBeTruthy();
  expect(request.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
});

test.each([0, null])("does not offer reset consumption when availability is %s", async count => {
  request.mockResolvedValue(new Response(JSON.stringify(usage(count))));
  render(<PlanUsage />);
  fireEvent.click(await screen.findByRole("button", { name: /Codex plan usage: 30% remaining/ }));
  expect(screen.queryByRole("button", { name: "Use a reset" })).toBeNull();
  expect(screen.getByText(count === 0 ? "No resets available" : "Reset availability not reported")).toBeTruthy();
});

test("requires confirmation and reuses the same reset attempt after an uncertain network failure", async () => {
  request.mockImplementation(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") throw new Error("Network unavailable. Retry the same attempt.");
    return new Response(JSON.stringify(usage()));
  });
  const view = render(<PlanUsage />);
  fireEvent.click(await screen.findByRole("button", { name: /Codex plan usage:/ }));
  fireEvent.click(screen.getByRole("button", { name: "Use a reset" }));
  expect(request.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Use one reset" }));
  await screen.findByRole("alert");
  const first = JSON.parse(request.mock.calls.find(([, init]) => init?.method === "POST")![1].body);
  view.unmount(); render(<PlanUsage />);
  fireEvent.click(await screen.findByRole("button", { name: /Codex plan usage:/ }));
  fireEvent.click(screen.getByRole("button", { name: "Use a reset" }));
  request.mockImplementation(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === "POST" ? { outcome: "reset", usage: usage(0) } : usage())));
  fireEvent.click(screen.getByRole("button", { name: "Use one reset" }));
  await screen.findByText("Your eligible Codex usage limits were reset.");
  const posts = request.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(2);
  expect(JSON.parse(posts[1][1].body).idempotency_key).toBe(first.idempotency_key);
  expect(screen.queryByRole("button", { name: "Use a reset" })).toBeNull();
});

test("an unavailable account stays unknown rather than showing a full allowance", async () => {
  request.mockResolvedValue(new Response(JSON.stringify({ status: "unavailable", limits: [], reset_credits: null, message: "Plan usage is unavailable." })));
  render(<PlanUsage />);
  fireEvent.click(await screen.findByRole("button", { name: /Codex plan usage: unavailable/ }));
  expect(screen.getByText("Plan usage is unavailable.")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
});
