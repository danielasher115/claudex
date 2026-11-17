import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { reasoningOf, type Child, type Item, type Session } from "../api";
import { groupTranscript, isFinal, itemRecord, itemTime, workDuration, type WorkBlock } from "../timeline";
import { exactTime, timeSeparator, timestamp } from "../time";
import { ConversationItem } from "./Conversation";

function duration(seconds: number) {
  const value = Math.floor(seconds);
  return value < 60 ? `${value}s` : value < 3600 ? `${Math.floor(value / 60)}m ${value % 60}s` : `${Math.floor(value / 3600)}h ${Math.floor(value / 60) % 60}m`;
}

const runningItem = (item: Item) => itemRecord(item).type !== "reasoning" && ["inProgress", "running"].includes(String(itemRecord(item).status));

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
  const running = [...block.items].reverse().find(runningItem);
  const record = running && itemRecord(running);
  if (record) {
    if (record.type === "commandExecution") {
      const actions = Array.isArray(record.commandActions) ? record.commandActions : [];
      if (actions.length && actions.every(action => action.type === "read")) return "Reading files";
      if (actions.length && actions.every(action => action.type === "search")) return "Searching files";
      if (actions.length && actions.every(action => action.type === "listFiles")) return "Listing files";
      if (actions.length && actions.every(action => ["read", "search", "listFiles"].includes(action.type))) return "Exploring files";
      return "Running command";
    }
    if (record.type === "fileChange") return "Editing files";
    if (record.type === "webSearch") return "Searching the web";
    return `Using ${String(record.tool || record.name || "tool")}`;
  }
  return "Thinking";
}

function Thinking({ label }: { label: string }) {
  return <div role="status" className="desktop-thinking"><span className="thinking-shimmer">{label}</span></div>;
}

interface WorkProps { block: WorkBlock; expanded?: boolean; matchedIds?: Set<string>; select?: (id: string) => void; session?: Session | null; agents?: Child[] }

function Work(props: WorkProps) {
  const { block, expanded, matchedIds, select, session, agents } = props;
  const groups: { reasoning: boolean; items: Item[] }[] = [];
  for (const item of block.items) {
    const reasoning = itemRecord(item).type === "reasoning";
    const last = groups.at(-1);
    if (last?.reasoning === reasoning) last.items.push(item);
    else groups.push({ reasoning, items: [item] });
  }
  const last = block.items.at(-1);
  const currentReasoning = last && itemRecord(last).type === "reasoning" ? reasoningOf(itemRecord(last)) : null;
  const waiting = session?.activity?.turn_id === block.turnId && session.activity.phase.startsWith("waiting_");
  const thinking = block.active && !block.items.some(runningItem) && (waiting || !currentReasoning?.heading && !currentReasoning?.content);
  return <>{groups.map(group => group.reasoning ? group.items.map(item => <ConversationItem key={item.id} sessionId={session?.id} item={item} expanded={expanded} matched={matchedIds?.has(item.id)} select={select} agents={agents} />) : <ToolWork {...props} key={group.items[0].id} block={{ ...block, items: group.items, active: block.active && group.items.some(runningItem) }} />)}{thinking && <Thinking label={liveLabel(block, session)} />}</>;
}

