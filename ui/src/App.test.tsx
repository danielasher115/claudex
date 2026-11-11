import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { Session, Snapshot } from "./api";

let eventSource: { onmessage?: (event: { data: string }) => void };
let session: Session;
let request: ReturnType<typeof vi.fn<(url: string, init?: RequestInit) => Promise<Response>>>;
const snapshot = (): Snapshot => ({ sessions: [session], connection: { connected: true, error: null, home: "/test/.codex", socket: "/test/socket" } });
const response = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  localStorage.clear(); history.replaceState(null, "", "/");
  session = { id: "a", title: "Investigate queue delivery", preview: "Queue delivery", cwd: "/repos/platform", model: "codex", source: "claude", origin: "mcp", status: "idle", flags: [], created_at: 1, updated_at: 1, archived: false, can_send: true, reason: null, revision: "one", messages: [] };
  vi.stubGlobal("EventSource", class { onmessage?: (event: { data: string }) => void; constructor() { eventSource = this; } close() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  HTMLElement.prototype.scrollIntoView = vi.fn();
  request = vi.fn(async (url: string) => {
    if (url.endsWith("/view")) return response({ session, history: { items: [{ id: "u", type: "userMessage", content: [{ type: "text", text: "Please check the queue" }] }, { id: "r", type: "agentMessage", text: "The queue is ready." }], next_cursor: null, source: "codex", warning: null } });
    return response(snapshot());
  });
  vi.stubGlobal("fetch", request);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function openConversation() {
  render(<App />);
  act(() => eventSource.onmessage?.({ data: JSON.stringify(snapshot()) }));
  fireEvent.click(within(screen.getByRole("navigation", { name: "Sessions" })).getByRole("button", { name: /Investigate queue delivery/ }));
  await screen.findByText("The queue is ready.");
}

test("an unchanged idle snapshot does not reload conversation history", async () => {
  await openConversation();
  expect(request.mock.calls.filter(([url]) => url.endsWith("/view"))).toHaveLength(1);
  act(() => eventSource.onmessage?.({ data: JSON.stringify(snapshot()) }));
  expect(screen.getByText("The queue is ready.")).toBeTruthy();
  expect(request.mock.calls.filter(([url]) => url.endsWith("/view"))).toHaveLength(1);
  session = { ...session, revision: "two" };
  act(() => eventSource.onmessage?.({ data: JSON.stringify(snapshot()) }));
  await waitFor(() => expect(request.mock.calls.filter(([url]) => url.endsWith("/view"))).toHaveLength(2));
});

test("live updates coalesce while a slow conversation request completes", async () => {
  session = { ...session, status: "active" };
  await openConversation();
  const original = request.getMockImplementation()!;
  let finish: (value: Response) => void = () => {};
  let signal: AbortSignal | undefined;
  let pending = true;
  request.mockImplementation((url, init) => {
    if (url.endsWith("/view") && pending) { pending = false; signal = init?.signal as AbortSignal; return new Promise<Response>(resolve => { finish = resolve; }); }
    if (url.endsWith("/view")) return Promise.resolve(response({ session, history: { items: [{ id: "slow", type: "agentMessage", text: "Slow response completed" }], next_cursor: null, source: "codex", warning: null } }));
    return original(url, init);
  });
  session = { ...session, revision: "two" };
  act(() => eventSource.onmessage?.({ data: JSON.stringify(snapshot()) }));
  await waitFor(() => expect(signal).toBeTruthy());
  for (const revision of ["three", "four"]) {
    session = { ...session, revision };
    act(() => eventSource.onmessage?.({ data: JSON.stringify(snapshot()) }));
  }
  expect(signal!.aborted).toBe(false);
  expect(request.mock.calls.filter(([url]) => url.endsWith("/view"))).toHaveLength(2);
  await act(async () => finish(response({ session, history: { items: [{ id: "slow", type: "agentMessage", text: "Slow response completed" }], next_cursor: null, source: "codex", warning: null } })));
  expect(await screen.findByText("Slow response completed")).toBeTruthy();
  await waitFor(() => expect(request.mock.calls.filter(([url]) => url.endsWith("/view"))).toHaveLength(3));
});

test("a refresh beyond the loaded window resets pagination so intervening history stays reachable", async () => {
  await openConversation();
  const original = request.getMockImplementation()!;
  request.mockImplementation((url, init) => {
    if (url.endsWith("/view")) return Promise.resolve(response({ session, history: { items: [{ id: "new-tail", type: "agentMessage", text: "Latest window after many updates" }], next_cursor: "items:missing-middle", source: "codex", warning: null } }));
    if (url.includes("/history?")) return Promise.resolve(response({ items: [{ id: "middle", type: "agentMessage", text: "Intervening history" }], next_cursor: null, source: "codex", warning: null }));
    return original(url, init);
  });
  session = { ...session, revision: "many-new-items" };
  act(() => eventSource.onmessage?.({ data: JSON.stringify(snapshot()) }));
  await screen.findByText("Latest window after many updates");
  expect(screen.queryByText("The queue is ready.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Load earlier conversation/ }));
  expect(await screen.findByText("Intervening history")).toBeTruthy();
});


test("completion refreshes the viewer even when the history revision is unchanged", async () => {
  session = { ...session, status: "active" };
  await openConversation();
  const calls = request.mock.calls.filter(([url]) => url.endsWith("/view")).length;
  session = { ...session, status: "idle", activity: { phase: "completed", label: "Completed", detail: "", turn_id: "t", turn_started_at: 1, phase_started_at: 2, last_event_at: 2, last_update: "", source: "live", tool_count: 0 } };
  act(() => eventSource.onmessage?.({ data: JSON.stringify(snapshot()) }));
  await waitFor(() => expect(request.mock.calls.filter(([url]) => url.endsWith("/view"))).toHaveLength(calls + 1));
  expect(screen.queryByRole("region", { name: "Agent activity" })).toBeNull();
  expect(screen.queryByText("Codex is working")).toBeNull();
  const sidebar = within(screen.getByRole("navigation", { name: "Sessions" }));
  expect(sidebar.queryByText("Completed", { exact: true })).toBeNull();
  expect(sidebar.getByRole("img", { name: "Completed" })).toBeTruthy();
});

test("sending preserves draft text edited during delivery", async () => {
  await openConversation();
  let accept: (value: Response) => void = () => {};
  const get = request.getMockImplementation()!;
  request.mockImplementation((url: string, init?: RequestInit) => init?.method === "POST" ? new Promise<Response>(resolve => { accept = resolve; }) : get(url));
  const textbox = screen.getByRole("textbox", { name: "Message Codex" });
  fireEvent.change(textbox, { target: { value: "First message" } });
  fireEvent.click(screen.getByRole("button", { name: "Queue message" }));
  fireEvent.change(textbox, { target: { value: "Draft typed while waiting" } });
  await act(async () => accept(response({ id: "delivery", text: "First message", mode: "queue", status: "queued", created_at: 1 })));
  expect((textbox as HTMLTextAreaElement).value).toBe("Draft typed while waiting");
  expect(localStorage.getItem("draft:a")).toBe("Draft typed while waiting");
});

test("a queued message can be steered into the active turn without submitting a duplicate", async () => {
  session = { ...session, status: "active", active_turn_id: "current-turn", can_steer_queued: true, messages: [{ id: "queued-message", text: "Use this update now", mode: "queue", status: "queued", error: null, turn_id: null, created_at: 1 }] };
  await openConversation();
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (url, init) => {
    if (init?.method === "POST") {
      session = { ...session, messages: [{ ...session.messages![0], mode: "steer", status: "delivered", turn_id: "current-turn" }] };
      return response(session.messages![0]);
    }
    return original(url, init);
  });
  fireEvent.click(screen.getByRole("button", { name: "Steer queued message now" }));
  await waitFor(() => expect(screen.queryByText("Use this update now")).toBeNull());
  const posts = request.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(posts[0][0]).toBe("/api/sessions/a/messages/queued-message/steer");
  expect(JSON.parse(posts[0][1]!.body as string)).toEqual({ expected_turn_id: "current-turn" });
});

test("conversation search retains surrounding messages", async () => {
  await openConversation();
  fireEvent.click(screen.getByRole("button", { name: "Search conversation" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Find in conversation" }), { target: { value: "ready" } });
  await waitFor(() => expect(document.querySelectorAll("[data-search-match]")).toHaveLength(1));
  expect(screen.getByText("Please check the queue")).toBeTruthy();
  expect(screen.getByText("The queue is ready.")).toBeTruthy();
  expect(screen.getByText("1 / 1")).toBeTruthy();
});

test("pinning a session persists the preference", async () => {
  await openConversation();
  fireEvent.click(screen.getAllByRole("button", { name: "Pin session" })[0]);
  expect(JSON.parse(localStorage.getItem("session-pins")!)).toEqual(["a"]);
  expect(screen.getByText("Pinned")).toBeTruthy();
});

test("acknowledgment after navigation does not restore a submitted draft", async () => {
  await openConversation();
  let accept: (value: Response) => void = () => {};
  const get = request.getMockImplementation()!;
  request.mockImplementation((url: string, init?: RequestInit) => init?.method === "POST" ? new Promise<Response>(resolve => { accept = resolve; }) : get(url));
  fireEvent.change(screen.getByRole("textbox", { name: "Message Codex" }), { target: { value: "Send once" } });
  fireEvent.click(screen.getByRole("button", { name: "Queue message" }));
  fireEvent.click(screen.getByRole("button", { name: /^Overview/ }));
  await act(async () => accept(response({ id: "delivery", text: "Send once", mode: "queue", status: "queued", created_at: 1 })));
  fireEvent.click(within(screen.getByRole("navigation", { name: "Sessions" })).getByRole("button", { name: /Investigate queue delivery/ }));
  expect((screen.getByRole("textbox", { name: "Message Codex" }) as HTMLTextAreaElement).value).toBe("");
});

test("switching recorded and live history does not duplicate messages", async () => {
  await openConversation();
  const get = request.getMockImplementation()!;
  request.mockImplementation((url: string) => url.endsWith("/view") ? Promise.resolve(response({ session, history: { items: [{ id: "record-1", type: "record", record: { type: "message", role: "assistant", content: [{ text: "The queue is ready." }] } }], next_cursor: "record:40", source: "recorded", warning: "Recorded history" } })) : get(url));
  const offline = snapshot(); offline.connection.connected = false;
  act(() => eventSource.onmessage?.({ data: JSON.stringify(offline) }));
  await screen.findByText("Recorded history");
  expect(screen.getAllByText("The queue is ready.")).toHaveLength(1);
  expect(screen.getByRole("button", { name: /Load earlier conversation/ })).toBeTruthy();
});
