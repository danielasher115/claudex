import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Child, Item, Session } from "../api";
import { SessionView } from "./SessionView";

vi.mock("./BackgroundShells", () => ({ BackgroundShells: () => null }));
let session: Session;
let items: Item[];
let children: Child[];
let contextUsage: unknown;
let request: ReturnType<typeof vi.fn>;
const response = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
const props = { id: "a", onChange: vi.fn(), pinned: false, togglePin: vi.fn() };

beforeEach(() => {
  localStorage.clear();
  session = { id: "a", title: "Composer controls", preview: "", cwd: "/repo", model: "codex", source: "codex", origin: "codex", status: "idle", flags: [], created_at: 1, updated_at: 1, archived: false, can_send: true, reason: null, messages: [] };
  items = []; children = []; contextUsage = null;
  HTMLElement.prototype.showPopover = vi.fn();
  HTMLElement.prototype.hidePopover = vi.fn();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  request = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/view")) return response({ session, history: { items, next_cursor: null, source: "codex", warning: null }, children, context_usage: contextUsage });
    if (init?.method === "POST") return response({ id: "sent", text: "Continue", mode: JSON.parse(String(init.body)).mode, status: "queued", created_at: 1 });
    return response({ status: "available", processes: [] });
  });
  vi.stubGlobal("fetch", request);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function open() {
  const result = render(<SessionView {...props} />);
  await screen.findByText("This conversation is ready");
  return result;
}
function chooseMode(label: string) {
  fireEvent.click(screen.getByRole("combobox", { name: "Delivery mode" }));
  fireEvent.click(screen.getByText(label, { selector: "[role=option] span" }));
}

