import { Fragment, useMemo, useState } from "react";
import { ChevronRight, FileCode2 } from "lucide-react";
import { diffRows, type ChangedFile, type DiffRow, type FilePatch } from "../changes";
import { CopyButton } from "./shared";

export function ChangeCounts({ files }: { files: Pick<ChangedFile, "added" | "removed">[] }) {
  const added = files.reduce((total, file) => total + file.added, 0), removed = files.reduce((total, file) => total + file.removed, 0);
  return <span className="diff-counts"><span className="text-added-ink" aria-label={`${added} lines added`}>+{added}</span><span className="text-removed-ink" aria-label={`${removed} lines removed`}>-{removed}</span></span>;
}

function splitRows(rows: DiffRow[]): { left?: DiffRow; right?: DiffRow; full?: DiffRow }[] {
  const result: { left?: DiffRow; right?: DiffRow; full?: DiffRow }[] = [];
  for (let i = 0; i < rows.length;) {
    const row = rows[i];
    if (row.kind === "hunk" || row.kind === "meta") { result.push({ full: row }); i++; }
    else if (row.kind === "context") { result.push({ left: row, right: row }); i++; }
    else {
      const removed: DiffRow[] = [], added: DiffRow[] = [];
      while (i < rows.length && rows[i].kind === "delete") removed.push(rows[i++]);
      while (i < rows.length && rows[i].kind === "add") added.push(rows[i++]);
      for (let j = 0; j < Math.max(removed.length, added.length); j++) result.push({ left: removed[j], right: added[j] });
    }
  }
  return result;
}

function DiffTable({ patch, path, split, wrap }: { patch: FilePatch; path: string; split: boolean; wrap: boolean }) {
  const rows = useMemo(() => diffRows(patch), [patch]);
  const paired = useMemo(() => split ? splitRows(rows) : [], [rows, split]);
  const tone = (row?: DiffRow) => row?.kind === "add" ? "diff-add" : row?.kind === "delete" ? "diff-delete" : "";
  if (patch.binary) return <p className="p-5 text-sm text-faint">Binary file changed. A text diff is unavailable.</p>;
  if (!rows.length) return <div className="p-4 text-xs text-faint"><p className="mb-2">No text hunks recorded. Patch details:</p><pre className="overflow-auto whitespace-pre-wrap font-mono">{patch.raw || "No patch content available."}</pre></div>;
  return <div className={`diff-scroll ${wrap ? "diff-wrap" : ""}`}><table aria-label={`Diff for ${path}`} className={`diff-table ${split ? "diff-split" : ""}`}>
    {split && <colgroup><col style={{ width: 40 }} /><col style={{ width: 18 }} /><col /><col style={{ width: 40 }} /><col style={{ width: 18 }} /><col /></colgroup>}
    {split && <thead><tr><th colSpan={3}>Before</th><th colSpan={3}>After</th></tr></thead>}
    <tbody>{split ? paired.map((pair, index) => pair.full ? <tr key={index} className="diff-hunk"><td colSpan={6}>{pair.full.text}</td></tr> : <tr key={index}><td className={`diff-number ${tone(pair.left)}`}>{pair.left?.oldLine}</td><td aria-hidden className={`diff-sign ${tone(pair.left)}`}>{pair.left?.kind === "delete" ? "-" : ""}</td><td className={`diff-code ${tone(pair.left)}`}><span>{pair.left?.text ?? ""}</span></td><td className={`diff-number diff-mid ${tone(pair.right)}`}>{pair.right?.newLine}</td><td aria-hidden className={`diff-sign ${tone(pair.right)}`}>{pair.right?.kind === "add" ? "+" : ""}</td><td className={`diff-code ${tone(pair.right)}`}><span>{pair.right?.text ?? ""}</span></td></tr>) : rows.map((row, index) => row.kind === "hunk" || row.kind === "meta" ? <tr key={index} className="diff-hunk"><td colSpan={4}>{row.text}</td></tr> : <tr key={index} className={tone(row)}><td className="diff-number">{row.oldLine}</td><td className="diff-number">{row.newLine}</td><td aria-hidden className="diff-sign">{row.kind === "add" ? "+" : row.kind === "delete" ? "-" : ""}</td><td className="diff-code"><span>{row.text}</span></td></tr>)}</tbody>
  </table></div>;
}

export function DiffFile({ file, split = false, wrap = false }: { file: ChangedFile; split?: boolean; wrap?: boolean }) {
  const [open, setOpen] = useState(true);
  const basename = file.path.split("/").pop() || file.path;
  const directory = file.path.slice(0, -basename.length);
  return <section className="diff-file" aria-label={`Changes to ${file.path}`}>
    <div className="diff-file-header"><button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${file.path}`} className="flex min-w-0 flex-1 items-center gap-2 py-2.5 text-left"><ChevronRight className={`size-3.5 shrink-0 text-faint ${open ? "rotate-90" : ""}`} /><FileCode2 className="size-4 shrink-0 text-faint" /><span className="min-w-0 truncate text-xs" title={file.path}><span className="text-faint">{directory}</span><span className="font-medium">{basename}</span></span></button><span className="diff-kind" title={file.kind}>{({ add: "A", delete: "D", rename: "R", update: "M" }[file.kind] ?? "M")}</span><ChangeCounts files={[file]} /><CopyButton text={file.patches.map(patch => patch.raw).join("\n")} label="Copy diff" /></div>
    {open && <>{file.oldPath && <p className="border-b border-line px-4 py-2 text-xs text-faint">{file.oldPath} → {file.path}</p>}{file.patches.map((patch, index) => <Fragment key={index}>{file.patches.length > 1 && <div className="border-b border-t border-line bg-code px-4 py-2 text-[11px] text-faint">Edit {index + 1}</div>}<DiffTable patch={patch} path={file.path} split={split} wrap={wrap} /></Fragment>)}</>}
  </section>;
}
