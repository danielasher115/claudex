import { Check, Copy, Loader2, Square, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { label } from "../api";

export function Status({ status, compact = false, phase }: { status: string; compact?: boolean; phase?: string }) {
  if (compact && status !== "active" && ["completed", "failed", "interrupted"].includes(phase ?? "")) {
    const Icon = phase === "failed" ? X : phase === "interrupted" ? Square : Check;
    const title = phase === "failed" ? "Failed" : phase === "interrupted" ? "Stopped" : "Completed";
    return <span role="img" aria-label={title} title={title} className={`inline-flex shrink-0 items-center ${phase === "failed" ? "text-removed-ink" : "text-faint"}`}><Icon className="size-3" /></span>;
  }
  return <span className={`inline-flex shrink-0 items-center gap-1.5 text-xs ${status === "external" || status === "systemError" ? "text-amber-700 dark:text-amber-400" : "text-faint"}`}>
    <span aria-hidden className={status === "active" ? "size-3 animate-spin rounded-full border border-faint border-r-transparent" : `size-1.5 rounded-full ${status === "external" || status === "systemError" ? "bg-amber-500" : "bg-faint/40"}`} />{!compact && label(status)}
  </span>;
}

export function CopyButton({ text, label: name = "Copy", className = "" }: { text: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => { if (copied || error) { const timer = setTimeout(() => { setCopied(false); setError(false); }, 1800); return () => clearTimeout(timer); } }, [copied, error]);
  return <button type="button" title={error ? "Clipboard unavailable" : copied ? "Copied" : name} aria-label={copied ? "Copied" : name} className={`icon-button ${className}`} onClick={() => navigator.clipboard.writeText(text).then(() => setCopied(true), () => setError(true))}>{copied ? <Check className="size-3.5 text-emerald-600" /> : error ? <X className="size-3.5" /> : <Copy className="size-3.5" />}</button>;
}

export function Dialog({ title, subtitle, children, close, canClose = true, actions, className = "" }: { title: string; subtitle?: string; children: ReactNode; close: () => void; canClose?: boolean; actions?: ReactNode; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const descriptionId = useId();
  useEffect(() => { ref.current?.showModal(); return () => ref.current?.close(); }, []);
  return <dialog ref={ref} aria-label={title} aria-describedby={subtitle ? descriptionId : undefined} onCancel={e => { e.preventDefault(); e.stopPropagation(); if (canClose) close(); }} onClick={e => { if (canClose && e.target === ref.current) close(); }} className={`modal rounded-2xl border border-line bg-panel p-0 text-ink shadow-xl backdrop:bg-black/35 ${className}`}>
    <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-6 py-4"><div className="min-w-0 flex-1"><h2 className="truncate text-base font-semibold" title={title}>{title}</h2>{subtitle && <p id={descriptionId} title={subtitle} className="mt-1 truncate text-[11px] font-normal text-faint">{subtitle}</p>}</div>{actions}<button className="icon-button" aria-label="Close dialog" disabled={!canClose} onClick={close}><X className="size-4" /></button></div>
    {children}
  </dialog>;
}

export function Loading({ label: text = "Loading" }: { label?: string }) {
  return <span role="status" className="inline-flex items-center gap-2 text-xs text-faint"><Loader2 className="size-3.5 animate-spin" />{text}</span>;
}
