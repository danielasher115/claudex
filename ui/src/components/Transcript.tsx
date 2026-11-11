import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { reasoningOf, type Item, type Session } from "../api";
import { groupTranscript, isFinal, itemRecord, workDuration, type WorkBlock } from "../timeline";
import { ConversationItem } from "./Conversation";

function duration(seconds: number) {
  const value = Math.floor(seconds);
  return value < 60 ? `${value}s` : value < 3600 ? `${Math.floor(value / 60)}m ${value % 60}s` : `${Math.floor(value / 3600)}h ${Math.floor(value / 60) % 60}m`;
}

function heading(items: Item[]) {
  for (const item of [...items].reverse()) {
    if (itemRecord(item).type !== "reasoning") continue;
    const reasoning = reasoningOf(itemRecord(item));
    const last = [...reasoning.content.split(/\r?\n/)].reverse().find(line => line.trim() && !line.trim().startsWith("<!--") && !"<!--".startsWith(line.trim()));
    const value = (reasoning.heading || last || "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[*_`#~]/g, "").replace(/^\s*[-+]\s+/, "").trim();
    if (value) return value;
  }
  return "Thinking";
}

function summary(items: Item[]) {
  const records = items.map(itemRecord);
  const commands = records.filter(record => record.type === "commandExecution");
  if (commands.length === records.length && commands.length) {
    const actions = commands.flatMap(record => Array.isArray(record.commandActions) ? record.commandActions : []);
    if (actions.length && actions.every(action => ["read", "search", "listFiles"].includes(action.type))) {
      const parts = [["read", "file"], ["search", "search"], ["listFiles", "list"]].flatMap(([type, name]) => {
        const count = actions.filter(action => action.type === type).length;
        return count ? [`${count} ${name}${count === 1 ? "" : name === "search" ? "es" : "s"}`] : [];
      });
      return `Explored ${parts.join(", ")}`;
    }
    return `Ran ${commands.length} command${commands.length === 1 ? "" : "s"}`;
  }
  if (records.length && records.every(record => record.type === "fileChange")) {
    const files = new Set(records.flatMap(record => Array.isArray(record.changes) ? record.changes.map(change => change.path) : []));
    return `Edited ${files.size} file${files.size === 1 ? "" : "s"}`;
  }
  if (records.length && records.every(record => record.type === "webSearch")) return "Searched the web";
  return "Worked";
}

function liveLabel(block: WorkBlock, session?: Session | null) {
  if (session?.activity?.turn_id === block.turnId && ["waiting_approval", "waiting_input", "waiting_agents"].includes(session.activity.phase)) return session.activity.label;
  const running = [...block.items].reverse().map(itemRecord).find(record => record.status === "inProgress" || record.status === "running");
  if (running) {
    if (running.type === "commandExecution") return "Running command";
    if (running.type === "fileChange") return "Editing files";
    return `Using ${String(running.tool || running.name || "tool")}`;
  }
  return heading(block.items);
}

function Thinking({ label }: { label: string }) {
  return <div role="status" className="desktop-thinking"><span className="thinking-shimmer">{label}</span></div>;
}

function Work({ block, expanded, matchedIds, select, session }: { block: WorkBlock; expanded?: boolean; matchedIds?: Set<string>; select?: (id: string) => void; session?: Session | null }) {
  const [choice, setChoice] = useState(false);
  useEffect(() => { if (expanded !== undefined) setChoice(expanded); }, [expanded]);
  const matched = block.items.some(item => matchedIds?.has(item.id));
  const open = matched || choice;
  const activities = block.items.filter(item => itemRecord(item).type !== "reasoning" || matchedIds?.has(item.id));
  if (!activities.length) return block.active ? <Thinking label={liveLabel(block, session)} /> : null;
  const only = activities.length === 1 ? itemRecord(activities[0]) : null;
  const running = only && ["inProgress", "running"].includes(String(only.status));
  const multiPatch = only?.type === "fileChange" && Array.isArray(only.changes) && only.changes.length !== 1;
  if (!block.active && only && !running && !multiPatch) return <div className="desktop-tool" data-work-turn={block.turnId}><ConversationItem sessionId={session?.id} item={activities[0]} expanded={expanded} matched={matchedIds?.has(activities[0].id)} select={select} compact /></div>;
  const label = block.active ? liveLabel(block, session) : summary(activities);
  const streaming = block.active && !block.items.some(item => ["inProgress", "running"].includes(String(itemRecord(item).status)));
  return <section className="worklog" data-work-turn={block.turnId} aria-label="Agent work">
    <button type="button" aria-expanded={open} onClick={() => setChoice(!open)} className="worklog-toggle"><span aria-live={block.active ? "polite" : undefined} className={`min-w-0 truncate ${streaming ? "thinking-shimmer" : ""}`}>{label}</span><ChevronRight className={`worklog-chevron size-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} /></button>
    {open && <div className="worklog-content">{activities.map(item => <ConversationItem sessionId={session?.id} key={item.id} item={item} expanded={expanded} matched={matchedIds?.has(item.id)} select={select} compact />)}</div>}
  </section>;
}

export function Transcript({ items, metadata, expanded, matchedIds, select, session, after }: { items: Item[]; metadata?: Item[]; expanded?: boolean; matchedIds?: Set<string>; select?: (id: string) => void; session?: Session | null; after?: (item: Item) => ReactNode }) {
  const blocks = useMemo(() => groupTranscript(items, metadata), [items, metadata]);
  const turns = new Map((metadata ?? items).filter(item => item.type === "turn").map(item => [String(item.turn_id ?? item.id), item]));
  const last = blocks.at(-1);
  const active = [...turns.entries()].reverse().find(([, turn]) => turn.status === "inProgress");
  const currentGroup = active && last?.type === "work" && last.active && last.turnId === active[0];
  const needsThinking = Boolean(active && !currentGroup && !blocks.some(block => block.type === "item" && block.turnId === active[0] && isFinal(block.item)));
  const seenWork = new Set<string>();
  const lastInTurn = new Map(blocks.map(block => [block.turnId, block.id]));
  return <>{blocks.map(block => {
    if (block.type === "work") seenWork.add(block.turnId);
    const before = block.type === "item" && isFinal(block.item) && seenWork.has(block.turnId) ? workDuration({ type: "work", id: "timing", turnId: block.turnId, turn: turns.get(block.turnId), final: block.item, items: [], active: false }, 0) : null;
    const turn = turns.get(block.turnId);
    const stopped = turn?.status === "interrupted" && lastInTurn.get(block.turnId) === block.id;
    const stoppedTime = stopped ? workDuration({ type: "work", id: "stopped", turnId: block.turnId, turn, items: [], active: false }, 0) : null;
    return <Fragment key={block.id}>{before !== null && <div className="desktop-work-duration">Worked for {duration(before)}</div>}{block.type === "item" ? <ConversationItem sessionId={session?.id} item={block.item} expanded={expanded} matched={matchedIds?.has(block.item.id)} select={select} /> : <Work block={block} expanded={expanded} matchedIds={matchedIds} select={select} session={session} />}{after?.(block.type === "item" ? block.item : block.items.at(-1)!)}{stopped && <div role="status" className="desktop-work-duration">Stopped{stoppedTime !== null ? ` after ${duration(stoppedTime)}` : ""}</div>}</Fragment>;
  })}{needsThinking && <Thinking label={session?.activity?.phase.startsWith("waiting_") ? session.activity.label : "Thinking"} />}</>;
}
