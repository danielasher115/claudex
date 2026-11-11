import { expect, test } from "vitest";
import { diffFiles, diffRows, runChanges } from "./changes";

const patch = "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@ -10,2 +20,3 @@\n keep\n-old\n+new\n+extra\n";

test("Git patches expose real old/new line numbers and exclude headers from counts", () => {
  const file = diffFiles(patch)[0];
  expect(file.path).toBe("src/app.ts");
  expect([file.added, file.removed]).toEqual([2, 1]);
  expect(diffRows(file.patches[0]).filter(row => row.kind !== "hunk").map(row => [row.oldLine, row.newLine, row.text])).toEqual([[10, 20, "keep"], [11, null, "old"], [null, 21, "new"], [null, 22, "extra"]]);
});

test("runs keep repeated edits, exclude failed proposals, and prefer the native aggregate", () => {
  const items = [{ id: "turn-a", type: "turn", turn_id: "a", status: "completed" }, { id: "user", type: "userMessage", turn_id: "a", content: [{ type: "text", text: "Fix app" }] }, ...["one", "two"].map(id => ({ id, type: "fileChange", turn_id: "a", status: "completed", changes: [{ path: "/repo/src/app.ts", kind: { type: "update" }, diff: patch }] })), { id: "failed", type: "fileChange", turn_id: "a", status: "failed", changes: [{ path: "/repo/secret", diff: patch }] }];
  const run = runChanges(items, "/repo")[0];
  expect(run.prompt).toBe("Fix app");
  expect(run.files).toHaveLength(1);
  expect(run.files[0].patches).toHaveLength(2);
  expect(run.files[0].added).toBe(4);
  const native = runChanges([{ ...items[0], diff: patch }, ...items.slice(1)], "/repo")[0];
  expect(native.source).toBe("native");
  expect(native.files[0].patches).toHaveLength(1);
  expect(native.files[0].added).toBe(2);
  expect(runChanges([{ ...items[0], diff: "" }, ...items.slice(1)], "/repo")[0].files).toHaveLength(0);
});

test("additions, deletions, renames, binary and malformed patches retain their meaning", () => {
  expect(diffFiles("new\nfile\n", "new.txt", { type: "add" })[0].added).toBe(2);
  expect(diffFiles("old\nfile\n", "old.txt", { type: "delete" })[0].removed).toBe(2);
  const rename = diffFiles("diff --git a/old.txt b/new.txt\nsimilarity index 100%\nrename from old.txt\nrename to new.txt\n")[0];
  expect([rename.path, rename.oldPath, rename.kind]).toEqual(["new.txt", "old.txt", "rename"]);
  expect(diffFiles("diff --git a/a.png b/a.png\nBinary files a/a.png and b/a.png differ\n")[0].patches[0].binary).toBe(true);
  expect(diffFiles("unparsed patch", "app.ts")[0].patches[0].raw).toBe("unparsed patch");
});

test("native added file contents are literal even when the file itself contains a patch", () => {
  const file = diffFiles("@@ -1 +1 @@\n-old\n+new\n", "example.patch", { type: "add" })[0];
  expect([file.added, file.removed]).toEqual([3, 0]);
  expect(diffRows(file.patches[0]).filter(row => row.kind === "add").map(row => row.text)).toEqual(["@@ -1 +1 @@", "-old", "+new"]);
});

test("real a and b directories are preserved in explicit paths and renames", () => {
  expect(diffFiles("hello\n", "a/file.txt", { type: "add" })[0].path).toBe("a/file.txt");
  const file = diffFiles("diff --git a/a/old.txt b/a/new.txt\nsimilarity index 100%\nrename from a/old.txt\nrename to a/new.txt\n")[0];
  expect([file.oldPath, file.path]).toEqual(["a/old.txt", "a/new.txt"]);
});
