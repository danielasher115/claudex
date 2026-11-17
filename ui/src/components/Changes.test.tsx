import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Changes } from "./Changes";
import { DiffFile } from "./Diff";
import { diffFiles, type RunChanges } from "../changes";

const patch = "--- a/src/app.ts\n+++ b/src/app.ts\n@@ -10,2 +20,2 @@\n keep\n-old code\n+new code\n";
const run: RunChanges = { id: "run-one", prompt: "Fix the app", status: "completed", files: diffFiles(patch), source: "native", lastItemId: "last" };
beforeEach(() => {
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
