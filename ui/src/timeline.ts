import type { Item } from "./api";

export const itemRecord = (item: Item) => (item.type === "record" ? item.record : item) as Record<string, unknown>;

export function isFinal(item: Item) {
  const record = itemRecord(item);
  return (record.type === "agentMessage" || record.type === "message" && record.role === "assistant") && record.delivery !== "async" && !(Array.isArray(record.questions) && record.questions.length) && (record.phase === "final_answer" || record.phase == null);
}

export interface WorkBlock { type: "work"; id: string; turnId: string; items: Item[]; turn?: Item; final?: Item; closedBy?: Item; active: boolean }
export type TranscriptBlock = WorkBlock | { type: "item"; item: Item; id: string; turnId: string };

export function groupTranscript(items: Item[], metadata: Item[] = items): TranscriptBlock[] {
  const turns = new Map(metadata.filter(item => item.type === "turn").map(item => [String(item.turn_id ?? item.id), item]));
  const blocks: TranscriptBlock[] = [];
  let work: WorkBlock | null = null, current = "recorded";
  const flush = (closedBy?: Item) => { if (work) { work.closedBy = closedBy; blocks.push(work); } work = null; };
  for (const item of items) {
    const turnId = String(item.turn_id ?? itemRecord(item).turn_id ?? (item.type === "turn" ? item.id : current));
    if (turnId !== current || item.type === "turn") { flush(); current = turnId; }
    if (item.type === "turn") { if (item.error) blocks.push({ type: "item", item, id: item.id, turnId }); continue; }
    const record = itemRecord(item);
    const visible = ["userMessage", "agentMessage", "message", "plan", "imageView", "imageGeneration", "subAgentActivity", "contextCompaction"].includes(String(record.type));
    if (visible) { flush(item); blocks.push({ type: "item", item, id: item.id, turnId }); }
    else {
      if (!work) work = { type: "work", id: `work:${item.id}`, turnId: current, turn: turns.get(current), items: [], active: false };
      work.items.push(item);
    }
  }
  flush();
  const finals = new Map<string, Item>(), laterWork = new Set<string>();
  for (const block of [...blocks].reverse()) {
    if (block.type === "item") { if (isFinal(block.item)) finals.set(block.turnId, block.item); continue; }
    block.final = finals.get(block.turnId);
    block.active = block.turn?.status === "inProgress" && !block.closedBy && !block.final && !laterWork.has(block.turnId);
    laterWork.add(block.turnId);
  }
  return blocks;
}

export function workDuration(block: WorkBlock, now: number): number | null {
  const start = typeof block.turn?.started_at === "number" ? block.turn.started_at : block.items.find(item => typeof item._started_at === "number")?._started_at;
  const end = typeof block.final?._started_at === "number" ? block.final._started_at : block.active ? now : !block.final && typeof block.turn?.completed_at === "number" ? block.turn.completed_at : null;
  return typeof start === "number" && typeof end === "number" ? Math.max(0, end - start) : null;
}
