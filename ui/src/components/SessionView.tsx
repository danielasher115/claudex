import { lazy, Suspense, useCallback, useDeferredValue, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowDownToLine, ArrowUp, Check, ChevronDown, ChevronRight, ChevronUp, ChevronsDownUp, ChevronsUpDown, Clock3, FileDiff, GitBranch, Info, Pin, Search, Square, SquareTerminal, X } from "lucide-react";
import { api, mergeItems, project, searchable, type Child, type Delivery, type Item, type Page, type Session } from "../api";
import { Transcript } from "./Transcript";
import { Subagents } from "./Subagents";
import { Loading, Status } from "./shared";
import { AttachmentFeedback, AttachmentList, AttachmentPicker, useAttachments } from "./Attachments";
import { Select } from "./Select";
import { runChanges, type ChangedFile, type RunChanges } from "../changes";
import { collectSources, type SessionSource } from "../sessionSources";
import { ChangeCounts } from "./Diff";
import { BackgroundShells } from "./BackgroundShells";
const Changes = lazy(() => import("./Changes").then(module => ({ default: module.Changes })));
const SessionDetails = lazy(() => import("./SessionDetails").then(module => ({ default: module.SessionDetails })));

interface PendingRequest { id: string; text: string; mode: string; attachments?: string[] }
interface ContextUsage { last?: { total_tokens?: number; input_tokens?: number }; model_context_window?: number }
function pendingRequest(id: string): PendingRequest | null {
  try { return JSON.parse(localStorage.getItem(`pending:${id}`) ?? "null"); } catch { return null; }
}

export interface SessionResources {
  id: string; session: Session | null; children: Child[]; runs: RunChanges[]; sources: SessionSource[];
  hasEarlier: boolean; loadingEarlier: boolean; loadEarlier: () => void; addSources: () => void;
}
export interface SessionViewProps {
  id: string; summary?: Session; connected?: boolean; onChange: () => void; pinned: boolean; togglePin: () => void;
  select?: (id: string) => void; launchChild?: () => void; onResources?: (resources: SessionResources) => void;
  onReviewChanges?: (runId?: string) => void; embedded?: boolean; active?: boolean; keyboardActive?: boolean;
  headerActions?: ReactNode;
  reviewFiles?: ChangedFile[];
}

