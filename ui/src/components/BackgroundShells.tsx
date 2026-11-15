import { useEffect, useState } from "react";
import { ChevronRight, Terminal } from "lucide-react";
import { api } from "../api";

interface Shell { processId: string; itemId: string; command: string; cwd: string; osPid?: number | null; cpuPercent?: number | null; rssKb?: number | null }
interface Shells { status: string; items: Shell[]; scope?: string; message?: string; partial?: boolean }

export function BackgroundShells({ sessionId }: { sessionId: string }) {
  const [result, setResult] = useState<{ sessionId: string; data: Shells } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const data = await api<Shells>(`/api/sessions/${encodeURIComponent(sessionId)}/background-shells`, undefined, controller.signal);
        if (!controller.signal.aborted) setResult({ sessionId, data });
      } catch {
        if (!controller.signal.aborted) setResult({ sessionId, data: { status: "unavailable", items: [], message: "Background shells could not be checked. Retrying automatically." } });
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [sessionId]);
  const data = result?.sessionId === sessionId ? result.data : null;
  if (!data || data.status === "available" && !data.items.length && !data.partial) return null;
  const available = data.status === "available";
  const count = available ? data.items.length : 0;
  return <details className="group mb-2 rounded-lg border border-line bg-code/50 text-xs">
    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-muted">
      <Terminal className="size-3.5 shrink-0" />
      <span className="min-w-0 flex-1">{available ? `${count}${data.partial ? "+" : ""} background shell${count === 1 && !data.partial ? "" : "s"}` : "Background shells unavailable"}</span>
      <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
    </summary>
    <div className="max-h-64 space-y-3 overflow-auto border-t border-line px-3 py-2">
      <p className="text-[11px] leading-relaxed text-faint">{data.scope ?? "Background terminals reported by the connected Codex runtime for this session."}</p>
      {data.message && <p className="text-[11px] text-faint">{data.message}</p>}
      {available && data.items.map(shell => <div key={shell.processId} className="space-y-1.5">
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
