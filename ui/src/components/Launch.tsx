import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, GitBranch, Paperclip, SquareTerminal } from "lucide-react";
import { api, type Session } from "../api";
import { AttachmentFeedback, AttachmentList, AttachmentPicker, useAttachments } from "./Attachments";
import { Dialog } from "./shared";
import { Select } from "./Select";
import { useModels } from "./Models";

export function Launch({ parent, close, launched }: { parent?: Session; close: () => void; launched: (id: string) => void }) {
  const [prompt, setPrompt] = useState(() => localStorage.getItem("launch-prompt") ?? "");
  const [name, setName] = useState("");
  const [cwd, setCwd] = useState(parent?.cwd ?? "");
  useEffect(() => {
    if (parent?.cwd) return;
    const controller = new AbortController();
    api<{ default_cwd: string }>("/api/setup", undefined, controller.signal)
      .then(setup => { if (!controller.signal.aborted) setCwd(value => value || setup.default_cwd || ""); })
      .catch(() => {});
    return () => controller.abort();
  }, [parent?.cwd]);
  const [model, setModel] = useState("");
  const [sandbox, setSandbox] = useState(parent?.sandbox ?? (parent ? "read-only" : "workspace-write"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [inheritContext, setInheritContext] = useState(true);
  const [sessionSummary, setSessionSummary] = useState("");
  const attachments = useAttachments("launch-attachments");
  const { catalog, error: modelError } = useModels();
  const [effort, setEffort] = useState("high");
  useEffect(() => { if (catalog && !model) setModel(catalog.default_model); }, [catalog, model]);
  const pending = useRef<{ fingerprint: string; id: string } | null>((() => { try { return JSON.parse(localStorage.getItem("launch-request") ?? "null"); } catch { return null; } })());
  useEffect(() => { localStorage.setItem("launch-prompt", prompt); }, [prompt]);
  const start = async () => {
    setBusy(true); setError("");
    try {
      const payload = { prompt, name: name || undefined, cwd, model: model || undefined, effort, sandbox, parent_id: parent?.id, attachments: attachments.files.map(a => a.id), inherit_context: inheritContext, session_summary: sessionSummary || undefined };
      const fingerprint = JSON.stringify(payload);
      if (pending.current?.fingerprint !== fingerprint) { pending.current = { fingerprint, id: crypto.randomUUID() }; localStorage.setItem("launch-request", JSON.stringify(pending.current)); }
      const result = await api<{ thread_id: string; status: string }>("/api/agents", { ...payload, request_id: pending.current!.id });
      if (result.status === "unconfirmed") throw new Error("The launch was not confirmed. Check the session list before starting this task again.");
      localStorage.removeItem("launch-prompt"); localStorage.removeItem("launch-request"); attachments.setFiles([]); launched(result.thread_id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <Dialog title={parent ? "Launch subagent" : "Launch Codex agent"} close={close} canClose={!busy}><form className="space-y-4 p-6" onSubmit={e => { e.preventDefault(); void start(); }}>
    {parent && <div className="flex items-center gap-2 rounded-lg border border-line bg-code p-3 text-xs text-muted"><GitBranch className="size-4 shrink-0" /><span className="truncate">Parent: {parent.title}</span></div>}
    <label className="block text-xs font-medium">Task<textarea autoFocus required value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="Describe the work and the result you need..." rows={5} className="mt-2 w-full resize-y rounded-lg border border-line bg-code p-3 text-sm font-normal outline-none focus:border-faint" /></label>
    <label className="block text-xs font-medium">Session name <span className="font-normal text-faint">(optional)</span><input value={name} onChange={e => setName(e.target.value)} className="launch-input" placeholder="A short name for this agent" /></label>
    <label className="block text-xs font-medium">Workspace<input required value={cwd} onChange={e => setCwd(e.target.value)} className="launch-input font-mono" placeholder="/absolute/path/to/project" /></label>
    <div className="grid grid-cols-2 gap-3"><div className="text-xs font-medium"><div className="mb-2">Model</div><Select label="Model" value={model} onChange={next => { setModel(next); setEffort(catalog?.models.find(m => m.id === next)?.default_effort ?? "high"); }} className="w-full !py-2" disabled={!catalog} options={catalog?.models.map(m => ({ value: m.id, label: `${m.name}${m.is_default ? " (default)" : ""}` })) ?? [{ value: "", label: "Loading models..." }]} /></div><div className="text-xs font-medium"><div className="mb-2">Reasoning effort</div><Select label="Reasoning effort" value={effort} onChange={setEffort} className="w-full !py-2" options={(catalog?.models.find(m => m.id === model)?.efforts ?? ["high"]).map(e => ({ value: e, label: e }))} /></div></div>
    {catalog?.models.find(m => m.id === model) && <p className="-mt-1 text-[11px] text-faint">{catalog.models.find(m => m.id === model)?.claude_comparison} · Approximate task role</p>}{modelError && <p className="text-xs text-removed-ink">{modelError}</p>}
    <div className="text-xs font-medium"><div className="mb-2">Sandbox</div><Select label="Sandbox" value={sandbox} onChange={setSandbox} className="w-full !py-2" options={[{ value: "read-only", label: "Read only" }, { value: "workspace-write", label: "Workspace write" }, ...(!parent ? [{ value: "danger-full-access", label: "Full access" }] : [])]} /></div>
    <div className="rounded-lg border border-dashed border-line p-3" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void attachments.upload(e.dataTransfer.files); }}><div className="flex items-center gap-2 text-xs text-muted"><Paperclip className="size-3.5" /><span className="flex-1">Documents, images, code or transcript summaries</span><AttachmentPicker attachments={attachments} /></div><AttachmentList files={attachments.files} remove={id => attachments.setFiles(current => current.filter(a => a.id !== id))} /><AttachmentFeedback attachments={attachments} /></div>
    <p className="text-[11px] leading-relaxed text-faint">Every file is prepared before launch. The agent gets native delegation tools, inherited attachment context and parent/child tracking.</p>
    <label className="flex items-start gap-2 text-xs text-muted"><input className="mt-0.5 accent-ink" type="checkbox" checked={inheritContext} onChange={e => setInheritContext(e.target.checked)} /><span>Inherit instructions, skills, saved memory and compatible settings<span className="mt-1 block text-[10px] text-faint">Children inherit the same bundle. Claude-only hooks and permissions are reported rather than silently translated.</span></span></label>
    <details className="text-xs text-faint"><summary>Current session context (optional)</summary><textarea className="launch-input" rows={3} value={sessionSummary} onChange={e => setSessionSummary(e.target.value)} placeholder="Goals, decisions and constraints from your current conversation..." /><p className="mt-1 text-[10px]">Saved rules and memory are inherited automatically. Claude MCP launches also export their caller history through the context hook; dashboard launches can supply a handoff here.</p></details>
    {error && <p role="alert" className="text-xs text-removed-ink">{error}</p>}
    <div className="flex justify-end gap-2 pt-1"><button type="button" disabled={busy} className="secondary-button" onClick={close}>Cancel</button><button disabled={busy || attachments.uploading > 0 || !prompt.trim()} type="submit" className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-xs font-medium text-on-accent disabled:opacity-40"><SquareTerminal className="size-3.5" />{busy ? "Launching..." : parent ? "Launch subagent" : "Launch agent"}<ArrowUpRight className="size-3.5" /></button></div>
  </form></Dialog>;
}
