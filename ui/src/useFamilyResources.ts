import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, mergeItems, type Child, type Item, type Page, type Session } from "./api";
import { collectSources, type SessionSource } from "./sessionSources";
import { FAMILY_CHANGES_ID, familyRunId, runChanges, type RunChanges } from "./changes";
import type { SessionResources } from "./components/SessionView";

export type FamilySource = SessionSource & { sessionId: string; sessionName?: string };
interface Entry {
  sources: Map<string, SessionSource>; revision?: string; fetchedAt: number; cursor: string | null;
  items: Item[];
  knownRunsAtFetch?: RunChanges[];
  loaded: boolean; loading: boolean; error: string; source?: string; earlierAt: number;
  latestIds: Set<string>;
  retry: boolean; failedCursor?: string;
}
interface Options { rootId: string; children: Child[]; sessions: Session[]; known: Record<string, SessionResources>; enabled: boolean }

function mergeRunWindows(primary: RunChanges[], secondary: RunChanges[]) {
  // Shared runs anchor the ordered history windows; disjoint history precedes the fresher window.
  const positions = new Map(primary.map((run, index) => [run.id, index]));
  const result: RunChanges[] = [];
  let at = 0;
  for (const run of secondary) {
    const index = positions.get(run.id);
    if (index === undefined) result.push(run);
    else while (at <= index) result.push(primary[at++]);
  }
  return [...result, ...primary.slice(at)];
}

