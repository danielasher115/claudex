import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { Brain, Check, ChevronRight, ClipboardList, Clock3, FileText, GitBranch, Globe, Image as ImageIcon, Layers, Search, ShieldCheck } from "lucide-react";
import { reasoningOf, textOf, type Artifact, type Item } from "../api";
import { AttachmentList } from "./Attachments";
import { CopyButton } from "./shared";

const Markdown = lazy(() => import("./Markdown").then(module => ({ default: module.Markdown })));
export const CODEX_ITEMS = new Set(["reasoning", "plan", "imageView", "imageGeneration", "webSearch", "collabAgentToolCall", "subAgentActivity", "hookPrompt", "sleep", "enteredReviewMode", "exitedReviewMode", "contextCompaction"]);

function Body({ text }: { text: string }) {
  return <Suspense fallback={<p className="whitespace-pre-wrap text-sm">{text}</p>}><Markdown text={text} /></Suspense>;
}
function urlOf(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; } catch { return null; }
}
function Notice({ icon: Icon, children }: { icon: typeof Clock3; children: ReactNode }) {
  return <div className="my-4 flex items-center gap-2 text-xs text-faint"><Icon className="size-3.5 shrink-0" /><span>{children}</span></div>;
}

export function CodexItem({ item, record, matched, expanded, select, live = false }: { item: Item; record: Record<string, unknown>; matched: boolean; expanded?: boolean; select?: (id: string) => void; live?: boolean }) {
  const type = String(record.type);
  const [open, setOpen] = useState(false);
  const manual = useRef(false);
  useEffect(() => { if (expanded !== undefined) { manual.current = false; setOpen(expanded); } }, [expanded]);
  useEffect(() => { if (matched) setOpen(true); }, [matched]);
  useEffect(() => {
    if (manual.current || matched || expanded !== undefined) return;
    if (type === "reasoning") setOpen(live);
    else if (["webSearch", "collabAgentToolCall"].includes(type) && typeof record.status === "string") setOpen(record.status === "inProgress" || record.status === "running");
  }, [live, expanded, matched, type, record.status]);
  const images = Array.isArray(item.images) ? (item.images as Artifact[]).map(file => ({ ...file, source_path: file.source_path || (typeof record.path === "string" ? record.path : typeof record.savedPath === "string" ? record.savedPath : undefined) })) : [];
  let body: ReactNode;
  switch (type) {
    case "reasoning": {
      const reasoning = reasoningOf(record);
      if (!reasoning.content) {
        if (!reasoning.heading && !live) return null;
        body = <div className="codex-reasoning flex items-center gap-2 py-2 text-xs text-faint"><Brain className={`size-3.5 shrink-0 ${live ? "animate-pulse" : ""}`} /><span>{reasoning.heading || "Thinking"}</span></div>;
        break;
      }
      body = <div className="codex-reasoning"><button type="button" aria-expanded={open} onClick={() => { manual.current = true; setOpen(!open); }} className="flex w-full items-center gap-2 py-2 text-left text-xs text-faint hover:text-muted"><ChevronRight className={`size-3 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} /><Brain className="size-3.5 shrink-0" /><span className="truncate font-medium">{reasoning.heading || "Thinking"}</span>{!reasoning.heading && <span className="min-w-0 truncate text-faint">{reasoning.content.split("\n")[0]}</span>}</button>{open && <div className="reasoning-content border-l border-line pl-5"><div className="float-right"><CopyButton text={reasoning.content} label="Copy reasoning summary" /></div><Body text={reasoning.content} /></div>}</div>;
      break;
    }
    case "plan":
      body = <section aria-label="Agent plan" className="my-4 rounded-xl border border-line bg-panel p-4"><h3 className="mb-3 flex items-center gap-2 text-xs font-semibold"><ClipboardList className="size-4 text-faint" />Plan</h3><Body text={textOf(record.text)} /></section>; break;
    case "imageView":
    case "imageGeneration":
      body = <section aria-label={type === "imageView" ? "Viewed image" : "Generated image"} className="my-4"><div className="mb-2 flex items-center gap-2 text-xs text-faint"><ImageIcon className="size-4 shrink-0" /><span>{type === "imageView" ? "Viewed image" : record.status === "failed" ? "Image generation failed" : "Generated image"}</span><span className="min-w-0 truncate font-mono text-[10px]" title={String(record.path ?? record.savedPath ?? "")}>{String(record.path ?? record.savedPath ?? "").split(/[\\/]/).pop()}</span></div><AttachmentList files={images} gallery />{item.media_error ? <p className="text-xs text-removed-ink">{String(item.media_error)}</p> : !images.length && <p className="rounded-lg border border-line bg-code p-3 text-xs text-faint">{record.status === "inProgress" || record.status === "in_progress" ? "The generated image will appear when it is ready." : "The image bytes are not available in this history."}</p>}{Boolean(record.revisedPrompt) && <details className="mt-2 text-xs text-faint"><summary>Image prompt</summary><p className="mt-2 whitespace-pre-wrap">{String(record.revisedPrompt)}</p></details>}{Boolean(record.failure) && <pre className="tool-output mt-2 text-removed-ink">{JSON.stringify(record.failure, null, 2)}</pre>}</section>; break;
    case "webSearch": {
      const action = record.action && typeof record.action === "object" ? record.action as Record<string, unknown> : {};
      const title = action.type === "openPage" ? "Opened web page" : action.type === "findInPage" ? "Searched within page" : "Searched the web";
      body = <details open={open} onToggle={event => { if (event.currentTarget.open !== open) { manual.current = true; setOpen(event.currentTarget.open); } }} className="tool-card border-line"><summary className="flex items-center gap-2 px-3 py-2 text-xs text-muted"><Globe className="size-4 shrink-0" /><span className="shrink-0 font-medium">{title}</span><span className="min-w-0 truncate text-faint">{String(record.query || action.query || action.url || action.pattern || "")}</span></summary><div className="space-y-3 border-t border-line p-3">{urlOf(action.url) && <a className="block break-all text-xs underline" target="_blank" rel="noreferrer" href={urlOf(action.url)!}>{String(action.url)}</a>}{Boolean(record.query) && <p className="text-xs">{String(record.query)}</p>}{Array.isArray(record.results) && record.results.map((result, index) => { const value = result && typeof result === "object" ? result as Record<string, unknown> : {}; const url = urlOf(value.url ?? value.link); return <div key={index} className="text-xs">{url ? <><a href={url} target="_blank" rel="noreferrer" className="font-medium underline underline-offset-2">{String(value.title || url)}</a>{Boolean(value.snippet) && <p className="mt-1 text-faint">{String(value.snippet)}</p>}</> : <pre className="tool-output">{JSON.stringify(result, null, 2)}</pre>}</div>; })}</div></details>; break;
    }
    case "subAgentActivity":
      body = <Notice icon={GitBranch}><button className="font-medium hover:underline" onClick={() => select?.(String(record.agentThreadId))}>{String(record.agentPath)}</button> {String(record.kind)}{record.model ? <span className="ml-2 text-[10px]">{String(record.model)} · {String(record.reasoningEffort ?? "")}</span> : null}</Notice>; break;
    case "collabAgentToolCall": {
      const agents = record.agentsStates && typeof record.agentsStates === "object" ? record.agentsStates as Record<string, { status?: string; message?: string }> : {};
      const receivers = Array.isArray(record.receiverThreadIds) ? record.receiverThreadIds as string[] : Object.keys(agents);
      body = <details open={open} onToggle={event => { if (event.currentTarget.open !== open) { manual.current = true; setOpen(event.currentTarget.open); } }} className="tool-card border-line"><summary className="flex items-center gap-2 px-3 py-2 text-xs text-muted"><GitBranch className="size-4" /><span className="font-medium">{String(record.tool ?? "Subagent")}</span><span className="ml-auto text-faint">{String(record.status ?? "")}</span></summary><div className="space-y-3 border-t border-line p-3">{Boolean(record.prompt) && <Body text={String(record.prompt)} />}{receivers.map(id => <div key={id} className="rounded-lg bg-code p-3 text-xs"><button className="font-mono text-[11px] hover:underline" onClick={() => select?.(id)}>Open agent {id.slice(0, 12)}</button><span className="ml-3 text-faint">{agents[id]?.status ?? "Unknown state"}</span>{agents[id]?.message && <p className="mt-2 whitespace-pre-wrap text-faint">{agents[id].message}</p>}</div>)}</div></details>; break;
    }
    case "hookPrompt":
      body = <details open={open} onToggle={event => { if (event.currentTarget.open !== open) { manual.current = true; setOpen(event.currentTarget.open); } }} className="my-3 rounded-lg border border-line bg-code px-3 py-2 text-xs text-faint"><summary className="flex items-center gap-2"><FileText className="size-3.5" />Hook context</summary><div className="mt-3"><Body text={textOf(record.fragments)} /></div></details>; break;
    case "contextCompaction": body = <Notice icon={Layers}>Context compacted</Notice>; break;
    case "sleep": body = <Notice icon={Clock3}>Paused for {typeof record.durationMs === "number" ? `${(record.durationMs / 1000).toFixed(1)}s` : "the requested interval"}</Notice>; break;
    case "enteredReviewMode": body = <Notice icon={Search}>Review started{record.review ? `: ${String(record.review)}` : ""}</Notice>; break;
    case "exitedReviewMode": body = <Notice icon={ShieldCheck}>Review completed{record.review ? `: ${String(record.review)}` : ""}</Notice>; break;
    default: body = <Notice icon={Check}>Recorded Codex event</Notice>;
  }
  return <div data-search-match={matched || undefined} data-item-id={item.id} data-codex-type={type} className={`group min-w-0 ${matched ? "search-match" : ""}`}>{body}</div>;
}