test("steer is the default and idle sends still start the next turn", async () => {
  await open();
  expect(within(screen.getByRole("combobox", { name: "Delivery mode" })).getByText("Steer now")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "Message Codex" }), { target: { value: "Continue" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Message Codex" }), { key: "Enter" });
  await waitFor(() => expect(request.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const body = JSON.parse(String(request.mock.calls.find(([, init]) => init?.method === "POST")![1].body));
  expect(body.mode).toBe("queue"); expect(body.expected_turn_id).toBeFalsy();
});

test("delivery selection persists globally across sessions and reloads", async () => {
  const first = await open(); chooseMode("After current work");
  expect(localStorage.getItem("delivery-mode")).toBe("queue"); first.unmount();
  session = { ...session, id: "b" };
  const second = render(<SessionView {...props} id="b" />);
  await screen.findByText("This conversation is ready");
  expect(within(screen.getByRole("combobox", { name: "Delivery mode" })).getByText("After current work")).toBeTruthy();
  chooseMode("Steer now"); second.unmount(); await open();
  expect(within(screen.getByRole("combobox", { name: "Delivery mode" })).getByText("Steer now")).toBeTruthy();
});

test("active default steering targets the current turn", async () => {
  session = { ...session, status: "active", active_turn_id: "turn-now" }; await open();
  fireEvent.change(screen.getByRole("textbox", { name: "Message Codex" }), { target: { value: "Continue" } });
  fireEvent.click(screen.getByRole("button", { name: "Steer Codex" }));
  await waitFor(() => expect(request.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const body = JSON.parse(String(request.mock.calls.find(([, init]) => init?.method === "POST")![1].body));
  expect(body).toMatchObject({ mode: "steer", expected_turn_id: "turn-now" });
});

test("the composer pill reuses native run patches and opens the matching diff", async () => {
  items = [{ id: "turn-one", type: "turn", status: "completed", diff: "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1,2 @@\n-old\n+new\n+extra\n" }, { id: "turn-two", type: "turn", status: "completed" }];
  render(<SessionView {...props} />);
  const pill = await screen.findByRole("button", { name: "Review latest file changes" });
  expect(pill.closest(".composer-area")).toBeTruthy();
  expect(within(pill).getByText("1 file changed")).toBeTruthy();
  expect(within(pill).getByText("+2")).toBeTruthy(); expect(within(pill).getByText("-1")).toBeTruthy();
  fireEvent.click(pill); expect(await screen.findByRole("table", { name: "Diff for example.txt" })).toBeTruthy();
});

test("the context ring uses the latest native total and the recorded window", async () => {
  contextUsage = { last: { input_tokens: 430000, total_tokens: 464000 }, total: { total_tokens: 9000000 }, model_context_window: 828000 }; await open();
  const meter = screen.getByRole("meter", { name: "Context window" });
  expect(meter.getAttribute("aria-valuenow")).toBe("56");
  expect(meter.getAttribute("aria-valuetext")).toBe("464,000 / 828,000 tokens used");
  expect(screen.getByRole("tooltip").textContent).toContain("56% full");
  expect(screen.getByRole("tooltip").textContent).toContain("464,000 / 828,000 tokens used");
  expect(request.mock.calls.every(([url]) => !url.includes("/details"))).toBe(true);
});

test("the context tooltip escapes a clipped pane and follows its ring while focused or hovered", async () => {
  contextUsage = { last: { total_tokens: 500 }, model_context_window: 1000 };
  const view = await open();
  view.container.style.overflow = "hidden";
  const meter = screen.getByRole("meter", { name: "Context window" });
  const tooltip = screen.getByRole("tooltip");
  let left = 600;
  vi.stubGlobal("innerWidth", 800);
  vi.spyOn(meter, "getBoundingClientRect").mockImplementation(() => ({ left, top: 500, width: 28, height: 28, right: left + 28, bottom: 528, x: left, y: 500, toJSON: () => ({}) }));
  Object.defineProperty(tooltip, "offsetWidth", { configurable: true, value: 256 });
  Object.defineProperty(tooltip, "offsetHeight", { configurable: true, value: 120 });
  fireEvent.focus(meter);
  expect(view.container.contains(tooltip)).toBe(false);
  expect(document.body.contains(tooltip)).toBe(true);
  expect(tooltip.style.left).toBe("486px");
  expect(tooltip.style.top).toBe("368px");
  left = 750; fireEvent.scroll(view.container);
  expect(tooltip.style.left).toBe("536px");
  expect(tooltip.style.getPropertyValue("--tooltip-arrow-x")).toBe("227px");
  vi.stubGlobal("innerWidth", 1000); fireEvent.resize(window);
  expect(tooltip.style.left).toBe("636px");
  expect(tooltip.style.getPropertyValue("--tooltip-arrow-x")).toBe("127px");
  fireEvent.mouseEnter(meter); fireEvent.blur(meter);
  left = 0; fireEvent.resize(window);
  expect(tooltip.style.left).toBe("8px");
  expect(tooltip.style.getPropertyValue("--tooltip-arrow-x")).toBe("5px");
  fireEvent.mouseLeave(meter);
  left = 300; fireEvent.scroll(view.container);
  expect(tooltip.style.left).toBe("8px");
});

test("unknown context usage never turns cumulative session tokens into a context total", async () => {
  session = { ...session, tokens: 9000000 }; await open();
  expect(screen.queryByRole("meter")).toBeNull();
  expect(screen.getByRole("tooltip").textContent).toContain("Context usage has not been recorded");
  expect(screen.getByRole("tooltip").textContent).not.toContain("9,000,000");
});

test.each([
  [{ last: { input_tokens: 0 }, model_context_window: 1000 }, "0", "0 / 1,000 tokens used"],
  [{ last: { input_tokens: 250 }, model_context_window: 1000 }, "25", "250 / 1,000 tokens used"],
])("the ring accepts recorded input snapshots including zero (%j)", async (usage, percent, text) => {
  contextUsage = usage; await open();
  const meter = screen.getByRole("meter", { name: "Context window" });
  expect(meter.getAttribute("aria-valuenow")).toBe(percent);
  expect(meter.getAttribute("aria-valuetext")).toBe(text);
});

test("a context window without a latest native usage count remains unknown", async () => {
  contextUsage = { total: { total_tokens: 9000000 }, model_context_window: 828000 }; await open();
  expect(screen.queryByRole("meter")).toBeNull();
});

test("inspecting a tool prevents incoming updates from snapping the conversation to the bottom", async () => {
  items = [{ id: "turn", type: "turn", turn_id: "t", status: "completed" }, { id: "cmd", type: "commandExecution", turn_id: "t", command: "npm test", status: "completed", aggregatedOutput: "All checks passed" }];
  const view = render(<SessionView {...props} summary={{ ...session, revision: "one" }} />);
  const toggle = await screen.findByRole("button", { name: /Terminal/ });
  const scroller = view.container.querySelector(".overscroll-contain") as HTMLElement;
  Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1500 });
  scroller.scrollTop = 800;
  fireEvent.click(toggle);
  expect(screen.getByRole("button", { name: "Jump to latest" })).toBeTruthy();
  items = [...items, { id: "new", type: "agentMessage", turn_id: "t", text: "A later update" }];
  view.rerender(<SessionView {...props} summary={{ ...session, revision: "two" }} />);
  await screen.findByText("A later update");
  expect(scroller.scrollTop).toBe(800);
});


test("subagents nest under their direct parent and event links use matching names", async () => {
  session = { ...session, parent_id: "direct-parent", root_id: "top-parent", depth: 1 };
  children = [
    { thread_id: "grandchild", parent_id: "child", name: "Verify parser", depth: 3, cwd: "/repo", status: "idle", model: "codex", origin: "native" },
    { thread_id: "child", parent_id: "a", name: "Inspect parser", depth: 2, cwd: "/repo", status: "active", model: "codex", origin: "gateway", agent_path: "/root/parser" },
    { thread_id: "sibling", parent_id: "a", name: "Review API", depth: 2, cwd: "/repo", status: "idle", origin: "native" },
  ];
  items = [
    { id: "spawn", type: "collabAgentToolCall", tool: "spawn_agent", status: "completed", receiverThreadIds: ["grandchild"] },
    { id: "activity", type: "subAgentActivity", agentThreadId: "child", agentPath: "/root/parser", kind: "started" },
  ];
  const select = vi.fn();
  render(<SessionView {...props} select={select} />);
  const tree = await screen.findByRole("list", { name: "Subagents" });
  expect(tree.children).toHaveLength(2);
  const nested = within(tree).getByRole("list", { name: "Subagents of Inspect parser" });
  expect(within(nested).getByRole("button", { name: "Open chat for Verify parser" })).toBeTruthy();
  expect(within(nested).getByText("Idle")).toBeTruthy();
  expect(within(nested).getByText("codex")).toBeTruthy();
  fireEvent.click(within(tree).getByRole("button", { name: "Subagents of Inspect parser" }));
  expect(within(tree).queryByText("Verify parser")).toBeNull();
  fireEvent.click(within(tree).getByRole("button", { name: "Subagents of Inspect parser" }));
  fireEvent.click(within(tree).getByRole("button", { name: "Open chat for Verify parser" }));
  expect(select).toHaveBeenLastCalledWith("grandchild");
  fireEvent.click(screen.getByRole("button", { name: "Back to parent" }));
  expect(select).toHaveBeenLastCalledWith("direct-parent");
  fireEvent.click(screen.getByRole("button", { name: "Expand tools" }));
  const eventLink = await screen.findByRole("button", { name: "Verify parser" });
  fireEvent.click(eventLink);
  expect(select).toHaveBeenLastCalledWith("grandchild");
  const activity = document.querySelector('[data-codex-type="subAgentActivity"]') as HTMLElement;
  fireEvent.click(within(activity).getByRole("button", { name: "Inspect parser" }));
  expect(select).toHaveBeenLastCalledWith("child");
});