export function SessionView({ id, summary, connected, onChange, pinned: isPinned, togglePin, select, launchChild,
  onResources, onReviewChanges, embedded = false, active = true, keyboardActive = true, headerActions, reviewFiles }: SessionViewProps) {
  const [detail, setSession] = useState<Session | null>(summary ?? null);
  const session = detail ? { ...detail, ...summary, messages: detail.messages, active_turn_id: detail.active_turn_id, inherited_context: detail.inherited_context } : summary ?? null;
  const [items, setItems] = useState<Item[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [earlier, setEarlier] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [find, setFind] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [match, setMatch] = useState(0);
  const deferredFind = useDeferredValue(find.trim().toLowerCase());
  const [limit, setLimit] = useState(100);
  const [expanded, setExpanded] = useState<boolean | undefined>();
  const [info, setInfo] = useState(false);
  const [reviewRun, setReviewRun] = useState<string | null>(null);
  const [draft, setDraft] = useState(() => localStorage.getItem(`draft:${id}`) ?? "");
  const [mode, setMode] = useState(() => localStorage.getItem("delivery-mode") === "queue" ? "queue" : "steer");
  const [contextUsage, setContextUsage] = useState<ContextUsage | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [receipt, setReceipt] = useState("");
  const [following, setFollowing] = useState(true);
  const [steering, setSteering] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [children, setChildren] = useState<Child[]>([]);
  const attachments = useAttachments(`attachments:${id}`);
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const follow = useRef(true);
  const loaded = useRef(false);
  const loadedIds = useRef(new Set<string>());
  const pageSource = useRef<string | null>(null);
  const height = useRef<number | null>(null);
  const request = useRef<PendingRequest | null>(pendingRequest(id));
  const controller = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const pendingRefresh = useRef(false);
  const mounted = useRef(true);
  const refresh = useCallback(async (): Promise<void> => {
    if (inFlight.current) { pendingRefresh.current = true; return; }
    inFlight.current = true;
    const current = new AbortController(); controller.current = current;
    try {
      const data = await api<{ session: Session; history: Page; children?: Child[]; context_usage?: ContextUsage | null }>(`/api/sessions/${id}/view`, undefined, current.signal);
      if (current.signal.aborted) return;
      const sourceChanged = pageSource.current !== null && pageSource.current !== data.history.source;
      const incomingIds = data.history.items.filter(item => item.type !== "turn").map(item => item.id);
      const gap = loadedIds.current.size > 0 && incomingIds.length > 0 && !incomingIds.some(itemId => loadedIds.current.has(itemId));
      const replace = sourceChanged || gap;
      setSession(data.session); setItems(previous => replace ? data.history.items : mergeItems(previous, data.history.items));
      if (!loaded.current || replace) { setCursor(data.history.next_cursor); loaded.current = true; }
      if (replace) { loadedIds.current.clear(); setLimit(100); }
      incomingIds.forEach(itemId => loadedIds.current.add(itemId));
      pageSource.current = data.history.source;
      setWarning(data.history.warning ?? ""); setError("");
      setChildren(data.children ?? []);
      setContextUsage(data.context_usage ?? null);
    } catch (e) { if (!current.signal.aborted) setError((e as Error).message); }
    finally {
      inFlight.current = false;
      if (!current.signal.aborted) setLoading(false);
      if (mounted.current && pendingRefresh.current) { pendingRefresh.current = false; void refresh(); }
    }
  }, [id]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, [refresh]);
  useEffect(() => { if (active) void refresh(); }, [active, refresh, summary?.revision, summary?.status, connected]);
  useEffect(() => {
    if (!active || summary?.status !== "active") return;
    const timer = setInterval(() => void refresh(), 2000);
    return () => clearInterval(timer);
  }, [active, summary?.status, refresh]);
  useEffect(() => { localStorage.setItem(`draft:${id}`, draft); }, [id, draft]);
  useEffect(() => {
    const accepted = (event: Event) => {
      const detail = (event as CustomEvent<{ id: string; text: string; attachments?: string[] }>).detail;
      if (detail.id === id) {
        setDraft(current => current === detail.text ? "" : current);
        if (detail.attachments) attachments.setFiles(current => current.filter(a => !detail.attachments!.includes(a.id)));
      }
    };
    window.addEventListener("codex:message-sent", accepted);
    return () => window.removeEventListener("codex:message-sent", accepted);
  }, [id]);
  useEffect(() => {
    if (!textarea.current) return;
    textarea.current.style.height = "auto";
    textarea.current.style.height = `${Math.min(240, Math.max(32, textarea.current.scrollHeight))}px`;
  }, [draft]);
  useEffect(() => { if (!embedded) document.title = `${session?.title ?? "Session"} · Claudex`; }, [embedded, session?.title]);
  useEffect(() => { if (receipt) { const timer = setTimeout(() => setReceipt(""), 4000); return () => clearTimeout(timer); } }, [receipt]);
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element) return;
    if (height.current !== null) { element.scrollTop = element.scrollHeight - height.current; height.current = null; }
    else if (follow.current) element.scrollTop = element.scrollHeight;
  }, [items, limit, session?.messages]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (!active || !keyboardActive) return;
      if (document.querySelector("dialog[open]")) return;
      if ((e.ctrlKey || e.metaKey) && e.key === "f") { e.preventDefault(); setSearchOpen(true); requestAnimationFrame(() => search.current?.focus()); }
      if (e.key === "Escape" && searchOpen) { setSearchOpen(false); setFind(""); textarea.current?.focus(); }
    };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  }, [searchOpen, active, keyboardActive]);
  const matchIds = useMemo(() => new Set(deferredFind ? items.filter(item => searchable(item).includes(deferredFind)).map(item => item.id) : []), [items, deferredFind]);
  const displayItems = items;
  const navigateMatch = useCallback((index: number) => {
    const matches = scroller.current?.querySelectorAll<HTMLElement>("[data-search-match]");
    if (!matches?.length) return;
    const next = (index + matches.length) % matches.length;
    setMatch(next);
    follow.current = false; setFollowing(false);
    matches[next].scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    matches.forEach((element, i) => element.classList.toggle("search-current", i === next));
  }, []);
  useEffect(() => { if (deferredFind) navigateMatch(0); }, [deferredFind, navigateMatch]);
  const older = useCallback(async (forcePage = false) => {
    if (earlier) return;
    const anchor = () => { if (scroller.current) height.current = scroller.current.scrollHeight - scroller.current.scrollTop; };
    if (!forcePage && limit < displayItems.length && !deferredFind) { anchor(); setLimit(value => value + 100); return; }
    if (!cursor) return;
    setEarlier(true);
    try {
      const page = await api<Page>(`/api/sessions/${id}/history?cursor=${encodeURIComponent(cursor)}`);
      anchor(); setItems(current => mergeItems(current, page.items, true)); setLimit(value => value + page.items.length); setCursor(page.next_cursor); setError("");
      page.items.filter(item => item.type !== "turn").forEach(item => loadedIds.current.add(item.id));
    } catch (e) { setError((e as Error).message); } finally { setEarlier(false); }
  }, [earlier, limit, displayItems.length, deferredFind, cursor, id]);
  const saveRequest = (value: PendingRequest | null) => { request.current = value; if (value) localStorage.setItem(`pending:${id}`, JSON.stringify(value)); else localStorage.removeItem(`pending:${id}`); };
  const deliveryMode = mode === "steer" && session?.status !== "active" ? "queue" : mode;
  const send = async () => {
    if ((!draft.trim() && !attachments.files.length) || sending || attachments.uploading > 0) return;
    setSending(true); setSendError(""); setReceipt("");
    const submitted = draft.trim() ? draft : "Please read and inspect the attached files.";
    const submittedFiles = attachments.files.map(a => a.id);
    if (!request.current || request.current.text !== submitted || request.current.mode !== deliveryMode || JSON.stringify(request.current.attachments ?? []) !== JSON.stringify(submittedFiles)) saveRequest({ id: crypto.randomUUID(), text: submitted, mode: deliveryMode, attachments: submittedFiles });
    try {
      const result = await api<Delivery>(`/api/sessions/${id}/messages`, { text: submitted, mode: deliveryMode, request_id: request.current!.id, expected_turn_id: deliveryMode === "steer" ? session?.active_turn_id : undefined, attachments: submittedFiles });
      if (["uncertain", "failed"].includes(result.status)) { if (result.status === "failed") saveRequest(null); throw new Error(result.error || "Delivery could not be confirmed. Check the conversation before retrying."); }
      if (localStorage.getItem(`draft:${id}`) === submitted) localStorage.removeItem(`draft:${id}`);
      const storedFiles = JSON.parse(localStorage.getItem(`attachments:${id}`) ?? "[]") as { id: string }[];
      localStorage.setItem(`attachments:${id}`, JSON.stringify(storedFiles.filter(a => !submittedFiles.includes(a.id))));
      window.dispatchEvent(new CustomEvent("codex:message-sent", { detail: { id, text: submitted, attachments: submittedFiles } }));
      setDraft(current => current === submitted ? "" : current); saveRequest(null); follow.current = true; setFollowing(true);
      setSession(current => current ? { ...current, messages: [...(current.messages ?? []).filter(m => m.id !== result.id), result] } : current);
      setReceipt(result.status === "delivered" ? "Message delivered" : (result.mode ?? deliveryMode) === "steer" ? "Steering pending delivery" : session?.status === "active" ? "Waiting for current work to finish" : "Follow-up iteration queued");
      void refresh(); onChange();
    } catch (e) { setSendError((e as Error).message); }
    finally { setSending(false); textarea.current?.focus(); }
  };
  const cancel = async (messageId: string) => {
    setCancelling(messageId);
    try { await api(`/api/sessions/${id}/messages/${messageId}/cancel`, {}); setReceipt("Queued message removed"); await refresh(); onChange(); }
    catch (e) { setSendError((e as Error).message); } finally { setCancelling(null); }
  };
  const steerQueued = async (messageId: string) => {
    if (!session?.active_turn_id || !session.can_steer_queued) return;
    setSteering(messageId); setSendError("");
    try {
      const result = await api<Delivery>(`/api/sessions/${id}/messages/${messageId}/steer`, { expected_turn_id: session.active_turn_id });
      if (result.status === "delivered") setReceipt("Queued message sent to the active turn");
      else setSendError(result.error || "Steering delivery was not confirmed. Check this message before retrying.");
      void refresh(); onChange();
    } catch (error) { setSendError((error as Error).message); void refresh(); }
    finally { setSteering(null); }
  };
  const canSend = session?.can_send && (deliveryMode !== "steer" || Boolean(session.active_turn_id));
  const stop = async () => {
    setStopping(true); setSendError("");
    try { await api(`/api/sessions/${id}/stop`, {}); setReceipt("Stop requested. Queued follow-ups were removed."); await refresh(); onChange(); }
    catch (e) { setSendError((e as Error).message); } finally { setStopping(false); }
  };
  const runs = useMemo(() => runChanges(items, session?.cwd), [items, session?.cwd]);
  const sources = useMemo(() => collectSources(items), [items]);
  const addSources = useCallback(() => fileInput.current?.click(), []);
  const loadEarlierSources = useCallback(() => { void older(true); }, [older]);
  const resources = useMemo<SessionResources>(() => ({ id, session: detail ?? summary ?? null, children, runs, sources,
    hasEarlier: Boolean(cursor), loadingEarlier: earlier, loadEarlier: loadEarlierSources, addSources }),
    [id, detail, summary, children, runs, sources, cursor, earlier, loadEarlierSources, addSources]);
  useEffect(() => { onResources?.(resources); }, [onResources, resources]);
  const reviewChanges = (runId?: string) => onReviewChanges ? onReviewChanges(runId) : setReviewRun(runId ?? "");
  const latestChanges = runs.filter(run => run.files.length).at(-1);
  const latestFiles = reviewFiles ?? latestChanges?.files;
  const runEndings = useMemo(() => new Map(runs.filter(run => run.files.length).map(run => [run.lastItemId, run])), [runs]);
  const visible = deferredFind ? displayItems : displayItems.slice(-limit);
  const pending = session?.messages?.filter(m => !["delivered", "cancelled"].includes(m.status)) ?? [];
  const jump = () => { follow.current = true; setFollowing(true); scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); };
  return <>
    <header className="session-header"><div className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-line bg-code"><SquareTerminal className="size-[17px] text-muted" /></div><div className="min-w-0 flex-1"><h1 className="truncate text-[14px] font-semibold" title={session?.title}>{session?.title ?? "Loading session..."}</h1><div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-faint"><span title={session?.cwd} className="max-w-60 truncate">{project(session?.cwd ?? "")}</span>{session?.model && <span>{session.model}</span>}{session && <Status status={session.status} />}</div></div><div className="flex shrink-0 gap-0.5">{headerActions}{session?.status === "active" && <button className="secondary-button mr-2 !border-transparent !bg-transparent !text-ink" aria-label="Stop agent" disabled={stopping} onClick={() => void stop()}><Square className="size-3 fill-current" />{stopping ? "Stopping..." : "Stop"}</button>}{!onReviewChanges && <button className="icon-button" aria-label="Review changes" title="Review run changes" onClick={() => reviewChanges(runs.at(-1)?.id)}><FileDiff className="size-4" /></button>}<button className="icon-button" aria-label="Search conversation" title="Find in conversation (Ctrl F)" onClick={() => { setSearchOpen(!searchOpen); requestAnimationFrame(() => search.current?.focus()); }}><Search className="size-4" /></button><button className="icon-button hidden sm:flex" aria-label={expanded ? "Collapse tools" : "Expand tools"} title={expanded ? "Collapse tools" : "Expand tools"} onClick={() => setExpanded(!expanded)}>{expanded ? <ChevronsDownUp className="size-4" /> : <ChevronsUpDown className="size-4" />}</button>{!embedded && <button className="icon-button" aria-label={isPinned ? "Unpin session" : "Pin session"} title={isPinned ? "Unpin session" : "Pin session"} onClick={togglePin}><Pin className={`size-3.5 ${isPinned ? "fill-current" : ""}`} /></button>}<button className={`icon-button ${info ? "bg-hover" : ""}`} aria-label="Session details" aria-expanded={info} title="Session details" onClick={() => setInfo(!info)}><Info className="size-4" /></button></div></header>
    {reviewRun !== null && <Suspense fallback={<Loading label="Opening changes" />}><Changes runs={runs} initialRun={reviewRun} close={() => setReviewRun(null)} /></Suspense>}
    {info && session && <Suspense fallback={<Loading label="Opening session details" />}><SessionDetails key={id} session={session} close={() => setInfo(false)} /></Suspense>}
    {searchOpen && <div className="conversation-search"><Search className="size-3.5 text-faint" /><input ref={search} aria-label="Find in conversation" placeholder="Find in loaded conversation..." value={find} onChange={e => setFind(e.target.value)} onKeyDown={e => { if (e.key === "Enter") navigateMatch(match + (e.shiftKey ? -1 : 1)); }} className="min-w-0 flex-1 bg-transparent text-xs outline-none" /><span className="whitespace-nowrap text-[11px] text-faint">{deferredFind ? matchIds.size ? `${Math.min(match + 1, matchIds.size)} / ${matchIds.size}` : "No matches" : ""}</span><button className="icon-button" aria-label="Previous match" disabled={!matchIds.size} onClick={() => navigateMatch(match - 1)}><ChevronUp className="size-3.5" /></button><button className="icon-button" aria-label="Next match" disabled={!matchIds.size} onClick={() => navigateMatch(match + 1)}><ChevronDown className="size-3.5" /></button><button className="icon-button" aria-label="Close conversation search" onClick={() => { setSearchOpen(false); setFind(""); }}><X className="size-3.5" /></button></div>}
    <div className="relative flex min-h-0 flex-1 flex-col"><div ref={scroller} onClickCapture={event => { if ((event.target as Element).closest("button[aria-expanded], summary")) { follow.current = false; setFollowing(false); } }} onScroll={() => { const el = scroller.current!; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; setFollowing(follow.current); }} className="session-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain">
      <div className="conversation-column pb-8 pt-3">
        {(session?.parent_id || launchChild) && <div className="mb-3 flex items-center justify-between gap-3 text-[11px] text-faint">{session?.parent_id ? <button className="flex items-center gap-1.5 hover:text-ink" onClick={() => select?.(session.parent_id!)}><GitBranch className="size-3.5" />Back to parent</button> : <span />}{launchChild && <button className="flex items-center gap-1.5 hover:text-ink" onClick={launchChild}><GitBranch className="size-3.5" />Launch subagent</button>}</div>}
        {!onResources && <Subagents id={id} agents={children} select={select} />}
        {(cursor || limit < displayItems.length) && <button disabled={earlier} className="earlier-button" onClick={() => void older()}>{earlier ? <Loading label="Loading earlier turns" /> : <><Clock3 className="size-3.5" />{limit < displayItems.length && !deferredFind ? `Show earlier steps (${displayItems.length - limit})` : "Load earlier conversation"}<ChevronUp className="size-3.5" /></>}</button>}
        {(error || warning) && <div className="notice my-3"><Info className="size-4 shrink-0" /><p className="text-xs">{error || warning}</p>{error && <button className="ml-auto text-xs underline" onClick={() => void refresh()}>Retry</button>}</div>}
        {loading && <div className="space-y-5 py-8"><div className="skeleton ml-auto h-20 w-3/4 rounded-2xl" /><div className="space-y-2"><div className="skeleton h-3 w-1/3 rounded" /><div className="skeleton h-3 w-full rounded" /><div className="skeleton h-3 w-5/6 rounded" /></div></div>}
        {!loading && !items.length && !error && <div className="py-14 text-center"><MessageEmpty /><h2 className="mt-4 text-sm font-medium">This conversation is ready</h2><p className="mt-2 text-xs text-faint">Send a message below to continue with Codex.</p></div>}
        <Transcript agents={children} items={visible} metadata={items} expanded={expanded} matchedIds={matchIds} select={select} session={session} after={item => { const changes = runEndings.get(item.id); return changes ? <button type="button" className="run-changes-summary" aria-label={`Review changes for ${changes.prompt || "run"}`} onClick={() => reviewChanges(changes.id)}><FileDiff className="size-4 shrink-0 text-faint" /><span className="text-xs font-medium">{changes.files.length} file{changes.files.length === 1 ? "" : "s"} changed</span><ChangeCounts files={changes.files} /><span className="ml-auto text-xs text-faint">Review<ChevronRight className="ml-1 inline size-3.5" /></span></button> : null; }} />
      </div>
    </div>{!following && <button className="jump-latest" onClick={jump}><ArrowDownToLine className="size-3.5" />Jump to latest</button>}</div>
    <div className="composer-area"><div className="conversation-column">
      {pending.length > 0 && <div className="mb-3 space-y-2">{pending.map(m => <div key={m.id} className="pending-message"><div className="flex items-center gap-2 text-[11px] text-faint"><Clock3 className="size-3" /><span>{m.status === "queued" ? m.mode === "steer" ? "Steering queued" : "After current work" : m.status === "sending" ? m.mode === "steer" ? "Steering..." : "Sending..." : m.status === "uncertain" ? "Delivery not confirmed" : "Delivery failed"}</span>{m.status === "queued" && m.mode === "queue" && <button type="button" aria-label="Steer queued message now" title={!session?.can_steer_queued ? "Available after the pending server update" : session?.active_turn_id ? "Send this message into the current turn" : "An active turn is required for steering"} className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-ink hover:bg-hover disabled:opacity-40" disabled={!session?.can_steer_queued || !session?.can_send || !session.active_turn_id || steering !== null || cancelling === m.id} onClick={() => void steerQueued(m.id)}><ArrowUp className="size-3" />{steering === m.id ? "Steering..." : "Steer now"}</button>}{m.status === "queued" && <button aria-label="Remove queued message" className="icon-button !p-0" disabled={cancelling === m.id || steering === m.id} onClick={() => void cancel(m.id)}><X className="size-3.5" /></button>}</div><p className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-xs">{m.text}</p>{m.error && <p className="mt-2 text-[11px] text-removed-ink">{m.error}</p>}</div>)}</div>}
      {session?.reason && <p className="mb-2 flex items-start gap-1.5 text-[11px] leading-relaxed text-muted"><Info className="mt-0.5 size-3 shrink-0" />{session.reason}</p>}
      {sendError && <p role="alert" className="mb-2 text-xs text-removed-ink">{sendError}</p>}
      {receipt && <p role="status" className="mb-2 flex items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-400"><Check className="size-3.5" />{receipt}</p>}
      <AttachmentList files={attachments.files} remove={artifactId => attachments.setFiles(current => current.filter(a => a.id !== artifactId))} />
      <AttachmentFeedback attachments={attachments} />
      {latestFiles && latestFiles.length > 0 && <div className="mb-3 flex justify-center"><button type="button" className="inline-flex items-center gap-2 rounded-full border border-line bg-panel px-3.5 py-2 text-xs text-muted transition-colors hover:bg-hover" aria-label="Review latest file changes" onClick={() => reviewChanges(reviewFiles ? undefined : latestChanges?.id)}><span>{latestFiles.length} {reviewFiles ? `file change${latestFiles.length === 1 ? "" : "s"}` : `file${latestFiles.length === 1 ? "" : "s"} changed`}</span><ChangeCounts files={latestFiles} /></button></div>}
      <div className="composer-shell-group">
      <BackgroundShells sessionId={id} enabled={active} />
      <div className="composer" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void attachments.upload(e.dataTransfer.files); }}>
        <AttachmentPicker attachments={attachments} inputRef={fileInput} />
        <textarea ref={textarea} aria-label="Message Codex" value={draft} onChange={e => setDraft(e.target.value)} onPaste={e => { const images = Array.from(e.clipboardData.files); if (images.length) { e.preventDefault(); void attachments.upload(images); } }} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (canSend) void send(); } }} placeholder={session?.can_send ? session.status === "active" ? "Message the agent..." : "Continue the conversation" : "Write a draft for this session..."} rows={1} className="block max-h-60 min-h-8 min-w-0 flex-1 resize-none overflow-y-auto bg-transparent py-1 text-[14.5px] leading-6 outline-none placeholder:text-faint" />
        {session?.status === "active" && !draft.trim() && !attachments.files.length ? <button aria-label="Stop agent from composer" title="Stop this agent" disabled={stopping} onClick={() => void stop()} className="send-button"><Square className="size-3 fill-current" /></button> : <button aria-label={deliveryMode === "steer" ? "Steer Codex" : "Queue message"} title={canSend ? "Send (Enter)" : deliveryMode === "steer" ? "Waiting for the active turn" : "This session is not connected"} disabled={!canSend || (!draft.trim() && !attachments.files.length) || sending || attachments.uploading > 0} onClick={() => void send()} className="send-button">{sending ? <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" /> : <ArrowUp className="size-4" />}</button>}
      </div>
      </div>
      <div className="mt-2 flex items-center gap-2"><ContextWindow usage={contextUsage} /><Select label="Delivery mode" value={mode} onChange={value => { setMode(value); localStorage.setItem("delivery-mode", value); setSendError(""); }} className="!border-transparent !bg-transparent !py-1 !text-[11px]" options={[{ value: "steer", label: "Steer now" }, { value: "queue", label: "After current work" }]} /><p className="min-w-0 flex-1 text-center text-[11px] text-faint">Enter sends · Shift + Enter adds a new line</p><span className="hidden shrink-0 text-[10px] text-faint sm:inline">Draft saved</span></div>
    </div></div>
  </>;
}

function ContextWindow({ usage }: { usage: ContextUsage | null }) {
  const tooltipId = useId();
  const trigger = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const open = hovered || focused;
  const used = usage?.last?.total_tokens ?? usage?.last?.input_tokens;
  const window = usage?.model_context_window;
  const known = typeof used === "number" && Number.isFinite(used) && used >= 0 && typeof window === "number" && Number.isFinite(window) && window > 0;
  const percent = known ? Math.round(used / window * 100) : null;
  const label = known ? `${used.toLocaleString()} / ${window.toLocaleString()} tokens used` : "Context usage has not been recorded";
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const bubble = tooltip.current, icon = trigger.current?.getBoundingClientRect();
      if (!bubble || !icon) return;
      const center = icon.left + icon.width / 2;
      const left = Math.max(8, Math.min(center - bubble.offsetWidth / 2, globalThis.innerWidth - bubble.offsetWidth - 8));
      bubble.style.left = `${left}px`;
      bubble.style.top = `${Math.max(8, icon.top - bubble.offsetHeight - 12)}px`;
      bubble.style.setProperty("--tooltip-arrow-x", `${center - left - 1}px`);
    };
    position();
    globalThis.addEventListener("resize", position);
    globalThis.addEventListener("scroll", position, true);
    return () => { globalThis.removeEventListener("resize", position); globalThis.removeEventListener("scroll", position, true); };
  }, [open, label, percent]);
  return <><div ref={trigger} tabIndex={0} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)} role={known ? "meter" : "img"} aria-label="Context window" aria-valuemin={known ? 0 : undefined} aria-valuemax={known ? 100 : undefined} aria-valuenow={percent === null ? undefined : Math.min(100, percent)} aria-valuetext={known ? label : undefined} aria-describedby={tooltipId} className="relative flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-full text-faint outline-none focus-visible:ring-2 focus-visible:ring-faint">
    <svg className="size-[18px] -rotate-90" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="2.5" className="opacity-25" />{percent !== null && <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="2.5" pathLength="100" strokeDasharray={`${Math.min(100, percent)} 100`} strokeLinecap="round" />}</svg>
  </div>{createPortal(<div ref={tooltip} id={tooltipId} role="tooltip" className={`context-tooltip pointer-events-none fixed z-50 w-64 rounded-2xl border border-line bg-panel px-4 py-3 text-center text-xs leading-6 text-ink shadow-xl transition-opacity ${open ? "opacity-100" : "opacity-0"}`}><p className="text-faint">Context window:</p>{percent !== null && <p className="text-muted">{percent}% full</p>}<p>{label}</p>{known && <p className="mt-1 text-[10px] leading-4 text-faint">Latest recorded native usage</p>}</div>, document.body)}</>;
}

function MessageEmpty() { return <div className="mx-auto flex size-12 items-center justify-center rounded-2xl border border-line bg-code"><SquareTerminal className="size-6 text-muted" /></div>; }
