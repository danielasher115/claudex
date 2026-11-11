import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { Item } from "../api";
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

test("desktop activity is collapsed during work and commentary remains visible after the final starts", async () => {
  const active = { ...marker, status: "inProgress", completed_at: undefined };
  const { rerender } = render(<Transcript items={[active, user, update, command]} />);
  expect(await screen.findByText("Checking delivery")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Thinking" }).getAttribute("aria-expanded")).toBe("false");
  rerender(<Transcript items={[active, user, update, command, final]} />);
  expect(await screen.findByText("Fixed the queue.")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Terminal/ }).getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByText("Checking delivery")).toBeTruthy();
});

test("ordinary reasoning supplies the inline desktop heading without becoming a tool card", async () => {
  const active = { ...marker, status: "inProgress" };
  const thinking: Item = { id: "r", type: "reasoning", turn_id: "t", summary: ["**Inspecting delivery**\nChecking **ordering** before changing it."], content: [] };
  const { rerender } = render(<Transcript items={[active, user, thinking]} />);
  expect(screen.getByRole("status").textContent).toBe("Inspecting delivery");
  expect(screen.queryByText("ordering", { selector: "strong" })).toBeNull();
  rerender(<Transcript items={[active, user, { ...thinking, summary: [] }]} />);
  expect(screen.getByRole("status").textContent).toBe("Thinking");
  rerender(<Transcript items={[marker, user, { ...thinking, summary: [] }, final]} />);
  expect(screen.queryByText("Thinking")).toBeNull();
  expect(screen.getByText("Fixed the queue.")).toBeTruthy();
});

test("a plain native reasoning summary supplies the current thinking headline", () => {
  render(<Transcript items={[{ ...marker, status: "inProgress" }, user, { id: "r", type: "reasoning", turn_id: "t", summary: ["Inspecting SDK joins"], content: [] }]} />);
  expect(screen.getByRole("status").textContent).toBe("Inspecting SDK joins");
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
  fireEvent.click(screen.getByRole("button", { name: "Thinking" }));
  rerender(<Transcript items={[active, user, { ...command, status: "inProgress" }]} />);
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
