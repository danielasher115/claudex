export interface Delivery {
  id: string; text: string; mode: string; status: string; error: string | null; turn_id: string | null; created_at: number;
}
export interface Session {
  id: string; title: string; preview: string; cwd: string; model: string; source: string; origin: string;
  status: string; flags: string[]; created_at: number; updated_at: number; archived: boolean;
  can_send: boolean; reason: string | null; active_turn_id?: string | null; messages?: Delivery[];
  can_steer_queued?: boolean;
  revision?: string; tokens?: number;
  parent_id?: string | null; depth?: number; sandbox?: string;
  inherited_context?: { provider: string; instruction_files: number; memory_files: number; skills: number; has_session_summary: boolean; settings: string[]; unsupported_settings: string[]; warnings: string[]; sources: string[] };
  activity?: AgentActivity;
}
export interface AgentActivity { phase: string; label: string; detail: string; turn_id: string | null; turn_started_at: number | null; phase_started_at: number; last_event_at: number | null; last_update: string; source: string; tool_count: number }
export interface Artifact { id: string; name: string; mime: string; kind: string; size: number; characters: number; pages: number | null; image_count: number; status: string; content_url: string; summary?: string; source_path?: string }
export interface Child { thread_id: string; parent_id: string; name: string; depth: number; cwd: string }
export interface Snapshot {
  sessions: Session[];
  connection: { connected: boolean; error: string | null; home: string; socket: string };
}
export interface Item { id: string; type: string; [key: string]: unknown }
export interface Page { items: Item[]; next_cursor: string | null; source: string; warning: string | null }

export async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, body === undefined ? { signal } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? `Request failed (${response.status})`);
  return data;
}

export function mergeItems(previous: Item[], incoming: Item[], older = false): Item[] {
  const ordered = older ? [...incoming, ...previous] : [...previous, ...incoming];
  const merged = new Map<string, Item>();
  for (const item of ordered) {
    const existing = merged.get(item.id);
    merged.set(item.id, existing && JSON.stringify(existing) === JSON.stringify(item) ? existing : item);
  }
  return [...merged.values()];
}

export const label = (status: string) => ({ active: "Working", idle: "Idle", saved: "Saved", external: "Separate process", unavailable: "Offline", systemError: "Error" }[status] ?? status);
export const project = (cwd: string) => cwd.split(/[\\/]/).filter(Boolean).pop() || "No project";
export const tokens = (value: number) => Intl.NumberFormat(undefined, { notation: value >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
export const exactTime = (at: number) => new Date(at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

export function textOf(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(textOf).filter(Boolean).join("\n");
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return textOf(record.text ?? record.content ?? record.output ?? record.summary ?? "");
  }
  return "";
}

export function reasoningOf(record: Record<string, unknown>): { heading: string; content: string } {
  const parts = (value: unknown) => Array.isArray(value) ? value.map(textOf) : [textOf(value)];
  const split = (values: string[]) => {
    let placeholderHeading = "";
    const content: string[] = [];
    for (const value of values) {
      const part = value.trim();
      if (!part) continue;
      const end = part.startsWith("**") && part.indexOf("**", 2) > 2 ? part.indexOf("**", 2) + 2 : 0;
      if ((end ? part.slice(end) : part).trim() === "<!-- -->") { if (!content.length && !placeholderHeading && end) placeholderHeading = part.slice(2, end - 2); continue; }
      content.push(part);
    }
    const joined = content.join("\n\n");
    const end = joined.startsWith("**") ? joined.indexOf("**", 2) : -1;
    if (end > 2 && /^[\r\n]/.test(joined.slice(end + 2))) return { heading: joined.slice(2, end), content: joined.slice(end + 2).trim() };
    return { heading: placeholderHeading, content: joined };
  };
  const summaryParts = parts(record.summary).filter(part => part.trim() && part.trim() !== "<!-- -->");
  if (summaryParts.length > 1 && !summaryParts[0].trim().startsWith("**")) summaryParts[0] = `**${summaryParts[0].trim()}**`;
  const summary = split(summaryParts);
  const fallback = split(parts(record.content));
  return summary.content ? summary : { heading: summary.heading || fallback.heading, content: fallback.content };
}

export function toolOutput(record: Record<string, unknown>): string {
  const value = record.type === "dynamicToolCall" ? record.contentItems : record.type === "mcpToolCall" ? record.result ?? record.error : record.aggregatedOutput ?? record.output ?? record.result;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const result = value as Record<string, unknown>;
    return textOf(result.content) || textOf(result.output) || (result.structuredContent ? JSON.stringify(result.structuredContent, null, 2) : Array.isArray(result.content) ? "" : JSON.stringify(result, null, 2));
  }
  if (Array.isArray(value) && value.every(block => block && typeof block === "object" && ["text", "inputText", "input_text", "image", "inputImage", "input_image", "audio", "inputAudio", "input_audio", "encrypted_content"].includes(block.type))) return textOf(value);
  return textOf(value) || (value == null ? "" : JSON.stringify(value, null, 2));
}

export function searchable(item: Item): string {
  const record = (item.type === "record" ? item.record : item) as Record<string, unknown>;
  return [record.text, record.content, record.summary, record.command, record.aggregatedOutput, record.output, record.arguments, record.changes, record.result]
    .map(value => typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "")).join("\n").toLowerCase();
}
export function ago(at: number) {
  const seconds = Math.max(0, Date.now() / 1000 - at);
  return seconds < 60 ? "just now" : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : seconds < 86400 ? `${Math.floor(seconds / 3600)}h ago` : new Date(at * 1000).toLocaleDateString();
}
