import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Changes } from "./Changes";
import { DiffFile } from "./Diff";
import { FAMILY_CHANGES_ID, familyRunId, diffFiles, type RunChanges } from "../changes";

const patch = "--- a/src/app.ts\n+++ b/src/app.ts\n@@ -10,2 +20,2 @@\n keep\n-old code\n+new code\n";
const run: RunChanges = { id: "run-one", prompt: "Fix the app", status: "completed", files: diffFiles(patch), source: "native", lastItemId: "last" };
beforeEach(() => {
  HTMLElement.prototype.showPopover = vi.fn(function (this: HTMLElement) { this.style.display = "block"; });
  HTMLElement.prototype.hidePopover = vi.fn(function (this: HTMLElement) { this.style.display = "none"; });
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(cleanup);

test("review changes shows a file list, real line numbers, split layout and file filtering", () => {
  render(<Changes runs={[run]} initialRun={run.id} close={vi.fn()} />);
  expect(screen.getByRole("dialog", { name: "Review changes" })).toBeTruthy();
  expect(within(screen.getByRole("navigation", { name: "Changed files" })).getByRole("button", { name: /src\/app.ts/ })).toBeTruthy();
  const table = screen.getByRole("table", { name: "Diff for src/app.ts" });
  expect(table.textContent).toContain("10");
  expect(table.textContent).toContain("20");
  expect(table.textContent).toContain("old code");
  fireEvent.click(screen.getByRole("button", { name: "Split" }));
  expect(screen.getByRole("columnheader", { name: "Before" })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "After" })).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "Filter changed files" }), { target: { value: "missing" } });
  expect(screen.getByText("No matching files")).toBeTruthy();
});

test("embedded changes selects and filters files without opening a dialog", () => {
  const secondPatch = "--- a/src/other.ts\n+++ b/src/other.ts\n@@ -1 +1 @@\n-other old\n+other new\n";
  render(<Changes runs={[{ ...run, files: diffFiles(patch + secondPatch) }]} initialRun={run.id} close={vi.fn()} embedded />);
  const changes = screen.getByRole("region", { name: "Changes" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(within(changes).getByRole("combobox", { name: "Run" })).toBeTruthy();
  expect(within(changes).getByRole("button", { name: "Copy run diff" })).toBeTruthy();
  expect(within(changes).getByRole("table", { name: "Diff for src/app.ts" }).textContent).toContain("new code");
  fireEvent.click(within(changes).getByRole("button", { name: "src/other.ts" }));
  expect(within(changes).getByRole("table", { name: "Diff for src/other.ts" }).textContent).toContain("other new");
  expect(within(changes).queryByRole("table", { name: "Diff for src/app.ts" })).toBeNull();
  fireEvent.click(within(changes).getByRole("button", { name: "Split" }));
  expect(within(changes).getByRole("columnheader", { name: "Before" })).toBeTruthy();
  fireEvent.click(within(changes).getByRole("checkbox", { name: "Wrap lines" }));
  expect(within(changes).getByRole("table").parentElement?.classList.contains("diff-wrap")).toBe(true);
  expect(within(changes).getByRole("button", { name: "Copy diff" })).toBeTruthy();
  fireEvent.change(within(changes).getByRole("textbox", { name: "Filter changed files" }), { target: { value: "app.ts" } });
  expect(within(changes).getByRole("table", { name: "Diff for src/app.ts" })).toBeTruthy();
  expect(within(changes).queryByRole("button", { name: "src/other.ts" })).toBeNull();
});

test("per-file diffs collapse without mounting their code and preserve copy controls", () => {
  render(<DiffFile file={run.files[0]} />);
  const toggle = screen.getByRole("button", { name: "Collapse src/app.ts" });
  fireEvent.click(toggle);
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByRole("button", { name: "Copy diff" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Expand src/app.ts" }));
  expect(screen.getByRole("table")).toBeTruthy();
});

test("runs without patches state that no file changes were recorded", () => {
  render(<Changes runs={[{ ...run, files: [] }]} initialRun={run.id} close={vi.fn()} />);
  expect(screen.getByText("No file changes recorded for this run")).toBeTruthy();
});

test("all agents preserves overlapping paths and allows each agent run to be reviewed separately", () => {
  const owned = ["Manager", "Worker"].map((name, index) => {
    const owner = { sessionId: `agent-${index}`, name, cwd: `/worktree/${name}` };
    return { ...run, id: familyRunId(owner.sessionId, run.id), originalRunId: run.id, owner, files: diffFiles(patch.replace("new code", `${name} code`)).map(file => ({ ...file, owner })) };
  });
  const all: RunChanges = { ...run, id: FAMILY_CHANGES_ID, prompt: "All agents", aggregate: true, files: owned.flatMap(value => value.files) };
  render(<Changes embedded runs={[all, ...owned]} initialRun={FAMILY_CHANGES_ID} close={vi.fn()} />);
  expect(screen.getByText("2 file changes")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Copy run diff" })).toBeNull();
  expect(screen.getByRole("button", { name: "Copy diff" })).toBeTruthy();
  const files = screen.getByRole("navigation", { name: "Changed files" });
  expect(within(files).getByText("/worktree/Manager")).toBeTruthy();
  fireEvent.click(within(files).getByRole("button", { name: "Worker: src/app.ts" }));
  expect(screen.getByRole("table").textContent).toContain("Worker code");
  fireEvent.click(screen.getByRole("combobox", { name: "Run" }));
  fireEvent.click(screen.getByRole("option", { name: /Manager · Run 1/ }));
  expect(screen.getByRole("table").textContent).toContain("Manager code");
  expect(screen.getByRole("button", { name: "Copy run diff" })).toBeTruthy();
  fireEvent.click(screen.getByRole("combobox", { name: "Run" }));
  fireEvent.click(screen.getByRole("option", { name: /Worker · Run 1/ }));
  expect(screen.getByRole("table").textContent).toContain("Worker code");
});
