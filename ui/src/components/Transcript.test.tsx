import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { Item, Session } from "../api";
import { Transcript } from "./Transcript";

afterEach(cleanup);
const marker: Item = { id: "turn-t", type: "turn", turn_id: "t", status: "completed", started_at: 100, completed_at: 115 };
const user: Item = { id: "u", type: "userMessage", turn_id: "t", content: [{ type: "text", text: "Fix the queue" }] };
const update: Item = { id: "update", type: "agentMessage", turn_id: "t", phase: "commentary", text: "Checking delivery" };
const command: Item = { id: "cmd", type: "commandExecution", turn_id: "t", command: "npm test", status: "completed", aggregatedOutput: "All checks passed" };
const final: Item = { id: "final", type: "agentMessage", turn_id: "t", phase: "final_answer", text: "Fixed the queue.", _started_at: 109 };

test("commentary stays outside collapsed desktop activity and final work time", async () => {
  render(<Transcript items={[marker, user, update, command, final]} />);
  expect(await screen.findByText("Fix the queue")).toBeTruthy();
  expect(await screen.findByText("Fixed the queue.")).toBeTruthy();
  const toggle = screen.getByRole("button", { name: /Terminal/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByText("Checking delivery").closest(".worklog")).toBeNull();
  expect(screen.getByText("Worked for 9s")).toBeTruthy();
  expect(screen.queryByText("Turn completed")).toBeNull();
  fireEvent.click(toggle);
  expect(await screen.findByText("Checking delivery")).toBeTruthy();
  expect(screen.getByText("All checks passed")).toBeTruthy();
});

test("completed command disclosures keep their label while the turn continues", async () => {
  const active = { ...marker, status: "inProgress", completed_at: undefined };
  const { rerender } = render(<Transcript items={[active, user, update, command]} />);
  expect(await screen.findByText("Checking delivery")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Terminal/ }).getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByRole("status").textContent).toBe("Thinking");
  rerender(<Transcript items={[active, user, update, command, final]} />);
  expect(await screen.findByText("Fixed the queue.")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Terminal/ }).getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByText("Checking delivery")).toBeTruthy();
});

test("public reasoning stays inspectable during work and in completed history", async () => {
  const active = { ...marker, status: "inProgress" };
  const thinking: Item = { id: "r", type: "reasoning", turn_id: "t", summary: ["**Inspecting delivery**\nChecking **ordering** before changing it."], content: [] };
  const { rerender } = render(<Transcript items={[active, user, thinking]} />);
  const toggle = screen.getByRole("button", { name: "Inspecting delivery" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(await screen.findByText("ordering", { selector: "strong" })).toBeTruthy();
  rerender(<Transcript items={[marker, user, thinking, final]} />);
  expect(screen.getByRole("button", { name: "Inspecting delivery" })).toBeTruthy();
  expect(screen.getByText("ordering", { selector: "strong" })).toBeTruthy();
});

test("empty reasoning provides only an active status and no empty completed panel", async () => {
  const thinking: Item = { id: "r", type: "reasoning", turn_id: "t", status: "inProgress", summary: [], content: [], encrypted_content: "private bytes" };
  const { rerender } = render(<Transcript items={[{ ...marker, status: "inProgress" }, user, thinking]} />);
  expect(screen.getByRole("status").textContent).toBe("Thinking");
  expect(screen.queryByRole("button", { name: "Thinking" })).toBeNull();
  rerender(<Transcript items={[marker, user, thinking, final]} />);
  expect(screen.queryByText("Thinking")).toBeNull();
  expect(screen.queryByText("private bytes")).toBeNull();
  expect(await screen.findByText("Fixed the queue.")).toBeTruthy();
});

test("thinking and tool disclosures preserve transcript order without merging their labels", () => {
  const thought = (id: string, heading: string): Item => ({ id, type: "reasoning", turn_id: "t", summary: [`**${heading}**\nPublic summary.`] });
  const { container } = render(<Transcript items={[marker, user, thought("r1", "Inspecting inputs"), command, thought("r2", "Checking results"), { ...command, id: "cmd2" }, final]} />);
  const ids = [...container.querySelectorAll("[data-item-id]")].map(node => node.getAttribute("data-item-id"));
  expect(ids).toEqual(["u", "r1", "cmd", "r2", "cmd2", "final"]);
  expect(screen.getAllByRole("button", { name: /Terminal/ })).toHaveLength(2);
});

test("native running action types distinguish reading, searching and commands", () => {
  const active = { ...marker, status: "inProgress" };
  const running = { ...command, status: "inProgress", commandActions: [{ type: "read", path: "queue.ts" }] };
  const { rerender } = render(<Transcript items={[active, user, running]} />);
  expect(screen.getByRole("button", { name: "Reading files" })).toBeTruthy();
  rerender(<Transcript items={[active, user, { ...running, commandActions: [{ type: "search", query: "queue" }] }]} />);
  expect(screen.getByRole("button", { name: "Searching files" })).toBeTruthy();
  rerender(<Transcript items={[active, user, { ...running, commandActions: [] }]} />);
  expect(screen.getByRole("button", { name: "Running command" })).toBeTruthy();
});

test("search opens recorded reasoning alongside independently folded commands", async () => {
  const thinking: Item = { id: "r", type: "reasoning", turn_id: "t", summary: ["**Checking delivery**\nInspecting **ordering**."] };
  render(<Transcript items={[marker, user, thinking, command, final]} matchedIds={new Set([thinking.id])} />);
  expect(await screen.findByText("ordering", { selector: "strong" })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Terminal/ }).getAttribute("aria-expanded")).toBe("false");
});

