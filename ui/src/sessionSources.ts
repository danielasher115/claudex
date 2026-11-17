import { unified } from "unified";
import remarkParse from "remark-parse";
import { textOf, type Artifact, type Item } from "./api";

export interface SessionSource { id: string; name: string; origin: "attached" | "generated" | "viewed" | "referenced"; file?: Artifact; href?: string; itemId: string }

type RecordValue = Record<string, unknown>;
interface MarkdownNode { type: string; url?: string; value?: string; alt?: string | null; identifier?: string; children?: MarkdownNode[] }
const parser = unified().use(remarkParse);
const provenance = { referenced: 0, viewed: 1, attached: 2, generated: 3 };
const itemCache = new WeakMap<Item, SessionSource[]>();
const object = (value: unknown): RecordValue => value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};

function safeHref(value: unknown, local = false): string | undefined {
  if (typeof value !== "string" || !value || /[\u0000-\u001f\u007f]/.test(value) || /^(?:[?#]|\/\/|\\\\)/.test(value)) return;
  if (/^https?:\/\//i.test(value)) {
    try { const url = new URL(value); return url.hostname && !url.username && !url.password ? value : undefined; } catch { return; }
  }
  if (!local) return;
  if (/^file:/i.test(value)) {
    try { const url = new URL(value); return !url.hostname || url.hostname === "localhost" ? value : undefined; } catch { return; }
  }
  if (/^[a-z]:(?:[\\/]|%2f|%5c)/i.test(value)) return value;
  if (/^[a-z][a-z\d+.-]*:/i.test(value)) return;
  return value === "/" || /^\/(?:api|sessions|assets)(?:\/|$)/.test(value) ? undefined : value;
}

function markdownLinks(text: string, add: (href: unknown, name?: unknown, local?: boolean) => void) {
  const root: MarkdownNode = parser.parse(text);
  const definitions = new Map<string, MarkdownNode>();
  const index = (node: MarkdownNode) => {
    if (node.type === "definition" && node.identifier && !definitions.has(node.identifier)) definitions.set(node.identifier, node);
    node.children?.forEach(index);
  };
  index(root);
  const label = (node: MarkdownNode): string => node.value ?? node.alt ?? node.children?.map(label).join("") ?? "";
  const visit = (node: MarkdownNode) => {
    if (["code", "inlineCode", "html", "definition"].includes(node.type)) return;
    if (["link", "linkReference", "image", "imageReference"].includes(node.type)) {
      add(node.url ?? definitions.get(node.identifier ?? "")?.url, label(node), true);
      return;
    }
    if (node.type === "text") {
      for (const match of (node.value ?? "").matchAll(/https?:\/\/[^\s<>"']+/g)) {
        let href = match[0].replace(/[.,;!?]+$/, "");
        while (href.endsWith(")") && (href.match(/\)/g)?.length ?? 0) > (href.match(/\(/g)?.length ?? 0)) href = href.slice(0, -1);
        add(href);
      }
    }
    node.children?.forEach(visit);
  };
  visit(root);
}

function extractSources(items: Item[], attached: Artifact[] = []): SessionSource[] {
  const sources = new Map<string, SessionSource>();
  const addFile = (value: unknown, origin: SessionSource["origin"], itemId: string, path?: unknown) => {
    const candidate = object(value);
    if (typeof candidate.id !== "string" || typeof candidate.name !== "string" || typeof candidate.content_url !== "string") return;
    const file = { ...candidate, ...(typeof path === "string" && !candidate.source_path ? { source_path: path } : {}) } as Artifact;
    const id = `file:${file.id}`, previous = sources.get(id);
    sources.set(id, { id, name: file.name, origin: previous && provenance[previous.origin] > provenance[origin] ? previous.origin : origin, file: { ...previous?.file, ...file }, itemId: previous?.itemId || itemId });
  };
  for (const item of items) {
    const record = item.type === "record" ? object(item.record) : item;
    const type = record.type;
    const user = type === "userMessage" || type === "message" && record.role === "user";
    const publicText = user || type === "agentMessage" || type === "message" && record.role === "assistant";
    const addLink = (value: unknown, name?: unknown, local = false) => {
      const href = safeHref(value, local);
      if (!href) return;
      const id = `link:${href}`;
      if (!sources.has(id)) sources.set(id, { id, name: typeof name === "string" && name.trim() ? name : href, origin: "referenced", href, itemId: item.id });
    };
    for (const owner of item === record ? [item] : [item, record]) {
      if (Array.isArray(owner.attachments)) for (const file of owner.attachments) addFile(file, "attached", item.id);
      if (Array.isArray(owner.images)) for (const file of owner.images) addFile(file, type === "imageGeneration" ? "generated" : user ? "attached" : "viewed", item.id, record.path ?? record.savedPath);
    }
    if (publicText || type === "plan") markdownLinks(textOf(record.text ?? record.content), addLink);
    if (type === "webSearch") {
      addLink(object(record.action).url);
      if (Array.isArray(record.results)) for (const value of record.results) {
        const result = object(value);
        addLink(result.url ?? result.link, result.title);
      }
    }
    if (publicText || type === "webSearch") {
      const cite = (value: unknown) => {
        if (!Array.isArray(value)) return;
        for (const entry of value) {
          const citation = object(entry), urlCitation = object(citation.url_citation);
          addLink(citation.url ?? urlCitation.url, citation.title ?? urlCitation.title);
        }
      };
      cite(record.citations); cite(record.annotations);
      if (Array.isArray(record.content)) for (const block of record.content) { const content = object(block); cite(content.citations); cite(content.annotations); }
    }
  }
  for (const file of attached) addFile(file, "attached", "");
  return [...sources.values()];
}

export function collectSources(items: Item[], attached: Artifact[] = []): SessionSource[] {
  const sources = new Map<string, SessionSource>();
  const merge = (source: SessionSource) => {
    const previous = sources.get(source.id);
    if (previous && !source.file) return;
    sources.set(source.id, {
      ...source,
      origin: previous && provenance[previous.origin] > provenance[source.origin] ? previous.origin : source.origin,
      itemId: previous?.itemId || source.itemId,
      ...(source.file ? { file: { ...previous?.file, ...source.file } } : {}),
    });
  };
  for (const item of items) {
    let parsed = itemCache.get(item);
    if (!parsed) { parsed = extractSources([item]); itemCache.set(item, parsed); }
    parsed.forEach(merge);
  }
  extractSources([], attached).forEach(merge);
  return [...sources.values()];
}
