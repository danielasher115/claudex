import { lazy, memo, Suspense, useEffect, useRef, useState } from "react";
import { Check, ChevronRight, FilePenLine, SquareTerminal, Wrench, X } from "lucide-react";
import { textOf, toolOutput, type Artifact, type Item } from "../api";
import { CopyButton } from "./shared";
import { AttachmentList } from "./Attachments";
import { CODEX_ITEMS, CodexItem } from "./CodexItems";
import { diffFiles } from "../changes";
import { DiffFile } from "./Diff";
import { FileReferenceScope } from "./FileLinks";

const Markdown = lazy(() => import("./Markdown").then(module => ({ default: module.Markdown })));

interface ConversationProps { item: Item; matched?: boolean; expanded?: boolean; select?: (id: string) => void; compact?: boolean; live?: boolean; sessionId?: string }
export const ConversationItem = memo(function ConversationItem(props: ConversationProps) {
  return <FileReferenceScope sessionId={props.sessionId} itemId={props.item.id}><ConversationRecord {...props} /></FileReferenceScope>;
});

function ConversationRecord({ item, matched = false, expanded, select, live = false }: ConversationProps) {
  const record = (item.type === "record" ? item.record : item) as Record<string, unknown>;
  const type = String(record.type ?? "unknown");
  const [open, setOpen] = useState(false);
  const [rawOpen, setRawOpen] = useState(false);
  const manual = useRef(false);
  useEffect(() => { if (expanded !== undefined) { manual.current = false; setOpen(expanded); } }, [expanded]);
  useEffect(() => { if (matched) setOpen(true); }, [matched]);
  useEffect(() => { if (!matched && !manual.current && expanded === undefined) setOpen(record.status === "inProgress" || record.status === "running"); }, [record.status, matched, expanded]);
  if (item.type === "turn") {
    const error = item.error && typeof item.error === "object" ? textOf((item.error as Record<string, unknown>).message) : textOf(item.error);
    return <div className="turn-divider"><span className="h-px flex-1 bg-line" /><span>{item.status === "inProgress" ? "Working" : item.status === "completed" ? "Turn completed" : item.status === "interrupted" ? "Turn interrupted" : "Turn"}</span>{error && <span className="text-removed-ink">{error}</span>}<span className="h-px flex-1 bg-line" /></div>;
  }
  const user = type === "userMessage" || (type === "message" && record.role === "user");
  const assistant = type === "agentMessage" || (type === "message" && record.role === "assistant");
  if (user || assistant) {
    const text = textOf(record.text ?? record.content);
    return <article aria-label={user ? "Your message" : "Codex message"} data-search-match={matched || undefined} data-item-id={item.id} className={`message group/message ${user ? "message-user" : "message-assistant"} ${matched ? "search-match" : ""}`}>
      <div className="message-copy opacity-0 group-hover/message:opacity-100 focus-within:opacity-100"><CopyButton text={text} label="Copy message" /></div>
      {text ? <Suspense fallback={<p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>}><Markdown text={text} /></Suspense> : <p className="text-sm text-muted">Attached content</p>}
      {Array.isArray(item.attachments) && <AttachmentList files={item.attachments as Artifact[]} />}
      {Array.isArray(item.images) && <AttachmentList files={item.images as Artifact[]} gallery />}
      {Array.isArray(record.questions) && record.questions.map((question, index) => <div key={index} className="my-3 rounded-xl border border-line bg-code p-3 text-sm"><p className="font-medium">{question.title}</p>{question.options?.length > 0 && <ul className="mt-2 list-inside list-disc text-xs text-faint">{question.options.map((option: string) => <li key={option}>{option}</li>)}</ul>}<p className="mt-2 text-[10px] text-faint">Reply through the conversation to answer.</p></div>)}
    </article>;
  }
  if (CODEX_ITEMS.has(type)) return <CodexItem item={item} record={record} matched={matched} expanded={expanded} select={select} live={live} />;
  const file = type === "fileChange";
  const command = type === "commandExecution" || Boolean(record.command);
  const tool = [record.server ?? record.namespace, record.tool ?? record.name].filter(Boolean).join(".");
  const title = file ? "Edit files" : command ? "Terminal" : type === "functionCallOutput" ? `${tool || "Function"} result` : tool || type.replace(/([A-Z])/g, " $1").replaceAll("_", " ");
  const summary = file && Array.isArray(record.changes) ? record.changes.map(c => c.path).join(", ") : String(record.command ?? (record.arguments ? typeof record.arguments === "string" ? record.arguments : JSON.stringify(record.arguments) : ""));
  const output = open ? toolOutput(record) : "";
  const failed = record.status === "failed" || record.status === "declined" || record.success === false || (typeof record.exitCode === "number" && record.exitCode !== 0);
  const Icon = file ? FilePenLine : command ? SquareTerminal : Wrench;
  const raw = { ...record }; delete raw.encrypted_content;
  return <div data-search-match={matched || undefined} data-item-id={item.id} className={`tool-card ${matched ? "search-match" : ""} ${failed ? "border-removed-ink/40" : "border-line"}`}>
    <button type="button" aria-expanded={open} onClick={() => { manual.current = true; setOpen(!open); }} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] hover:bg-hover">
      <ChevronRight className={`size-3.5 shrink-0 text-faint transition-transform ${open ? "rotate-90" : ""}`} /><Icon className="size-4 shrink-0 text-muted" /><span className="shrink-0 font-medium">{title}</span><span title={summary} className="min-w-0 truncate font-mono text-xs text-faint">{summary}</span>
      <span className="ml-auto flex shrink-0 items-center gap-1.5 text-xs text-faint">{typeof record.durationMs === "number" && record.durationMs > 1000 && `${(record.durationMs / 1000).toFixed(1)}s`}{failed ? <X className="size-3.5 text-removed-ink" /> : record.status === "completed" ? <Check className="size-3.5" /> : record.status === "inProgress" ? <span className="size-1.5 animate-pulse rounded-full bg-emerald-500" /> : null}</span>
    </button>
    {Array.isArray(item.images) && <div className="px-3"><AttachmentList files={item.images as Artifact[]} gallery /></div>}
    {open && <div className="border-t border-line px-3 py-3">
      {command && <div className="mb-3 flex items-start gap-2 rounded-md bg-code px-3 py-2"><pre className="min-w-0 flex-1 overflow-auto whitespace-pre-wrap font-mono text-xs">$ {String(record.command ?? "")}</pre><CopyButton text={String(record.command ?? "")} label="Copy command" /></div>}
      {!command && Boolean(record.arguments) && <details className="mb-3 text-xs text-faint"><summary>Input</summary><pre className="tool-output mt-2">{typeof record.arguments === "string" ? record.arguments : JSON.stringify(record.arguments, null, 2)}</pre></details>}
      {output && <div className="relative pr-5">{command ? <pre className={`tool-output ${failed ? "text-removed-ink" : ""}`}>{output}</pre> : <Suspense fallback={<pre className="tool-output">{output}</pre>}><Markdown text={output} /></Suspense>}<div className="absolute right-0 top-0"><CopyButton text={output} label="Copy output" /></div></div>}
      {file && Array.isArray(record.changes) && record.changes.flatMap(change => diffFiles(String(change.diff ?? ""), String(change.path ?? ""), change.kind)).map((change, i) => <DiffFile key={i} file={change} />)}
      {!output && !file && !Array.isArray(item.images) && <pre className="tool-output text-faint">{record.status === "inProgress" ? "Output will appear as this tool runs." : JSON.stringify(raw, null, 2)}</pre>}
      <button className="mt-3 flex items-center gap-1 text-[11px] text-faint hover:text-ink" onClick={() => setRawOpen(!rawOpen)}><ChevronRight className={`size-3 ${rawOpen ? "rotate-90" : ""}`} />Recorded details</button>
      {rawOpen && <pre className="tool-output mt-2 text-faint">{JSON.stringify(raw, null, 2)}</pre>}
    </div>}
  </div>;
}
