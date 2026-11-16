import { useEffect, useRef, useState } from "react";
import { ChevronRight, Gauge, RefreshCw, RotateCcw } from "lucide-react";
import { api } from "../api";
import { Dialog, Loading } from "./shared";

interface Window { used_percent: number; window_minutes: number | null; resets_at: number | null }
interface Usage {
  status: "available" | "unavailable"; plan_type: string | null; checked_at: number; message?: string;
  limits: { id: string; name: string; primary: Window | null; secondary: Window | null; credits: { has_credits: boolean; unlimited: boolean; balance: string | null } | null }[];
  reset_credits: { available_count: number; credits: { id: string; title: string | null; expires_at: number | null }[] | null } | null;
}
const attemptKey = "codex-reset-attempt";
const remainingPercent = (used: number) => Math.round(100 - Math.min(100, Math.max(0, used)));
const outcomes: Record<string, string> = {
  reset: "Your eligible Codex usage limits were reset.", alreadyRedeemed: "This reset was already applied. Your usage is refreshed.",
  nothingToReset: "There are no eligible usage limits to reset right now.", noCredit: "No earned reset credits are available.",
};
const date = (at: number) => new Date(at * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
function windowName(minutes: number | null) {
  if (minutes === 10080) return "Weekly allowance";
  if (minutes === 1440) return "Daily allowance";
  if (minutes === null) return "Usage allowance";
  return minutes % 60 === 0 ? `${minutes / 60}-hour allowance` : `${minutes}-minute allowance`;
}
function planName(plan: string | null) {
  return plan === "self_serve_business_prolite" ? "Business Pro Lite" : plan?.replaceAll("_", " ") ?? "Codex";
}

export function PlanUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const controller = useRef<AbortController | null>(null);
  const sending = useRef(false);
  const version = useRef(0);
  const refresh = async (force = false) => {
    const signal = controller.current?.signal;
    const current = ++version.current;
    setRefreshing(true);
    try {
      const data = await api<Usage>(`/api/account/usage${force ? "?refresh=1" : ""}`, undefined, signal);
      if (!signal?.aborted && current === version.current) setUsage(data);
    } catch {
      if (!signal?.aborted && current === version.current) setUsage({ status: "unavailable", plan_type: null, checked_at: Date.now() / 1000, limits: [], reset_credits: null, message: "Plan usage could not be checked. Try refreshing." });
    } finally { if (!signal?.aborted && current === version.current) setRefreshing(false); }
  };
  useEffect(() => {
    controller.current = new AbortController();
    void refresh();
    const check = () => { if (document.visibilityState === "visible" && !sending.current) void refresh(); };
    const timer = setInterval(check, 60000);
    window.addEventListener("focus", check);
    return () => { controller.current?.abort(); clearInterval(timer); window.removeEventListener("focus", check); };
  }, []);
  const redeem = async () => {
    if (sending.current) return;
    sending.current = true; version.current++; setRefreshing(false); setBusy(true); setError("");
    try {
      const key = sessionStorage.getItem(attemptKey) ?? crypto.randomUUID();
      sessionStorage.setItem(attemptKey, key);
      const result = await api<{ outcome: string; usage: Usage }>("/api/account/usage/reset", { idempotency_key: key }, controller.current?.signal);
      if (!outcomes[result.outcome]) throw new Error("The reset outcome is unknown. Refresh usage before retrying.");
      sessionStorage.removeItem(attemptKey);
      setUsage(result.usage); setNotice(outcomes[result.outcome]); setConfirm(false);
    } catch (cause) { if (!controller.current?.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not use the reset. Retry the same attempt."); }
    finally { sending.current = false; if (!controller.current?.signal.aborted) setBusy(false); }
  };
  const available = usage?.status === "available";
  const windows = available ? usage.limits.flatMap(limit => [limit.primary, limit.secondary].filter((window): window is Window => window !== null)) : [];
  const used = windows.length ? Math.min(100, Math.max(0, ...windows.map(window => window.used_percent))) : null;
  const remaining = used === null ? null : remainingPercent(used);
  const resets = available ? usage.reset_credits?.available_count : undefined;
  const summary = !usage ? "Checking usage" : !available ? "Usage unavailable" : remaining === null ? "Limits not reported" : `${remaining}% left`;
  const resetLabel = resets === undefined ? "Resets not reported" : `${resets} reset${resets === 1 ? "" : "s"} available`;
  const close = () => { if (!busy) { setOpen(false); setConfirm(false); setError(""); } };
  return <>
    <button type="button" className="mx-3 mb-2 flex shrink-0 items-center gap-2.5 rounded-xl border border-line px-3 py-2 text-left hover:bg-hover" aria-label={`Codex plan usage: ${remaining === null ? available ? "limits not reported" : usage ? "unavailable" : "loading" : `${remaining}% remaining`}`} onClick={() => setOpen(true)} title="Codex account usage and earned resets">
      <Gauge className={`size-4 shrink-0 ${used !== null && used >= 90 ? "text-amber-600 dark:text-amber-400" : "text-faint"}`} />
      <div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2 text-[11px]"><span className="text-muted">Plan usage</span><span className="font-medium tabular-nums">{summary}</span></div><div className="mt-1 flex items-center gap-2"><span aria-hidden className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-line">{used !== null && <span className={`block h-full rounded-full ${used >= 90 ? "bg-amber-500" : "bg-faint"}`} style={{ width: `${remaining}%` }} />}</span><span className="shrink-0 text-[10px] text-faint">{resets && resets > 0 ? `${resets} reset${resets === 1 ? "" : "s"}` : "View limits"}</span></div></div><ChevronRight className="size-3 shrink-0 text-faint" />
    </button>
    {open && <Dialog title={confirm ? "Use a rate-limit reset?" : "Codex plan usage"} subtitle={confirm ? undefined : `${planName(usage?.plan_type ?? null)} plan`} close={close} canClose={!busy} className="!max-w-md"><div className="space-y-5 p-6">
      {confirm ? <>
        <div className="flex size-10 items-center justify-center rounded-xl border border-line bg-code"><RotateCcw className="size-5 text-muted" /></div>
        <p className="text-sm leading-relaxed text-muted">Use one earned reset to restore eligible Codex usage windows. Codex decides which windows can reset.</p><p className="text-xs text-faint">{resetLabel}. This spends one credit if a reset applies.</p>
        {error && <p role="alert" className="text-xs leading-relaxed text-removed-ink">{error}</p>}
        <div className="flex justify-end gap-2"><button className="secondary-button" disabled={busy} onClick={() => { setConfirm(false); setError(""); }}>Back</button><button className="secondary-button !bg-ink !text-surface" disabled={busy} onClick={() => void redeem()}>{busy ? <Loading label="Applying reset" /> : "Use one reset"}</button></div>
      </> : <>
        <div className="flex items-center justify-between"><span className="text-xs text-faint">Shared across your Codex sessions</span><button className="icon-button" aria-label="Refresh plan usage" disabled={refreshing} onClick={() => void refresh(true)}><RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} /></button></div>
        {!usage ? <Loading label="Checking plan usage" /> : !available ? <p role="status" className="text-sm text-muted">{usage.message ?? "Plan usage is unavailable for this account or runtime."}</p> : <>
          {usage.limits.map(limit => <section key={limit.id} className="space-y-4"><h3 className="text-sm font-medium">{limit.name}</h3>
            {[limit.primary, limit.secondary].filter((window): window is Window => window !== null).map((window, index) => <div key={index}><div className="mb-2 flex items-center justify-between gap-2 text-xs"><span className="text-muted">{windowName(window.window_minutes)}</span><span className="font-medium tabular-nums">{remainingPercent(window.used_percent)}% remaining</span></div>
              <div role="progressbar" aria-label={`${limit.name} ${windowName(window.window_minutes)}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={remainingPercent(window.used_percent)} aria-valuetext={`${remainingPercent(window.used_percent)}% remaining`} className="h-1.5 overflow-hidden rounded-full bg-line"><div className={`h-full rounded-full ${window.used_percent >= 90 ? "bg-amber-500" : "bg-ink/70"}`} style={{ width: `${remainingPercent(window.used_percent)}%` }} /></div>
              <p className="mt-2 text-[11px] text-faint">{window.resets_at === null ? "Reset time not reported" : window.resets_at * 1000 <= Date.now() ? `Reset was due ${date(window.resets_at)}. Refresh for current usage.` : `Resets ${date(window.resets_at)}`}</p></div>)}
            {limit.credits && <p className="text-xs text-faint">{limit.credits.unlimited ? "Unlimited credits" : limit.credits.balance !== null ? `${limit.credits.balance} credits remaining` : limit.credits.has_credits ? "Additional credits available" : "No additional credits available"}</p>}
          </section>)}
          {!usage.limits.length && <p className="text-xs text-faint">No usage windows were reported.</p>}
          <section className="border-t border-line pt-4"><div className="flex items-center gap-2"><RotateCcw className="size-3.5 text-faint" /><h3 className="text-xs font-medium">Earned resets</h3></div><div className="mt-2 flex items-center justify-between gap-3"><p className="text-xs text-muted">{resets === undefined ? "Reset availability not reported" : resets === 0 ? "No resets available" : resetLabel}</p>{resets !== undefined && resets > 0 && <button className="secondary-button !py-1.5" onClick={() => { setNotice(""); setConfirm(true); }}>Use a reset</button>}</div>{usage.reset_credits?.credits?.filter(credit => credit.expires_at !== null).slice(0,3).map(credit => <p key={credit.id} className="mt-2 text-[11px] text-faint">{credit.title ?? "Reset credit"}: expires {date(credit.expires_at!)}</p>)}</section>
        </>}
        {notice && <p role="status" className="rounded-lg border border-line bg-code p-3 text-xs leading-relaxed text-muted">{notice}</p>}
        {usage?.checked_at && <p className="text-[10px] text-faint">Checked {date(usage.checked_at)} · Updates every minute</p>}
      </>}
    </div></Dialog>}
  </>;
}
