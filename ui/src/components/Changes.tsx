import { useMemo, useState } from "react";
import { FileCode2, FileDiff, Search } from "lucide-react";
import type { RunChanges } from "../changes";
import { ChangeCounts, DiffFile } from "./Diff";
import { CopyButton, Dialog } from "./shared";
import { Select } from "./Select";

export function Changes({ runs, initialRun, close }: { runs: RunChanges[]; initialRun?: string; close: () => void }) {
  const [runId, setRunId] = useState(initialRun || runs.at(-1)?.id || "");
  const [selected, setSelected] = useState("");
  const [filter, setFilter] = useState("");
  const [split, setSplit] = useState(false);
  const [wrap, setWrap] = useState(false);
  const run = runs.find(value => value.id === runId) ?? runs.at(-1);
  const files = useMemo(() => (run?.files ?? []).filter(file => file.path.toLowerCase().includes(filter.toLowerCase())), [run, filter]);
  const file = files.find(value => value.path === selected) ?? files[0];
  const patch = run?.files.flatMap(value => value.patches.map(part => part.raw)).join("\n") ?? "";
  return <Dialog title="Review changes" close={close} className="changes-dialog" actions={patch ? <CopyButton text={patch} label="Copy run diff" /> : undefined}>
    <div className="changes-toolbar"><div className="min-w-0 flex-1"><Select label="Run" value={run?.id ?? ""} onChange={value => { setRunId(value); setSelected(""); setFilter(""); }} options={runs.map((value, index) => ({ value: value.id, label: `Run ${index + 1}: ${value.prompt || value.id.slice(0, 12)}` }))} /></div><div className="segmented !w-auto !p-0.5"><button className={split ? "" : "selected"} aria-pressed={!split} onClick={() => setSplit(false)}>Unified</button><button className={split ? "selected" : ""} aria-pressed={split} onClick={() => setSplit(true)}>Split</button></div><label className="flex shrink-0 cursor-pointer items-center gap-2 text-xs text-faint"><input type="checkbox" checked={wrap} onChange={event => setWrap(event.target.checked)} />Wrap lines</label></div>
    {run?.files.length ? <><div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-5 py-2.5 text-xs"><span className="font-medium">{run.files.length} file{run.files.length === 1 ? "" : "s"} changed</span><ChangeCounts files={run.files} /><span className="ml-auto text-[11px] text-faint" title={run.source === "recorded" ? "Each successful edit is preserved. Counts include repeated edits." : "The complete diff reported by Codex for this run."}>{run.source === "native" ? "Run diff" : "Recorded edits"}</span></div><div className="changes-layout"><nav aria-label="Changed files" className="changes-files"><label className="m-3 flex items-center gap-2 rounded-lg border border-line bg-surface px-2 py-2"><Search className="size-3.5 shrink-0 text-faint" /><input aria-label="Filter changed files" value={filter} onChange={event => setFilter(event.target.value)} placeholder="Filter files..." className="w-full min-w-0 bg-transparent text-xs outline-none" /></label><div className="px-2 pb-3">{files.map(value => <button type="button" key={value.path} aria-label={value.path} aria-current={file?.path === value.path ? "true" : undefined} onClick={() => setSelected(value.path)} className={`changes-file-button ${file?.path === value.path ? "bg-selected" : "hover:bg-hover"}`}><FileCode2 className="size-3.5 shrink-0 text-faint" /><span title={value.path} className="min-w-0 flex-1 text-left text-xs"><span className="block truncate font-medium">{value.path.split("/").pop()}</span>{value.path.includes("/") && <span className="mt-1 block truncate text-[10px] text-faint">{value.path.slice(0, value.path.lastIndexOf("/"))}</span>}</span><ChangeCounts files={[value]} /></button>)}{!files.length && <p className="px-2 py-4 text-xs text-faint">No matching files</p>}</div></nav><div className="changes-content">{file && <DiffFile key={`${run.id}:${file.path}`} file={file} split={split} wrap={wrap} />}</div></div></> : <div className="flex flex-1 flex-col items-center justify-center px-6 py-16 text-center"><FileDiff className="mb-4 size-7 text-faint" /><p className="text-sm font-medium">No file changes recorded for this run</p><p className="mt-2 max-w-md text-xs leading-relaxed text-faint">Edits appear here when Codex reports a patch. Changes made through tools that do not report patches may not appear.</p></div>}
  </Dialog>;
}
