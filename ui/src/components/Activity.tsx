import { useEffect, useState } from "react";
import { Brain, Check, Clock3, FilePenLine, FileSearch, FileText, GitBranch, MessageSquare, ShieldQuestion, Square, SquareTerminal, Unplug } from "lucide-react";
import type { Session } from "../api";

const ICONS = { thinking: Brain, reading: FileText, searching: FileSearch, running_command: SquareTerminal, editing: FilePenLine, waiting_agents: GitBranch, delegating: GitBranch, responding: MessageSquare, waiting_approval: ShieldQuestion, waiting_input: MessageSquare, completed: Check, interrupted: Square, offline: Unplug };

function elapsed(value: number) {
  const seconds = Math.max(0, Math.floor(value));
  return seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${Math.floor(seconds / 3600)}h ${Math.floor(seconds / 60) % 60}m`;
}

function useClock(active: boolean) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { setNow(Date.now() / 1000); if (!active) return; const timer = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, [active]);
  return now;
}

export function ActivityLine({ session }: { session: Session }) {
  const active = session.status === "active";
  const now = useClock(active);
  const activity = session.activity;
  if (!active && !activity) return null;
  if (!active && !["completed", "interrupted", "failed"].includes(activity?.phase ?? "")) return null;
  const Icon = ICONS[activity?.phase as keyof typeof ICONS] ?? Clock3;
  return <div className={`mt-1.5 flex min-w-0 items-center gap-1.5 text-[10.5px] ${active ? "text-muted" : "text-faint"}`} title={activity?.detail ?? "Codex reports an active turn"}><Icon className="size-3 shrink-0" /><span className="min-w-0 truncate">{activity?.label ?? "Working"}</span>{active && activity?.turn_started_at && <span className="ml-auto shrink-0 tabular-nums text-faint">{elapsed(now - activity.turn_started_at)}</span>}</div>;
}
