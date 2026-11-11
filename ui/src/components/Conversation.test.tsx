import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ConversationItem } from "./Conversation";

afterEach(cleanup);

test("collapsed tools do not render large output or raw records", () => {
  render(<ConversationItem item={{ id: "cmd", type: "commandExecution", command: "npm test", aggregatedOutput: "Six tests passed", status: "completed" }} />);
  expect(screen.queryByText("Six tests passed")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Terminal/ }));
  expect(screen.getByText("Six tests passed")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy output" })).toBeTruthy();
});

test("file changes expose readable diff lines", () => {
  render(<ConversationItem item={{ id: "file", type: "fileChange", status: "completed", changes: [{ path: "app.py", diff: "@@ -1 +1 @@\n-old line\n+new line\n" }] }} />);
  fireEvent.click(screen.getByRole("button", { name: /Edit files/ }));
  expect(screen.getByText("old line")).toBeTruthy();
  expect(screen.getByText("new line")).toBeTruthy();
});

test("an empty summary does not hide recorded reasoning content", () => {
  render(<ConversationItem item={{ id: "thinking", type: "reasoning", summary: [], content: ["This summary exists in content"] }} />);
  expect(screen.getByText("This summary exists in content")).toBeTruthy();
});

test("unavailable reasoning does not create an empty thinking card", () => {
  const { container } = render(<ConversationItem item={{ id: "thinking", type: "reasoning", summary: [], content: [] }} />);
  expect(container.querySelector(".tool-card")).toBeNull();
});

test("viewed images are visible inline and expand inside the app", async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  render(<ConversationItem item={{ id: "view", type: "imageView", path: "/workspace/screenshot.png", images: [{ id: "image", name: "screenshot.png", mime: "image/png", kind: "image", size: 32, characters: 0, pages: null, image_count: 1, status: "prepared", content_url: "/api/artifacts/image/content" }] }} />);
  expect(screen.getByRole("img", { name: "screenshot.png" }).getAttribute("src")).toBe("/api/artifacts/image/content");
  fireEvent.click(screen.getByRole("button", { name: "Preview screenshot.png" }));
  expect(await screen.findByRole("dialog", { name: "Preview of screenshot.png" })).toBeTruthy();
  expect(document.querySelector('a[target="_blank"]')).toBeNull();
});

test("thinking expands into Markdown with its native summary heading", async () => {
  const { container } = render(<ConversationItem item={{ id: "thinking", type: "reasoning", summary: ["**Inspecting the image**\nChecking **contrast**.\n\n- Read text\n- Check colors"] }} />);
  const toggle = screen.getByRole("button", { name: "Inspecting the image" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(await screen.findByText("contrast", { selector: "strong" })).toBeTruthy();
  expect(screen.getByRole("list").children.length).toBe(2);
  expect(container.querySelector(".reasoning-content")).toBeTruthy();
  expect(container.querySelector("button button")).toBeNull();
});

test("native plans, compaction, missing images and child agents have readable controls", async () => {
  const select = vi.fn();
  render(<><ConversationItem item={{ id: "plan", type: "plan", text: "1. Inspect\n2. Verify" }} /><ConversationItem item={{ id: "compact", type: "contextCompaction" }} /><ConversationItem item={{ id: "missing", type: "imageView", path: "/gone.png", media_error: "The recorded image is unavailable." }} /><ConversationItem select={select} item={{ id: "child", type: "subAgentActivity", agentPath: "/root/design", agentThreadId: "child-thread", kind: "started" }} /></>);
  expect(await screen.findByRole("region", { name: "Agent plan" })).toBeTruthy();
  expect(screen.getByText("Context compacted")).toBeTruthy();
  expect(screen.getByText("The recorded image is unavailable.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "/root/design" }));
  expect(select).toHaveBeenCalledWith("child-thread");
});
