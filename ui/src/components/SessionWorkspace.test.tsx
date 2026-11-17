import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Artifact, Child, Item, Session } from "../api";
import { SessionWorkspace } from "./SessionWorkspace";

vi.mock("./BackgroundShells", () => ({ BackgroundShells: () => null }));
const root: Session = { id: "root", title: "Build parser", preview: "", cwd: "/repo/parser", model: "codex", source: "agents", origin: "appServer", status: "idle", flags: [], created_at: 1, updated_at: 1, archived: false, can_send: true, reason: null, messages: [] };
const child: Session = { ...root, id: "child", title: "Review parser", parent_id: "root", root_id: "root", depth: 1 };
const children: Child[] = [{ thread_id: "child", parent_id: "root", name: child.title, cwd: "/repo/parser", depth: 1, status: "idle" }];
const screenshot: Artifact = { id: "screen", name: "parser.png", kind: "image", mime: "image/png", content_url: "/screen.png", size: 10, characters: 0, pages: null, image_count: 1, status: "ready" };
let items: Item[];
let childItems: Item[];
let fetcher: ReturnType<typeof vi.fn>;
const props = { id: "root", summary: root, sessions: [root, child], connected: true, onChange: vi.fn(), pinned: false, togglePin: vi.fn() };

beforeEach(() => {
  localStorage.clear();
  childItems = [{ id: "child-answer", type: "agentMessage", text: "Parser review complete" }];
  items = [{ id: "ask", type: "userMessage", content: [{ type: "text", text: "Fix parser edge cases" }], attachments: [screenshot] }];
  HTMLElement.prototype.showPopover = vi.fn(); HTMLElement.prototype.hidePopover = vi.fn();
  HTMLElement.prototype.scrollIntoView = vi.fn();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/view") ? {
    session: url.includes("/child/") ? child : root,
    children: url.includes("/child/") ? [] : children,
    history: { items: url.includes("/child/") ? childItems : items, source: "codex", next_cursor: null, warning: null },
  } : url.endsWith("/sources") ? { items: childItems, next_cursor: null, source: "codex" } : { status: "available", processes: [] }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function open() {
  const view = render(<SessionWorkspace {...props} />);
  await screen.findByRole("button", { name: "Open subagents (1)" });
  return view;
}

test("agents open in deduplicated side tabs, preserving both chat drafts and the parent DOM", async () => {
  const navigate = vi.fn();
  render(<SessionWorkspace {...props} select={navigate} />);
  const parent = screen.getByRole("region", { name: "Parent conversation" });
  const parentDraft = within(parent).getByRole("textbox", { name: "Message Codex" });
  fireEvent.change(parentDraft, { target: { value: "Keep the parent task" } });
  fireEvent.click(await screen.findByRole("button", { name: "Open subagents (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Open chat for Review parser" }));
  const tab = await screen.findByRole("tab", { name: "Review parser" });
  const panel = screen.getByRole("tabpanel", { name: "Review parser" });
  await within(panel).findByText("Parser review complete");
  const childDraft = within(panel).getByRole("textbox", { name: "Message Codex" });
  fireEvent.change(childDraft, { target: { value: "Check another edge" } });
  expect((parentDraft as HTMLTextAreaElement).value).toBe("Keep the parent task");
  expect(navigate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("tab", { name: "Subagents" }));
  fireEvent.click(screen.getByRole("button", { name: "Open chat for Review parser" }));
  expect(screen.getAllByRole("tab", { name: "Review parser" })).toHaveLength(1);
  expect(screen.getByRole("tabpanel", { name: "Review parser" })).toBe(panel);
  expect((within(panel).getByRole("textbox", { name: "Message Codex" }) as HTMLTextAreaElement).value).toBe("Check another edge");
  expect(within(parent).getByRole("textbox", { name: "Message Codex" })).toBe(parentDraft);
  expect(tab.getAttribute("aria-selected")).toBe("true");
  fireEvent.click(within(panel).getByRole("button", { name: "Back to parent" }));
  expect(screen.queryByRole("complementary", { name: "Session tabs" })).toBeNull();
  expect((parentDraft as HTMLTextAreaElement).value).toBe("Keep the parent task");
});

test("sources show prepared screenshots, generated images, and real links with previews", async () => {
  items.push({ id: "created", type: "imageGeneration", images: [{ ...screenshot, id: "generated", name: "result.png" }] });
  items.push({ id: "answer", type: "agentMessage", text: "Read [guide](https://example.com/guide) and [report](/repo/report.md)." });
  await open();
  const rail = screen.getByRole("complementary", { name: "Session resources" });
  fireEvent.click(within(rail).getByRole("button", { name: "Sources" }));
  const panel = screen.getByRole("tabpanel", { name: "Sources" });
  expect(within(panel).getByText("Attached to the conversation")).toBeTruthy();
  expect(within(panel).getByText("Generated by the agent")).toBeTruthy();
  expect(within(panel).getByRole("link", { name: /^guide/ }).getAttribute("href")).toBe("https://example.com/guide");
  expect(within(panel).getByRole("button", { name: /^report/ }).getAttribute("aria-haspopup")).toBe("dialog");
  fireEvent.click(within(panel).getByRole("button", { name: "Preview result.png" }));
  const preview = await screen.findByRole("dialog", { name: "Preview of result.png" });
  expect(within(preview).getByRole("img", { name: "result.png" })).toBeTruthy();
  fireEvent.click(within(preview).getByRole("button", { name: "Close dialog" }));
  fireEvent.click(within(panel).getByRole("button", { name: "Add sources" }));
  expect(screen.getByRole("textbox", { name: "Message Codex" })).toBeTruthy();
  expect(screen.queryByRole("complementary", { name: "Session tabs" })).toBeNull();
});

test("changes open in a tab without covering the parent and tabs support keyboard navigation", async () => {
  items.push({ id: "run", type: "turn", turn_id: "t", diff: "--- a/parser.ts\n+++ b/parser.ts\n@@ -1 +1 @@\n-old\n+fixed\n" });
  await open();
  const rail = screen.getByRole("complementary", { name: "Session resources" });
  fireEvent.click(within(rail).getByRole("button", { name: /^Changes/ }));
  await screen.findByRole("table", { name: "Diff for parser.ts" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("region", { name: "Parent conversation" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open a tab" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Sources" }));
  const tabs = screen.getByRole("tablist", { name: "Session panels" });
  fireEvent.keyDown(tabs, { key: "ArrowLeft" });
  expect(screen.getByRole("tab", { name: "Changes" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Maximize panel" }));
  expect(screen.getByRole("button", { name: "Restore split view" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close Changes tab" }));
  expect(screen.getByRole("tab", { name: "Sources" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Close Sources tab" }));
  expect(screen.queryByRole("complementary", { name: "Session tabs" })).toBeNull();
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
});


test("child resource tabs keep their own diff live after closing the chat tab", async () => {
  const patch = (text: string) => [{ id: "child-run", type: "turn", turn_id: "child-turn", diff: `--- a/child.ts\n+++ b/child.ts\n@@ -1 +1 @@\n-old\n+${text}\n` }];
  childItems = patch("first change");
  const view = await open();
  fireEvent.click(screen.getByRole("button", { name: "Open subagents (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Open chat for Review parser" }));
  const chat = await screen.findByRole("tabpanel", { name: "Review parser" });
  await within(chat).findByText("1 file changed");
  fireEvent.click(within(chat).getByRole("button", { name: "Review latest file changes" }));
  const changes = await screen.findByRole("tabpanel", { name: "Changes · Review parser" });
  await within(changes).findByRole("table", { name: "Diff for child.ts" });
  fireEvent.click(screen.getByRole("button", { name: "Close Review parser tab" }));
  childItems = patch("changed again");
  view.rerender(<SessionWorkspace {...props} sessions={[root, { ...child, revision: "new" }]} />);
  await waitFor(() => expect(within(changes).getByRole("table", { name: "Diff for child.ts" }).textContent).toContain("changed again"));
  expect(screen.queryByRole("tab", { name: "Review parser" })).toBeNull();
  expect(screen.getByRole("tab", { name: "Changes · Review parser" }).getAttribute("aria-selected")).toBe("true");
});

test("earlier Sources paginate history even while older loaded steps are folded", async () => {
  items = Array.from({ length: 120 }, (_, i) => ({ id: `step-${i}`, type: "agentMessage", text: `Review step ${i}` }));
  const original = fetcher.getMockImplementation() as (url: string) => Promise<Response>;
  fetcher.mockImplementation(async (url: string) => {
    if (url.includes("/history?")) return new Response(JSON.stringify({ items: [{ id: "earlier-file", type: "userMessage", content: [], attachments: [screenshot] }], next_cursor: null, source: "codex" }), { status: 200 });
    const result = await original(url);
    if (!url.endsWith("/view")) return result;
    const data = await result.json(); data.history.next_cursor = "before";
    return new Response(JSON.stringify(data), { status: 200 });
  });
  await open();
  const rail = screen.getByRole("complementary", { name: "Session resources" });
  fireEvent.click(within(rail).getByRole("button", { name: "Sources" }));
  fireEvent.click(screen.getByRole("button", { name: "Load sources from earlier conversation" }));
  await within(screen.getByRole("tabpanel", { name: "Sources" })).findByRole("button", { name: "Preview parser.png" });
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("/history?cursor=before"))).toBe(true);
  expect(screen.queryByRole("button", { name: "Load sources from earlier conversation" })).toBeNull();
});


test("browser-style tab gestures support middle close, reopen, cycling, numbering and reordering", async () => {
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Open subagents (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Open a tab" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Sources" }));
  fireEvent.keyDown(window, { altKey: true, key: "PageUp" });
  expect(screen.getByRole("tab", { name: "Subagents" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.keyDown(window, { altKey: true, key: "9" });
  expect(screen.getByRole("tab", { name: "Sources" }).getAttribute("aria-selected")).toBe("true");
  fireEvent(screen.getByRole("tab", { name: "Sources" }), new MouseEvent("auxclick", { bubbles: true, button: 1 }));
  expect(screen.queryByRole("tab", { name: "Sources" })).toBeNull();
  fireEvent.keyDown(window, { altKey: true, shiftKey: true, key: "T" });
  expect(screen.getByRole("tab", { name: "Sources" }).getAttribute("aria-selected")).toBe("true");
  fireEvent.keyDown(window, { ctrlKey: true, shiftKey: true, key: "PageUp" });
  expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual(["Sources", "Subagents"]);
  fireEvent.keyDown(window, { ctrlKey: true, key: "w" });
  expect(screen.queryByRole("tab", { name: "Sources" })).toBeNull();
  fireEvent.keyDown(window, { ctrlKey: true, shiftKey: true, key: "T" });
  expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual(["Sources", "Subagents"]);
  const dragged = new Map<string, string>();
  const dataTransfer = { types: ["text/x-claudex-tab"], setData: (type: string, value: string) => dragged.set(type, value), getData: (type: string) => dragged.get(type), effectAllowed: "", dropEffect: "" };
  fireEvent.dragStart(screen.getByRole("tab", { name: "Sources" }).parentElement!, { dataTransfer });
  fireEvent.drop(screen.getByRole("tab", { name: "Subagents" }).parentElement!, { dataTransfer });
  expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual(["Subagents", "Sources"]);
  fireEvent.contextMenu(screen.getByRole("tab", { name: "Subagents" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Close tabs to the right" }));
  expect(screen.getAllByRole("tab").map(tab => tab.textContent)).toEqual(["Subagents"]);
});

test("Sources include files from unopened subagents and retain child preview scope", async () => {
  childItems = [{ id: "child-image", type: "imageView", images: [{ ...screenshot, id: "from-child", name: "child.png" }] }];
  await open();
  const parent = screen.getByRole("region", { name: "Parent conversation" });
  expect(within(parent).queryByRole("button", { name: "Review changes" })).toBeNull();
  fireEvent.click(within(screen.getByRole("complementary", { name: "Session resources" })).getByRole("button", { name: "Sources" }));
  const panel = screen.getByRole("tabpanel", { name: "Sources" });
  fireEvent.click(await within(panel).findByRole("button", { name: "Preview child.png" }));
  expect(await screen.findByRole("dialog", { name: "Preview of child.png" })).toBeTruthy();
  expect(fetcher.mock.calls.some(([url]) => String(url) === "/api/sessions/child/sources")).toBe(true);
  expect(fetcher.mock.calls.some(([url]) => String(url) === "/api/sessions/child/view")).toBe(false);
});
