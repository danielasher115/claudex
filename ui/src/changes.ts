import { parsePatch, formatPatch, createTwoFilesPatch, type StructuredPatch, type StructuredPatchHunk } from "diff";
import { textOf, type Item } from "./api";

export interface FilePatch { raw: string; hunks: StructuredPatchHunk[]; binary: boolean }
export interface ChangedFile { path: string; oldPath?: string; kind: string; patches: FilePatch[]; added: number; removed: number }
export interface RunChanges { id: string; prompt: string; status: string; files: ChangedFile[]; source: "native" | "recorded"; lastItemId: string }
export interface DiffRow { kind: "add" | "delete" | "context" | "hunk" | "meta"; text: string; oldLine: number | null; newLine: number | null }

function cleanPath(path: string, cwd = "") {
  const value = path.replaceAll("\\", "/");
  const root = cwd.replaceAll("\\", "/").replace(/\/$/, "");
  return root && value.startsWith(root + "/") ? value.slice(root.length + 1) : value;
}

export function diffRows(patch: FilePatch): DiffRow[] {
  return patch.hunks.flatMap(hunk => {
    let oldLine = hunk.oldStart, newLine = hunk.newStart;
    const rows: DiffRow[] = [{ kind: "hunk", text: `@@ -${hunk.oldLines === 0 ? hunk.oldStart - 1 : hunk.oldStart},${hunk.oldLines} +${hunk.newLines === 0 ? hunk.newStart - 1 : hunk.newStart},${hunk.newLines} @@`, oldLine: null, newLine: null }];
    for (const line of hunk.lines) {
      const kind = line.startsWith("+") ? "add" : line.startsWith("-") ? "delete" : line.startsWith("\\") ? "meta" : "context";
      rows.push({ kind, text: kind === "meta" ? line : line.slice(1), oldLine: kind === "add" || kind === "meta" ? null : oldLine++, newLine: kind === "delete" || kind === "meta" ? null : newLine++ });
    }
    return rows;
  });
}

export function diffFiles(raw: string, path = "", kind?: { type?: string; move_path?: string | null }, cwd = ""): ChangedFile[] {
  const source = path && kind?.type === "add" ? createTwoFilesPatch("/dev/null", path, "", raw) : path && kind?.type === "delete" ? createTwoFilesPatch(path, "/dev/null", raw, "") : raw;
  let parsed: StructuredPatch[];
  try { parsed = parsePatch(source); } catch { parsed = []; }
  if (!parsed.some(patch => patch.hunks.length || patch.isGit || patch.oldFileName || patch.newFileName)) {
    return [{ path: cleanPath(kind?.move_path || path || "Recorded patch", cwd), oldPath: kind?.move_path ? cleanPath(path, cwd) : undefined, kind: kind?.move_path ? "rename" : kind?.type ?? "update", patches: [{ raw, hunks: [], binary: false }], added: 0, removed: 0 }];
  }
  return parsed.map(patch => {
    const original = patch.oldFileName ? patch.oldFileName.replace(/^[ab]\//, "") : path;
    const target = patch.newFileName === "/dev/null" ? original : patch.newFileName?.replace(/^[ab]\//, "");
    const next = cleanPath(kind?.move_path || path || target || "Recorded patch", cwd);
    const renamed = patch.isRename || Boolean(kind?.move_path);
    const patchKind = renamed ? "rename" : patch.isCreate || patch.oldFileName === "/dev/null" ? "add" : patch.isDelete || patch.newFileName === "/dev/null" ? "delete" : kind?.type ?? "update";
    const counts = patch.hunks.flatMap(hunk => hunk.lines);
    return { path: next, oldPath: renamed ? cleanPath(path || original, cwd) : undefined, kind: patchKind, patches: [{ raw: parsed.length === 1 ? source : formatPatch(patch), hunks: patch.hunks, binary: Boolean(patch.isBinary) }], added: counts.filter(line => line.startsWith("+")).length, removed: counts.filter(line => line.startsWith("-")).length };
  });
}

export function runChanges(items: Item[], cwd = ""): RunChanges[] {
  const runs = new Map<string, RunChanges & { aggregate?: string }>();
  let current = "recorded";
  for (const item of items) {
    const record = (item.type === "record" ? item.record : item) as Record<string, unknown>;
    const id = String(item.turn_id || record.turn_id || (item.type === "turn" ? item.id : current));
    current = id;
    if (!runs.has(id)) runs.set(id, { id, prompt: "", status: "", files: [], source: "recorded", lastItemId: item.id });
    const run = runs.get(id)!;
    run.lastItemId = item.id;
    if (item.type === "turn") {
      run.status = String(item.status ?? "");
      if (typeof item.diff === "string") { run.aggregate = item.diff; run.source = "native"; }
    }
    if (!run.prompt && (record.type === "userMessage" || record.type === "message" && record.role === "user")) run.prompt = textOf(record.text ?? record.content).trim().split("\n")[0].slice(0, 120);
    if (record.type !== "fileChange" || record.status !== "completed" || !Array.isArray(record.changes)) continue;
    for (const change of record.changes) {
      if (!change || typeof change.path !== "string" || typeof change.diff !== "string") continue;
      for (const file of diffFiles(change.diff, change.path, change.kind, cwd)) {
        const existing = run.files.find(previous => previous.path === file.path);
        if (existing) { existing.patches.push(...file.patches); existing.added += file.added; existing.removed += file.removed; existing.kind = file.kind; }
        else run.files.push(file);
      }
    }
  }
  return [...runs.values()].map(({ aggregate, ...run }) => ({ ...run, files: aggregate === undefined ? run.files : aggregate.trim() ? diffFiles(aggregate, "", undefined, cwd) : [] }));
}
