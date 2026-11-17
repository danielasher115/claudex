import { useEffect, useState } from "react";
import { ChevronUp, Terminal } from "lucide-react";
import { api } from "../api";
import "./background-shells.css";

interface Shell { processId: string; itemId: string; command: string; cwd: string; osPid?: number | null; cpuPercent?: number | null; rssKb?: number | null }
interface Shells { status: string; items: Shell[]; scope?: string; message?: string; partial?: boolean }

export function BackgroundShells({ sessionId, enabled = true }: { sessionId: string; enabled?: boolean }) {
  const [result, setResult] = useState<{ sessionId: string; data: Shells } | null>(null);
  useEffect(() => {
    setResult(null);
    if (!enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const data = await api<Shells>(`/api/sessions/${encodeURIComponent(sessionId)}/background-shells`, undefined, controller.signal);
        if (!controller.signal.aborted) setResult({ sessionId, data });
      } catch {
        if (!controller.signal.aborted) setResult(null);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [sessionId, enabled]);
  const data = result?.sessionId === sessionId ? result.data : null;
  if (!enabled || data?.status !== "available" || !data.items.length) return null;
  const count = data.items.length;
  return <details className="background-shells group text-xs">
    <summary className="flex cursor-pointer list-none items-center gap-2 rounded-t-2xl px-3 py-2 text-muted outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-faint">
      <Terminal className="size-3.5 shrink-0" />
      <span className="min-w-0 flex-1">{count}{data.partial ? "+" : ""} background shell{count === 1 && !data.partial ? "" : "s"}</span>
      <ChevronUp className="size-3.5 shrink-0 transition-transform group-open:rotate-180 motion-reduce:transition-none" />
    </summary>
    <div className="max-h-64 space-y-3 overflow-auto border-t border-line px-3 py-2">
      <p className="text-[11px] leading-relaxed text-faint">{data.scope ?? "Background terminals reported by the connected Codex runtime for this session."}</p>
      {data.message && <p className="text-[11px] text-faint">{data.message}</p>}
      {data.items.map(shell => <div key={shell.processId} className="space-y-1.5">
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-ink">{shell.command}</pre>
        <p className="break-all font-mono text-[10px] text-faint">{shell.cwd}</p>
        <p className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-faint">
          <span>Running</span><span>Shell {shell.processId}</span>
          {shell.osPid != null && <span>PID {shell.osPid}</span>}
          {shell.cpuPercent != null && <span>CPU {shell.cpuPercent.toLocaleString(undefined, { maximumFractionDigits: 1 })}%</span>}
          {shell.rssKb != null && <span>{(shell.rssKb / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} MB</span>}
        </p>
      </div>)}
    </div>
  </details>;
}
