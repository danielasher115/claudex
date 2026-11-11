import { useEffect, useState } from "react";
import { Check, Cpu, RefreshCw } from "lucide-react";
import { api } from "../api";
import { Dialog, Loading } from "./shared";

export interface Model { id: string; name: string; description: string; is_default: boolean; efforts: string[]; default_effort: string; modalities: string[]; tier: string; claude_comparison: string; legacy: boolean }
export interface ModelCatalog { default_model: string; models: Model[]; claude_aliases: Record<string, string>; comparison_note: string; sources: string[] }

export function useModels() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [error, setError] = useState("");
  const refresh = () => api<ModelCatalog>("/api/models?refresh=1").then(data => { setCatalog(data); setError(""); }).catch(e => setError(e.message));
  useEffect(() => { let stopped = false; api<ModelCatalog>("/api/models").then(data => { if (!stopped) setCatalog(data); }).catch(e => { if (!stopped) setError(e.message); }); return () => { stopped = true; }; }, []);
  return { catalog, error, refresh };
}

export function Models({ close }: { close: () => void }) {
  const { catalog, error, refresh } = useModels();
  return <Dialog title="Available Codex models" close={close}><div className="space-y-4 p-6"><div className="flex items-center justify-between gap-3"><p className="text-xs text-muted">Live catalog from your local Codex runtime.</p><button className="icon-button" aria-label="Refresh models" onClick={() => void refresh()}><RefreshCw className="size-3.5" /></button></div>
    {error && <p role="alert" className="text-xs text-removed-ink">{error}</p>}{!catalog && !error && <Loading label="Loading available models" />}
    {catalog?.models.map(model => <div key={model.id} className="rounded-xl border border-line bg-panel p-4"><div className="flex items-center gap-2"><Cpu className="size-4 text-faint" /><span className="text-sm font-semibold">{model.name}</span>{model.is_default && <span className="ml-auto flex items-center gap-1 rounded bg-hover px-2 py-1 text-[10px]"><Check className="size-3" />Default</span>}{model.legacy && <span className="ml-auto text-[10px] text-faint">Previous generation</span>}</div><p className="mt-2 text-xs text-muted">{model.description}</p><p className="mt-2 text-[11px] text-faint">{model.claude_comparison}</p><div className="mt-3 flex flex-wrap gap-1.5"><code className="rounded bg-code px-1.5 py-1 text-[10px]">{model.id}</code><span className="rounded bg-code px-1.5 py-1 text-[10px] text-faint">{model.modalities.join(" + ")}</span><span className="rounded bg-code px-1.5 py-1 text-[10px] text-faint">Effort: {model.efforts.join(", ")}</span></div></div>)}
    {catalog && <><div className="rounded-lg border border-line bg-code p-3"><p className="text-xs font-medium">For Claude Code callers</p><div className="mt-2 space-y-1">{Object.entries(catalog.claude_aliases).map(([alias, target]) => <div key={alias} className="flex justify-between gap-3 text-[11px]"><code>{alias}</code><code className="text-faint">{target}</code></div>)}</div><p className="mt-3 text-[11px] leading-relaxed text-faint">{catalog.comparison_note}</p></div><div className="flex flex-wrap gap-3 text-[10px] text-faint"><a href={catalog.sources[0]} target="_blank" rel="noreferrer" className="underline">OpenAI model catalog</a><a href={catalog.sources[2]} target="_blank" rel="noreferrer" className="underline">Claude model guide</a></div></>}
  </div></Dialog>;
}