export function useFamilyResources({ rootId, children, sessions, known, enabled }: Options) {
  const cache = useRef({ rootId, entries: new Map<string, Entry>(), controllers: new Set<AbortController>() });
  if (cache.current.rootId !== rootId) {
    cache.current = { rootId, entries: new Map(), controllers: new Set() };
  }
  const state = cache.current;
  const [version, changed] = useState(0);
  const [earlier, setEarlier] = useState(0);
  const handledEarlier = useRef(0);
  const sessionById = useMemo(() => new Map(sessions.map(session => [session.id, session])), [sessions]);
  const childById = useMemo(() => new Map(children.map(child => [child.thread_id, child])), [children]);
  const ids = [...childById.keys()].filter(id => id !== rootId).sort();
  const familyKey = JSON.stringify(ids);
  const revisions = ids.map(id => {
    const session = sessionById.get(id);
    const child = childById.get(id);
    return [id, session?.revision ?? `${session?.updated_at ?? ""}:${session?.status ?? child?.status ?? ""}`];
  });
  const revisionKey = JSON.stringify(revisions);

  useEffect(() => () => {
    state.controllers.forEach(controller => controller.abort());
    state.controllers.clear();
  }, [state, enabled, familyKey]);

  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const requestedEarlier = handledEarlier.current !== earlier;
    if (requestedEarlier) handledEarlier.current = earlier;
    for (const id of ids) if (!state.entries.has(id)) state.entries.set(id, {
      sources: new Map(), items: [], fetchedAt: 0, cursor: null, loaded: false, loading: false, error: "", earlierAt: 0, latestIds: new Set(), retry: false,
    });
    const fetchPage = async (id: string, revision: string, cursor?: string) => {
      const entry = state.entries.get(id)!;
      const controller = new AbortController();
      state.controllers.add(controller);
      entry.loading = true; entry.error = ""; entry.retry = false;
      if (!cursor) { entry.revision = revision; entry.fetchedAt = Date.now(); }
      changed(value => value + 1);
      try {
        const page = await api<Page>(`/api/sessions/${encodeURIComponent(id)}/sources${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        if (entry.source && entry.source !== page.source) { entry.sources.clear(); entry.items = []; entry.latestIds.clear(); entry.loaded = false; }
        for (const source of collectSources(page.items)) entry.sources.set(source.id, source);
        entry.items = mergeItems(entry.items, page.items, Boolean(cursor));
        if (!cursor) entry.knownRunsAtFetch = known[id]?.runs;
        // A latest page without overlap leaves a gap before the saved history cursor.
        const pageIds = page.items.filter(item => item.type !== "turn").map(item => item.id);
        if (cursor || !entry.loaded || !pageIds.some(id => entry.latestIds.has(id))) entry.cursor = page.next_cursor;
        if (!cursor) entry.latestIds = new Set(pageIds);
        entry.source = page.source; entry.loaded = true;
      } catch (error) {
        if (!controller.signal.aborted) { entry.error = error instanceof Error ? error.message : String(error); entry.failedCursor = cursor; }
      } finally {
        state.controllers.delete(controller); entry.loading = false;
        if (controller.signal.aborted && !cursor) entry.revision = undefined;
        if (cache.current === state) changed(value => value + 1);
      }
    };
    const slots = Math.max(0, 2 - [...state.entries.values()].filter(entry => entry.loading).length);
    const latest: [string, string, string?][] = [];
    const retries: [string, string, string?][] = revisions.filter(([id]) => {
      const entry = state.entries.get(id)!;
      return entry.retry && !entry.loading;
    }).map(([id, revision]) => [id, revision, state.entries.get(id)!.failedCursor]);
    let nextRefresh = Infinity;
    for (const [id, revision] of revisions) {
      const entry = state.entries.get(id)!;
      if (entry.loading || entry.revision === revision) continue;
      const delay = entry.fetchedAt ? 10_000 - (Date.now() - entry.fetchedAt) : 0;
      if (delay > 0) nextRefresh = Math.min(nextRefresh, delay);
      else latest.push([id, revision]);
    }
    const older = requestedEarlier ? revisions.filter(([id]) => {
      const entry = state.entries.get(id)!;
      return entry.loaded && entry.cursor && !entry.loading;
    }).sort(([a], [b]) => state.entries.get(a)!.earlierAt - state.entries.get(b)!.earlierAt).slice(0, 2).map(([id, revision]) => {
      const entry = state.entries.get(id)!;
      entry.earlierAt = Date.now();
      return [id, revision, entry.cursor!] as [string, string, string];
    }) : [];
    const queued = new Map<string, [string, string, string?]>();
    for (const request of [...retries, ...older, ...latest]) if (!queued.has(request[0])) queued.set(request[0], request);
    for (const [id, revision, cursor] of [...queued.values()].slice(0, slots)) void fetchPage(id, revision, cursor);
    if (Number.isFinite(nextRefresh)) timer = setTimeout(() => { if (!disposed) changed(value => value + 1); }, nextRefresh);
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [state, enabled, familyKey, revisionKey, earlier, version]);

  const sources = useMemo(() => {
    const result: FamilySource[] = [];
    for (const id of [rootId, ...ids]) {
      const values = new Map(state.entries.get(id)?.sources);
      for (const source of known[id]?.sources ?? []) values.set(source.id, source);
      const sessionName = childById.get(id)?.name ?? sessionById.get(id)?.title;
      for (const source of values.values()) result.push({ ...source, id: `${id}:${source.id}`, sessionId: id, sessionName });
    }
    return result;
  }, [rootId, familyKey, state, version, known, childById, sessionById]);
  const runs = useMemo(() => {
    const result: RunChanges[] = [];
    const latest = new Map<string, RunChanges>();
    for (const id of [rootId, ...ids]) {
      const child = childById.get(id), session = known[id]?.session ?? sessionById.get(id);
      const owner = { sessionId: id, name: child?.name || session?.title || (id === rootId ? "Managing agent" : id), cwd: session?.cwd || child?.cwd || "" };
      const entry = state.entries.get(id);
      const cachedRuns = runChanges(entry?.items ?? [], owner.cwd), knownRuns = known[id]?.runs ?? [];
      const knownIsNewer = known[id]?.runs !== entry?.knownRunsAtFetch;
      const values = knownIsNewer ? mergeRunWindows(knownRuns, cachedRuns) : mergeRunWindows(cachedRuns, knownRuns);
      for (const run of values) {
        const owned = { ...run, id: familyRunId(id, run.id), originalRunId: run.id, owner, files: run.files.map(file => ({ ...file, owner })) };
        result.push(owned);
        if (run.files.length) latest.set(id, owned);
      }
    }
    const all: RunChanges = { id: FAMILY_CHANGES_ID, prompt: "All agents", status: "", files: [...latest.values()].flatMap(run => run.files), source: "recorded", lastItemId: "", aggregate: true,
      warning: ["Latest recorded changes from each agent. Shared files appear separately under each agent and workspace.", ...[...latest.values()].filter(run => run.warning).map(run => `${run.owner!.name}: ${run.warning}`)].join("\n") };
    return [all, ...result];
  }, [rootId, familyKey, state, version, known, childById, sessionById]);
  const entries = ids.map(id => state.entries.get(id));
  const loadEarlier = useCallback(() => setEarlier(value => value + 1), []);
  const retry = useCallback(() => {
    for (const entry of state.entries.values()) if (entry.error) entry.retry = true;
    changed(value => value + 1);
  }, [state]);
  return {
    sources, runs, changes: runs[0].files, loading: enabled && entries.some(entry => !entry || entry.loading || entry.retry || entry.revision === undefined),
    error: [...new Set(entries.map(entry => entry?.error).filter(Boolean))].join("; "),
    hasEarlier: entries.some(entry => Boolean(entry?.cursor)), loadEarlier, retry,
  };
}