test("an approval request remains visible beside the available reasoning summary", () => {
  const thinking: Item = { id: "r", type: "reasoning", turn_id: "t", summary: ["**Checking delivery**\nInspecting ordering."] };
  const session = { id: "s", activity: { turn_id: "t", phase: "waiting_approval", label: "Waiting for approval" } } as Session;
  render(<Transcript items={[{ ...marker, status: "inProgress" }, user, thinking]} session={session} />);
  expect(screen.getByRole("status").textContent).toBe("Waiting for approval");
  expect(screen.getByRole("button", { name: "Checking delivery" })).toBeTruthy();
});

test("async responses remain standalone and stopped work stays inspectable", async () => {
  const active = { ...marker, status: "inProgress" };
  const { rerender } = render(<Transcript items={[active, user, update, { ...final, delivery: "async" }]} />);
  expect(await screen.findByText("Checking delivery")).toBeTruthy();
  rerender(<Transcript items={[{ ...marker, status: "interrupted" }, user, update, command]} />);
  expect(await screen.findByText("Checking delivery")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("Stopped after 15s");
  const toggle = screen.getByRole("button", { name: /Terminal/ });
  fireEvent.click(toggle);
  expect(screen.getByRole("button", { name: /Terminal/ })).toBeTruthy();
});

test("search reveals folded work and manual disclosure choices survive normal updates", async () => {
  const items = [marker, user, update, command, final];
  const { rerender } = render(<Transcript items={items} matchedIds={new Set([command.id])} />);
  expect(screen.getByText("All checks passed")).toBeTruthy();
  rerender(<Transcript items={items} />);
  fireEvent.click(screen.getByRole("button", { name: /Terminal/ }));
  expect(screen.getByRole("button", { name: /Terminal/ })).toBeTruthy();
  rerender(<Transcript items={[...items]} />);
  expect(screen.getByText("Checking delivery")).toBeTruthy();
});

test("nested tools open only while running inside an explicitly expanded activity group", () => {
  const active = { ...marker, status: "inProgress" };
  const { rerender } = render(<Transcript items={[active, user, command]} />);
  expect(screen.queryByText("All checks passed")).toBeNull();
  rerender(<Transcript items={[active, user, { ...command, status: "inProgress" }]} />);
  fireEvent.click(screen.getByRole("button", { name: "Running command" }));
  expect(screen.getByText("All checks passed")).toBeTruthy();
  rerender(<Transcript items={[active, user, command]} />);
  expect(screen.queryByText("All checks passed")).toBeNull();
});

test("native subagent tool disclosures follow the running and completed lifecycle", async () => {
  const active = { ...marker, status: "inProgress" };
  const call: Item = { id: "child-call", type: "collabAgentToolCall", turn_id: "t", status: "inProgress", tool: "spawn_agent", prompt: "Inspect queue ordering", receiverThreadIds: [] };
  const { rerender } = render(<Transcript items={[active, user, call]} />);
  fireEvent.click(screen.getByRole("button", { name: /Using spawn_agent/ }));
  expect(await screen.findByText("Inspect queue ordering")).toBeTruthy();
  expect(document.querySelector('details[open]')).toBeTruthy();
  rerender(<Transcript items={[active, user, { ...call, status: "completed" }]} />);
  expect(document.querySelector('details[open]')).toBeNull();
});

test("each commentary paragraph splits consecutive tool calls into separate desktop disclosures", async () => {
  const second = { ...command, id: "cmd-2" };
  render(<Transcript items={[marker, user, command, update, second, final]} />);
  expect(screen.getAllByRole("button", { name: /Terminal/ })).toHaveLength(2);
  const paragraph = await screen.findByText("Checking delivery");
  expect(paragraph.closest(".worklog")).toBeNull();
  expect(document.querySelector(".worklog")).toBeNull();
});

test("multiple completed calls share one collapsed group while a single call is already a standalone disclosure", () => {
  render(<Transcript items={[marker, user, command, { ...command, id: "cmd-2" }, final]} />);
  const toggle = screen.getByRole("button", { name: "Ran 2 commands" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("button", { name: /Terminal/ })).toBeNull();
  fireEvent.click(toggle);
  expect(screen.getAllByRole("button", { name: /Terminal/ })).toHaveLength(2);
});

test("prompts show native local times and long gaps get quiet separators without opening tools", () => {
  const first = Date.parse("2026-11-17T12:00:00Z") / 1000;
  const { container } = render(<Transcript items={[
    { ...marker, started_at: first, completed_at: first + 15 },
    { ...user, _started_at: first }, command, { ...final, _started_at: first + 9 },
    { ...marker, id: "turn-next", turn_id: "next", started_at: first + 3600 },
    { ...user, id: "u-next", turn_id: "next", content: [{ type: "text", text: "Continue checking" }] },
  ]} />);
  const times = container.querySelectorAll(".message-user time");
  expect(times).toHaveLength(2);
  expect(times[0].getAttribute("datetime")).toBe("2026-11-17T12:00:00.000Z");
  expect(times[1].getAttribute("datetime")).toBe("2026-11-17T13:00:00.000Z");
  expect(times[1].getAttribute("title")).toContain("Turn started");
  expect(container.querySelectorAll("[data-timeline-separator]")).toHaveLength(2);
  expect(container.querySelectorAll("[data-timeline-separator]")[1].textContent).toContain("later");
  expect(screen.getByRole("button", { name: /Terminal/ }).getAttribute("aria-expanded")).toBe("false");
});

test("unknown prompt times stay absent while recorded ISO times remain available", () => {
  const { container } = render(<Transcript items={[
    { ...user },
    { id: "old", type: "record", timestamp: "2026-11-17T14:05:00+02:00", record: { type: "message", role: "user", content: "Recorded prompt" } },
  ]} />);
  expect(container.querySelectorAll(".message-user time")).toHaveLength(1);
  expect(container.querySelector(".message-user time")?.getAttribute("datetime")).toBe("2026-11-17T12:05:00.000Z");
});

test("turn start is only a fallback for the first prompt and full native turns keep their work recap", () => {
  const { container } = render(<Transcript items={[
    marker, user, { ...user, id: "steer", content: [{ type: "text", text: "Also check the timeout" }] }, command,
    { ...final, _started_at: undefined },
  ]} />);
  expect(container.querySelectorAll(".message-user time")).toHaveLength(1);
  expect(screen.getByText("Worked for 15s")).toBeTruthy();
});


test("an older prompt outside the visible slice prevents assigning its turn start to a steering message", () => {
  const steer = { ...user, id: "steer", content: [{ type: "text", text: "Also check the timeout" }] };
  const { container } = render(<Transcript items={[steer]} metadata={[marker, user, command, steer]} />);
  expect(container.querySelector(".message-user time")).toBeNull();
});

test("long native turns use completion time when measuring the gap before the next prompt", () => {
  const first = Date.parse("2026-11-17T12:00:00Z") / 1000;
  const { container } = render(<Transcript items={[
    { ...marker, started_at: first, completed_at: first + 3600 }, user, command, { ...final, _started_at: undefined },
    { ...marker, id: "turn-next", turn_id: "next", started_at: first + 3900 },
    { ...user, id: "next", turn_id: "next" },
  ]} />);
  expect(container.querySelectorAll("[data-timeline-separator]")).toHaveLength(1);
});
