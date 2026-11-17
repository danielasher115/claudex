import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";
import type { Session, Snapshot } from "./api";

let events: { onmessage?: (event: { data: string }) => void };
let sessions: Session[];
const publish = () => act(() => events.onmessage?.({ data: JSON.stringify({ sessions, connection: { connected: true, error: null, home: "/test/.codex", socket: "/test/socket" } } satisfies Snapshot) }));
const titles = () => within(screen.getByRole("navigation", { name: "Sessions" })).getAllByRole("button").filter(button => button.title.startsWith("Run ")).map(button => button.title);

beforeEach(() => {
  localStorage.clear(); history.replaceState(null, "", "/");
  const base: Session = { id: "old", title: "Run active", preview: "queue", cwd: "/repos/platform", model: "codex", source: "claude", origin: "mcp", status: "active", flags: [], created_at: 1, updated_at: 10, archived: false, can_send: true, reason: null, revision: "one", messages: [] };
  sessions = [base, { ...base, id: "new", title: "Run newest", status: "idle", created_at: 30, updated_at: 130 }, { ...base, id: "middle", title: "Run middle", cwd: "/repos/desktop", status: "idle", created_at: 20, updated_at: 200 }, { ...base, id: "external", title: "Run external", source: "desktop", status: "idle", created_at: 40, updated_at: 40 }];
  vi.stubGlobal("EventSource", class { onmessage?: (event: { data: string }) => void; constructor() { events = this; } close() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  HTMLElement.prototype.showPopover = vi.fn();
  HTMLElement.prototype.hidePopover = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("Recent keeps running sessions first and lists each filtered session once, including pins", () => {
  localStorage.setItem("session-pins", JSON.stringify(["old"]));
  render(<App />); publish();
  const navigation = within(screen.getByRole("navigation", { name: "Sessions" }));
  expect(navigation.getByText("Pinned")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  expect(titles()).toEqual(["Run active", "Run newest", "Run middle"]);
  expect(navigation.queryByText("Pinned")).toBeNull();
  expect(navigation.queryByRole("button", { expanded: true })).toBeNull();
  expect(navigation.getAllByRole("button", { name: "Unpin session" })).toHaveLength(1);
  sessions = sessions.map(session => session.id === "old" ? { ...session, updated_at: 50 } : session);
  publish();
  expect(titles()).toEqual(["Run active", "Run newest", "Run middle"]);
  cleanup();
  render(<App />); publish();
  expect(screen.getByRole("button", { name: "Recent", pressed: true })).toBeTruthy();
  expect(titles()).toEqual(["Run active", "Run newest", "Run middle"]);
});

test("view switching preserves search, source, status and project filters", () => {
  render(<App />); publish();
  fireEvent.click(screen.getByRole("combobox", { name: "Session source" }));
  fireEvent.click(screen.getByRole("option", { name: "All sessions", hidden: true }));
  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  expect(titles()).toEqual(["Run active", "Run external", "Run newest", "Run middle"]);
  fireEvent.change(screen.getByRole("textbox", { name: "Search sessions" }), { target: { value: "queue" } });
  fireEvent.click(screen.getByRole("button", { name: "Session filters" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Filter by status" }));
  fireEvent.click(screen.getByRole("option", { name: "Idle", hidden: true }));
  fireEvent.click(screen.getByRole("combobox", { name: "Filter by project" }));
  fireEvent.click(screen.getByRole("option", { name: "platform", hidden: true }));
  expect(titles()).toEqual(["Run external", "Run newest"]);
  fireEvent.click(screen.getByRole("button", { name: "Projects" }));
  expect(within(screen.getByRole("navigation", { name: "Sessions" })).getByRole("button", { name: "platform2" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  expect(titles()).toEqual(["Run external", "Run newest"]);
  fireEvent.change(screen.getByRole("textbox", { name: "Search sessions" }), { target: { value: "newest" } });
  expect(titles()).toEqual(["Run newest"]);
});

test("keyboard session navigation follows the visible Recent order", async () => {
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("fetch", async (url: string) => {
    const session = sessions.find(session => url.includes(`/sessions/${session.id}/`));
    return new Response(JSON.stringify({ session, history: { items: [], next_cursor: null, source: "codex", warning: null } }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  render(<App />); publish();
  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  await act(async () => fireEvent.keyDown(document.body, { altKey: true, key: "ArrowDown" }));
  expect(location.pathname).toBe("/sessions/old");
  await act(async () => fireEvent.keyDown(document.body, { altKey: true, key: "ArrowDown" }));
  expect(location.pathname).toBe("/sessions/new");
  await act(async () => fireEvent.keyDown(document.body, { altKey: true, key: "ArrowUp" }));
  expect(location.pathname).toBe("/sessions/old");
});

test("Overview is above search and secondary filters stay collapsed until requested", () => {
  render(<App />); publish();
  const overview = screen.getByRole("button", { name: /^Overview/ });
  const search = screen.getByRole("textbox", { name: "Search sessions" });
  expect(overview.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole("button", { name: "New agent" })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Session source" })).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Filter by project" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Session filters" }));
  expect(screen.getByRole("combobox", { name: "Filter by project" })).toBeTruthy();
});

test("running rows move only when a new run starts, and finished rows move below the running group", () => {
  sessions = sessions.filter(session => session.source === "claude").map(session => session.id === "new" ? { ...session, status: "active" } : session);
  sessions.push({ ...sessions[2], id: "history", title: "Run history", created_at: 25, updated_at: 250 });
  render(<App />); publish();
  fireEvent.click(screen.getByRole("button", { name: "Recent" }));
  expect(titles()).toEqual(["Run newest", "Run active", "Run history", "Run middle"]);
  sessions = sessions.map(session => session.id === "old" ? { ...session, updated_at: 500 } : session);
  publish();
  expect(titles()).toEqual(["Run newest", "Run active", "Run history", "Run middle"]);
  sessions = sessions.map(session => session.id === "middle" ? { ...session, status: "active", updated_at: 600 } : session);
  publish();
  expect(titles()).toEqual(["Run middle", "Run newest", "Run active", "Run history"]);
  sessions = sessions.map(session => session.id === "new" ? { ...session, status: "idle", updated_at: 700 } : session);
  publish();
  expect(titles()).toEqual(["Run middle", "Run active", "Run newest", "Run history"]);
  sessions = sessions.map(session => session.id === "old" ? { ...session, updated_at: 900 } : session);
  publish();
  expect(titles()).toEqual(["Run middle", "Run active", "Run newest", "Run history"]);
});

test("nested agents stay in their root chat while pins, search, status and missing roots remain discoverable", () => {
  const root = sessions[0];
  sessions = [root,
    { ...root, id: "child", title: "Run child", parent_id: root.id, depth: 1, cwd: "/repos/child", status: "idle" },
    { ...root, id: "grandchild", title: "Run grandchild", parent_id: "child", root_id: root.id, depth: 2 },
    { ...root, id: "pinned-child", title: "Run pinned child", parent_id: root.id, root_id: root.id, depth: 1 },
    { ...root, id: "orphan", title: "Run orphan", parent_id: "missing", root_id: "missing", depth: 1 }];
  localStorage.setItem("session-pins", JSON.stringify(["pinned-child"]));
  render(<App />); publish();
  expect(new Set(titles())).toEqual(new Set(["Run pinned child", "Run active", "Run orphan"]));
  const main = within(screen.getByRole("main"));
  expect(main.queryByText("Run child")).toBeNull();
  expect(main.queryByText("Run grandchild")).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Search sessions" }), { target: { value: "grandchild" } });
  expect(titles()).toEqual(["Run grandchild"]);
  fireEvent.change(screen.getByRole("textbox", { name: "Search sessions" }), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Session filters" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Filter by status" }));
  fireEvent.click(screen.getByRole("option", { name: "Working", hidden: true }));
  expect(titles()).toContain("Run grandchild");
  fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Filter by project" }));
  fireEvent.click(screen.getByRole("option", { name: "child", hidden: true }));
  expect(titles()).toEqual(["Run child"]);
  fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
  sessions = sessions.filter(session => session.id !== root.id); publish();
  expect(titles()).toContain("Run child");
  expect(titles()).toContain("Run grandchild");
});