function ToolWork({ block, expanded, matchedIds, select, session, agents }: WorkProps) {
  const [choice, setChoice] = useState(false);
  useEffect(() => { if (expanded !== undefined) setChoice(expanded); }, [expanded]);
  const matched = block.items.some(item => matchedIds?.has(item.id));
  const open = matched || choice;
  const activities = block.items;
  const only = activities.length === 1 ? itemRecord(activities[0]) : null;
  const running = only && ["inProgress", "running"].includes(String(only.status));
  const multiPatch = only?.type === "fileChange" && Array.isArray(only.changes) && only.changes.length !== 1;
  if (!block.active && only && !running && !multiPatch) return <div className="desktop-tool" data-work-turn={block.turnId}><ConversationItem sessionId={session?.id} item={activities[0]} expanded={expanded} matched={matchedIds?.has(activities[0].id)} select={select} agents={agents} compact /></div>;
  const label = block.active ? liveLabel(block, session) : summary(activities);
  return <section className="worklog" data-work-turn={block.turnId} aria-label="Agent work">
    <button type="button" aria-expanded={open} onClick={() => setChoice(!open)} className="worklog-toggle"><span aria-live={block.active ? "polite" : undefined} className="min-w-0 truncate">{label}</span><ChevronRight className={`worklog-chevron size-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} /></button>
    {open && <div className="worklog-content">{activities.map(item => <ConversationItem sessionId={session?.id} key={item.id} item={item} expanded={expanded} matched={matchedIds?.has(item.id)} select={select} agents={agents} compact />)}</div>}
  </section>;
}

export function Transcript({ items, metadata, expanded, matchedIds, select, session, agents, after }: { items: Item[]; metadata?: Item[]; expanded?: boolean; matchedIds?: Set<string>; select?: (id: string) => void; session?: Session | null; agents?: Child[]; after?: (item: Item) => ReactNode }) {
  const blocks = useMemo(() => groupTranscript(items, metadata), [items, metadata]);
  const { turns, firstPrompts } = useMemo(() => {
    const turns = new Map<string, Item>(), firstPrompts = new Map<string, string>();
    let current = "recorded";
    for (const item of metadata ?? items) {
      const record = itemRecord(item);
      current = String(item.turn_id ?? record.turn_id ?? (item.type === "turn" ? item.id : current));
      if (item.type === "turn") turns.set(current, item);
      if ((record.type === "userMessage" || record.type === "message" && record.role === "user") && !firstPrompts.has(current)) firstPrompts.set(current, item.id);
    }
    return { turns, firstPrompts };
  }, [metadata, items]);
  const last = blocks.at(-1);
  const active = [...turns.entries()].reverse().find(([, turn]) => turn.status === "inProgress");
  const currentGroup = active && last?.type === "work" && last.active && last.turnId === active[0];
  const needsThinking = Boolean(active && !currentGroup && !blocks.some(block => block.type === "item" && block.turnId === active[0] && isFinal(block.item)));
  const lastInTurn = new Map(blocks.map(block => [block.turnId, block.id]));
  let previousTime: number | null = null;
  return <>{blocks.map(block => {
    const before = block.type === "item" && isFinal(block.item) ? workDuration({ type: "work", id: "timing", turnId: block.turnId, turn: turns.get(block.turnId), final: block.item, items: [], active: false }, 0) : null;
    const turn = turns.get(block.turnId);
    const head = block.type === "item" ? block.item : block.items[0];
    const turnStartedAt = firstPrompts.get(block.turnId) === head.id ? turn?.started_at : undefined;
    const at = itemTime(head) ?? timestamp(turnStartedAt);
    const separator = timeSeparator(at, previousTime);
    const tailTime = block.type === "work" ? itemTime(block.items.at(-1)!) : null;
    const completedAt = block.type === "item" && isFinal(block.item) ? timestamp(turn?.completed_at) : null;
    previousTime = completedAt ?? tailTime ?? at ?? previousTime;
    const stopped = turn?.status === "interrupted" && lastInTurn.get(block.turnId) === block.id;
    const stoppedTime = stopped ? workDuration({ type: "work", id: "stopped", turnId: block.turnId, turn, items: [], active: false }, 0) : null;
    return <Fragment key={block.id}>{separator && <div className="turn-divider" data-timeline-separator title={exactTime(at)}><span className="h-px flex-1 bg-line" /><span>{separator}</span><span className="h-px flex-1 bg-line" /></div>}{before !== null && <div className="desktop-work-duration">Worked for {duration(before)}</div>}{block.type === "item" ? <ConversationItem sessionId={session?.id} item={block.item} turnStartedAt={turnStartedAt} expanded={expanded} matched={matchedIds?.has(block.item.id)} select={select} agents={agents} /> : <Work block={block} expanded={expanded} matchedIds={matchedIds} select={select} agents={agents} session={session} />}{after?.(block.type === "item" ? block.item : block.items.at(-1)!)}{stopped && <div role="status" className="desktop-work-duration">Stopped{stoppedTime !== null ? ` after ${duration(stoppedTime)}` : ""}</div>}</Fragment>;
  })}{needsThinking && <Thinking label={session?.activity?.phase.startsWith("waiting_") ? session.activity.label : "Thinking"} />}</>;
}
