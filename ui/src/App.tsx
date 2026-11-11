import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, ChevronRight, CircleHelp, Command, Cpu, Folder, Layers, Menu, MessageSquare, Moon, Pin, Plus, Search, Settings2, SlidersHorizontal, SquareTerminal, Sun, Unplug, X } from "lucide-react";
import { ago, api, exactTime, label, project, tokens, type Session, type Snapshot } from "./api";
import { Resizer, useSidebarWidth } from "./components/Resizer";
import { CopyButton, Dialog, Status } from "./components/shared";
import { SessionView } from "./components/SessionView";
import { Launch } from "./components/Launch";
import { Select } from "./components/Select";
import { Models } from "./components/Models";
import { ActivityLine } from "./components/Activity";
import { orderSessions } from "./sessionOrder";

function saved<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback; } catch { return fallback; }
}

export function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(() => location.pathname.match(/^\/sessions\/(.+)$/)?.[1] ?? null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState(() => localStorage.getItem("session-source") ?? "claude");
  const [status, setStatus] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [archived, setArchived] = useState(false);
  const [sidebarView, setSidebarView] = useState<"projects" | "recent">(() => localStorage.getItem("session-sidebar-view") === "recent" ? "recent" : "projects");
  const [pins, setPins] = useState<string[]>(() => saved("session-pins", []));
  const [collapsed, setCollapsed] = useState<string[]>(() => saved("collapsed-projects", []));
  const [dark, setDark] = useState(document.documentElement.classList.contains("dark"));
  const [width, setWidth] = useSidebarWidth();
  const [mobile, setMobile] = useState(false);
  const [dialog, setDialog] = useState<"setup" | "help" | "launch" | "models" | null>(null);
  const [launchParent, setLaunchParent] = useState<Session | undefined>();
  const search = useRef<HTMLInputElement>(null);
  const positions = useRef<ReturnType<typeof orderSessions>["positions"]>(new Map());
  const acceptSnapshot = useCallback((data: Snapshot) => {
    const ordered = orderSessions(data.sessions, positions.current);
    positions.current = ordered.positions;
    setSnapshot({ ...data, sessions: ordered.sessions }); setError("");
  }, []);
  const refresh = useCallback(() => api<Snapshot>("/api/sessions").then(acceptSnapshot).catch(e => setError(e.message)), [acceptSnapshot]);
  useEffect(() => {
    const events = new EventSource("/api/events");
    let stopped = false;
    events.onmessage = event => {
      if (stopped) return;
      if (event.data === "refresh") { void refresh(); return; }
      try { acceptSnapshot(JSON.parse(event.data)); } catch { setError("Could not read a live update. Reconnecting..."); }
    };
    events.onerror = () => setError("Connection interrupted. Your drafts are saved. Reconnecting...");
    return () => { stopped = true; events.close(); };
  }, [refresh]);
  useEffect(() => { document.documentElement.classList.toggle("dark", dark); localStorage.setItem("theme", dark ? "dark" : "light"); }, [dark]);
  useEffect(() => { localStorage.setItem("session-source", source); }, [source]);
  useEffect(() => { localStorage.setItem("session-sidebar-view", sidebarView); }, [sidebarView]);
  useEffect(() => { localStorage.setItem("session-pins", JSON.stringify(pins)); }, [pins]);
  useEffect(() => { localStorage.setItem("collapsed-projects", JSON.stringify(collapsed)); }, [collapsed]);
  const go = useCallback((id: string | null) => {
    setSelected(id); setMobile(false);
    history.pushState(null, "", id ? `/sessions/${encodeURIComponent(id)}` : "/");
    if (!id) document.title = "Claudex";
  }, []);
  const sessions = snapshot?.sessions ?? [];
  const shown = useMemo(() => {
    const text = query.trim().toLowerCase();
    return sessions.filter(s => s.archived === archived && (source === "all" || s.source === source || source === "claude" && s.source === "agents") && (!status || s.status === status) && (!workspace || s.cwd === workspace) && [s.title, s.preview, s.cwd, s.model, s.id].some(v => v.toLowerCase().includes(text)));
  }, [sessions, query, source, status, workspace, archived]);
  const navigation = shown;
  const projects = useMemo(() => [...new Set(sessions.filter(s => s.archived === archived && (source === "all" || s.source === source || source === "claude" && s.source === "agents")).map(s => s.cwd))].sort(), [sessions, source, archived]);
  const groups = useMemo(() => {
    const map = new Map<string, Session[]>();
    for (const s of shown.filter(s => !pins.includes(s.id))) { if (!map.has(s.cwd)) map.set(s.cwd, []); map.get(s.cwd)!.push(s); }
    return [...map.entries()];
  }, [shown, pins]);
  const togglePin = (id: string) => setPins(current => current.includes(id) ? current.filter(p => p !== id) : [...current, id]);
  useEffect(() => {
    const back = () => setSelected(location.pathname.match(/^\/sessions\/(.+)$/)?.[1] ?? null);
    const key = (e: KeyboardEvent) => {
      if (e.defaultPrevented || document.querySelector("dialog[open]")) return;
      const typing = (e.target as HTMLElement)?.matches("input,textarea,select,[contenteditable]");
      if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); setMobile(true); requestAnimationFrame(() => search.current?.focus()); }
      else if (e.altKey && e.code === "KeyH") { e.preventDefault(); go(null); }
      else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown") && navigation.length) {
        e.preventDefault(); const index = navigation.findIndex(s => s.id === selected); go(navigation[Math.max(0, Math.min(navigation.length - 1, index + (e.key === "ArrowDown" ? 1 : -1)))].id);
      } else if (e.key === "?" && !typing) setDialog("help");
      else if (e.key === "Escape" && !dialog) { setFiltersOpen(false); setMobile(false); }
    };
    window.addEventListener("popstate", back); window.addEventListener("keydown", key);
    return () => { window.removeEventListener("popstate", back); window.removeEventListener("keydown", key); };
  }, [navigation, selected, go, dialog]);
  const clear = () => { setQuery(""); setStatus(""); setWorkspace(""); };
  return <div className="flex h-dvh overflow-hidden bg-surface text-ink">
    {mobile && <button className="fixed inset-0 z-20 bg-black/35 md:hidden" aria-label="Close sidebar" onClick={() => setMobile(false)} />}
    <aside className={`sidebar ${mobile ? "flex" : "hidden"} md:flex`} style={{ width }}>
      <Resizer width={width} setWidth={setWidth} label="Sidebar width" />
      <div className="flex items-center gap-2 px-3 pb-2 pt-3">
        <div className="flex size-7 items-center justify-center rounded-lg bg-ink text-surface"><SquareTerminal className="size-4" /></div>
        <button onClick={() => go(null)} className="text-[13px] font-semibold tracking-tight">Claudex</button>
        <button className="icon-button ml-auto" aria-label="Switch theme" title="Switch theme" onClick={() => setDark(!dark)}>{dark ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}</button>
        <button className="icon-button md:hidden" aria-label="Close navigation" onClick={() => setMobile(false)}><X className="size-4" /></button>
      </div>
      <div className="flex items-center gap-1 px-2 pb-2">
        <button className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-medium ${selected === null ? "bg-selected text-ink" : "text-muted hover:bg-hover"}`} onClick={() => go(null)}><Layers className="size-3.5" /><span>Overview</span><span aria-hidden className="ml-auto text-[10px] text-faint">{shown.length}</span></button>
        <button className="icon-button !size-8" aria-label="New agent" title="New agent" onClick={() => { setLaunchParent(undefined); setDialog("launch"); }}><Plus className="size-4" /></button>
      </div>
      <div className="space-y-2 px-3 pb-2">
        <div className="flex items-center gap-1">
          <div className="search-field min-w-0 flex-1 !py-1.5"><Search className="size-3.5 shrink-0 text-faint" /><input ref={search} className="min-w-0 flex-1 bg-transparent text-xs outline-none" aria-label="Search sessions" placeholder="Search sessions..." value={query} onChange={e => setQuery(e.target.value)} />{query ? <button className="icon-button !p-0" aria-label="Clear session search" onClick={() => setQuery("")}><X className="size-3.5" /></button> : <kbd className="shortcut !text-[9px]">Ctrl K</kbd>}</div>
          <button className={`icon-button relative !size-8 ${filtersOpen || status || workspace ? "bg-hover text-ink" : ""}`} aria-label="Session filters" aria-expanded={filtersOpen} aria-controls="sidebar-filters" title={`Filters${status || workspace ? ` (${Number(Boolean(status)) + Number(Boolean(workspace))} active)` : ""}`} onClick={() => setFiltersOpen(!filtersOpen)}><SlidersHorizontal className="size-3.5" />{(status || workspace) && <span aria-hidden className="absolute right-0 top-0 flex size-3.5 items-center justify-center rounded-full bg-ink text-[9px] text-surface">{Number(Boolean(status)) + Number(Boolean(workspace))}</span>}</button>
        </div>
        <div className="flex items-center gap-1.5">
          <Select label="Session source" value={source} onChange={value => { setSource(value); setWorkspace(""); }} className="min-w-0 flex-1 !border-transparent !bg-transparent !px-2 !py-1 !text-[11px]" options={[{ value: "claude", label: "Agents" }, { value: "all", label: "All sessions" }]} />
          <div className="segmented shrink-0 !text-[10.5px]" role="group" aria-label="Session view">
            <button aria-pressed={sidebarView === "projects"} onClick={() => setSidebarView("projects")} className={`flex items-center justify-center gap-1 !px-2 !py-1 ${sidebarView === "projects" ? "selected" : ""}`}>Projects</button>
            <button aria-pressed={sidebarView === "recent"} onClick={() => setSidebarView("recent")} className={`flex items-center justify-center gap-1 !px-2 !py-1 ${sidebarView === "recent" ? "selected" : ""}`}>Recent</button>
          </div>
        </div>
        {filtersOpen && <div id="sidebar-filters" role="region" aria-label="Session filters" className="grid grid-cols-2 gap-1.5 rounded-lg border border-line bg-code p-2">
          <Select label="Filter by project" value={workspace} onChange={setWorkspace} className="min-w-0 !px-2 !py-1 !text-[11px]" options={[{ value: "", label: "All projects" }, ...projects.map(p => ({ value: p, label: project(p) }))]} />
          <Select label="Filter by status" value={status} onChange={setStatus} className="min-w-0 !px-2 !py-1 !text-[11px]" options={[{ value: "", label: "All statuses" }, ...["active", "idle", "saved", "external", "unavailable", "systemError"].map(s => ({ value: s, label: label(s) }))]} />
          {(status || workspace) && <button className="col-span-2 justify-self-end px-1 py-1 text-[10px] text-faint hover:text-ink" onClick={() => { setStatus(""); setWorkspace(""); }}>Reset filters</button>}
        </div>}
      </div>
      <nav aria-label="Sessions" className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {!snapshot && <div className="space-y-3 px-3 pt-4">{[1, 2, 3, 4].map(i => <div key={i} className="skeleton h-12 rounded-lg" />)}</div>}
        {sidebarView === "recent" ? navigation.map(s => <SessionRow key={s.id} session={s} selected={selected} go={go} pinned={pins.includes(s.id)} togglePin={togglePin} />) : <>
          {shown.some(s => pins.includes(s.id)) && <section className="mb-3"><div className="project-heading"><Pin className="size-3" /><span>Pinned</span></div>{shown.filter(s => pins.includes(s.id)).map(s => <SessionRow key={s.id} session={s} selected={selected} go={go} pinned togglePin={togglePin} />)}</section>}
          {groups.map(([cwd, entries]) => <section key={cwd} className="mb-2"><button onClick={() => setCollapsed(current => current.includes(cwd) ? current.filter(p => p !== cwd) : [...current, cwd])} className="project-heading w-full hover:text-ink" aria-expanded={!collapsed.includes(cwd)} title={cwd}><ChevronRight className={`size-3 transition-transform ${collapsed.includes(cwd) ? "" : "rotate-90"}`} /><Folder className="size-3.5" /><span className="min-w-0 truncate">{project(cwd)}</span><span className="ml-auto tabular-nums">{entries.length}</span></button>{!collapsed.includes(cwd) && entries.map(s => <SessionRow key={s.id} session={s} selected={selected} go={go} pinned={false} togglePin={togglePin} />)}</section>)}
        </>}
        {snapshot && !shown.length && <div className="px-4 py-8 text-center"><Search className="mx-auto mb-3 size-5 text-faint" /><p className="text-sm font-medium">No matching sessions</p><p className="mt-1 text-xs leading-relaxed text-faint">Try another search or change your filters.</p>{(query || status || workspace) && <button className="mt-3 text-xs underline underline-offset-4" onClick={clear}>Clear filters</button>}</div>}
      </nav>
      <div className="sidebar-footer"><button className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => setDialog("setup")} title="Connection and Claude setup"><span className={`size-1.5 shrink-0 rounded-full ${snapshot?.connection.connected ? "bg-emerald-500" : "bg-amber-500"}`} /><div className="min-w-0"><div className="truncate text-xs font-medium">{snapshot?.connection.connected ? "Local Codex connected" : "Codex disconnected"}</div><div className="mt-0.5 truncate text-[10px] text-faint" title={snapshot?.connection.home}>{snapshot?.connection.home ?? "Connecting to your workspace..."}</div></div></button><button className="icon-button" title="Models and Claude task tiers" aria-label="Models and Claude task tiers" onClick={() => setDialog("models")}><Cpu className="size-3.5" /></button><button className="icon-button" title="Keyboard shortcuts" aria-label="Keyboard shortcuts" onClick={() => setDialog("help")}><CircleHelp className="size-3.5" /></button><button className="icon-button" title="Connection and setup" aria-label="Connection and setup" onClick={() => setDialog("setup")}><Settings2 className="size-3.5" /></button></div>
    </aside>
    <main className="flex min-w-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3 md:hidden"><button aria-label="Open sidebar" onClick={() => setMobile(true)}><Menu className="size-5" /></button><SquareTerminal className="size-4" /><span className="text-sm font-semibold">Claudex</span></div>
      {error && <div role="status" className="flex items-center gap-2 border-b border-line bg-code px-5 py-2 text-xs text-muted"><Unplug className="size-3.5" />{error}<button className="ml-auto underline underline-offset-2" onClick={() => void refresh()}>Retry</button></div>}
      {selected ? <SessionView key={selected} id={selected} summary={sessions.find(s => s.id === selected)} connected={snapshot?.connection.connected} onChange={refresh} pinned={pins.includes(selected)} togglePin={() => togglePin(selected)} select={go} launchChild={() => { setLaunchParent(sessions.find(s => s.id === selected)); setDialog("launch"); }} /> : <Overview sessions={shown} loading={!snapshot} onSelect={go} connection={snapshot?.connection} setup={() => setDialog("setup")} archived={archived} setArchived={setArchived} filtered={Boolean(query || status || workspace)} clear={clear} />}
    </main>
    {dialog === "setup" && <Setup connection={snapshot?.connection} close={() => setDialog(null)} />}
    {dialog === "launch" && <Launch parent={launchParent} close={() => setDialog(null)} launched={id => { setDialog(null); void refresh(); go(id); }} />}
    {dialog === "models" && <Models close={() => setDialog(null)} />}
    {dialog === "help" && <Dialog title="Keyboard shortcuts" close={() => setDialog(null)}><div className="space-y-4 p-6">{[["Search sessions", "Ctrl / Cmd K"], ["Search this conversation", "Ctrl / Cmd F"], ["Previous / next session", "Alt ↑ / ↓"], ["Go to overview", "Alt H"], ["Send message", "Enter"], ["New line", "Shift Enter"], ["Close search or dialog", "Esc"]].map(([name, shortcut]) => <div key={name} className="flex items-center justify-between gap-8 text-sm"><span className="text-muted">{name}</span><kbd className="shortcut">{shortcut}</kbd></div>)}</div></Dialog>}
  </div>;
}

function SessionRow({ session: s, selected, go, pinned, togglePin }: { session: Session; selected: string | null; go: (id: string) => void; pinned: boolean; togglePin: (id: string) => void }) {
  return <div className={`session-row group/row ${s.id === selected ? "bg-selected" : "hover:bg-hover"}`}>
    <button aria-current={s.id === selected ? "page" : undefined} onClick={() => go(s.id)} className="min-w-0 flex-1 text-left" title={s.title}><div className="flex items-center gap-2"><Status status={s.status} compact phase={s.activity?.phase} /><span className="min-w-0 truncate text-[13px] font-medium">{s.title}</span></div>{s.status === "active" && <div className="pl-3.5"><ActivityLine session={s} /></div>}<div className="mt-1.5 flex items-center gap-2 pl-3.5 text-[10.5px] text-faint"><span className="min-w-0 truncate">{s.model || "Codex"}</span><span className="ml-auto shrink-0" title={exactTime(s.updated_at)}>{ago(s.updated_at)}</span></div></button>
    <button className={`pin-button ${pinned ? "opacity-60" : "opacity-0 group-hover/row:opacity-60 focus:opacity-100"}`} title={pinned ? "Unpin session" : "Pin session"} aria-label={pinned ? "Unpin session" : "Pin session"} onClick={() => togglePin(s.id)}><Pin className={`size-3 ${pinned ? "fill-current" : ""}`} /></button>
  </div>;
}

function Overview({ sessions, loading, onSelect, connection, setup, archived, setArchived, filtered, clear }: { sessions: Session[]; loading: boolean; onSelect: (id: string) => void; connection?: Snapshot["connection"]; setup: () => void; archived: boolean; setArchived: (value: boolean) => void; filtered: boolean; clear: () => void }) {
  const active = sessions.filter(s => s.status === "active");
  const totalTokens = sessions.reduce((sum, s) => sum + (s.tokens ?? 0), 0);
  return <div className="min-h-0 flex-1 overflow-y-auto"><div className="mx-auto max-w-[1500px] px-6 py-7 lg:px-9 lg:py-9">
    <div className="mb-7 flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-[23px] font-semibold tracking-tight">Your local workspace</h1><p className="mt-1.5 text-[13px] text-muted">Follow the work. Read every conversation. Keep your agents moving.</p></div><button onClick={setup} className="secondary-button"><SquareTerminal className="size-3.5" />Connect Claude<ArrowUpRight className="size-3.5 text-faint" /></button></div>
    {connection && !connection.connected && <div className="notice mb-6"><Unplug className="size-4 shrink-0" /><div><p className="font-medium">Showing saved conversations</p><p className="mt-1 text-xs text-muted">{connection.error || "Reconnect the Codex daemon to resume live updates and messaging."}</p></div><button className="ml-auto shrink-0 text-xs underline" onClick={setup}>View connection</button></div>}
    <div className="mb-7 grid grid-cols-2 gap-3 xl:grid-cols-4">{[["Sessions", sessions.length, MessageSquare], ["Working now", active.length, Command], ["Projects", new Set(sessions.map(s => s.cwd)).size, Folder], ["Recorded tokens", totalTokens ? tokens(totalTokens) : "0", Layers]].map(([name, value, Icon]) => { const Component = Icon as typeof Folder; return <div key={String(name)} className="metric"><div className="flex items-center justify-between text-xs text-faint"><span>{String(name)}</span><Component className="size-3.5 opacity-70" /></div><div className="mt-3 text-[27px] font-semibold leading-none tracking-tight tabular-nums">{loading ? <span className="skeleton inline-block h-7 w-12 rounded" /> : String(value)}</div></div>; })}</div>
    {active.length > 0 && <section className="mb-7"><h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><span className="size-1.5 rounded-full bg-emerald-500" />Working now<span className="font-normal text-faint">{active.length}</span></h2><div className="grid gap-3 lg:grid-cols-2">{active.map(s => <button key={s.id} className="active-card" onClick={() => onSelect(s.id)}><div className="flex items-center justify-between gap-3"><span className="truncate text-sm font-medium">{s.title}</span><ArrowUpRight className="size-4 shrink-0 text-faint" /></div><p className="mt-2 truncate text-xs text-faint">{project(s.cwd)} · {s.model}</p><ActivityLine session={s} /><p className="mt-2 truncate font-mono text-[10px] text-faint" title={s.activity?.detail}>{s.activity?.detail}</p><div className="mt-3 flex items-center justify-between"><Status status={s.status} /><span className="text-[11px] text-faint">{s.flags?.includes("waitingOnApproval") ? "Waiting for approval" : "View live conversation"}</span></div></button>)}</div></section>}
    <section className="overflow-hidden rounded-xl border border-line bg-panel"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5"><h2 className="flex items-center gap-2 text-sm font-semibold"><MessageSquare className="size-4 text-faint" />{archived ? "Archived sessions" : "Recent sessions"}<span className="text-xs font-normal text-faint">{sessions.length}</span></h2><label className="flex items-center gap-2 text-xs text-faint"><input type="checkbox" className="accent-ink" checked={archived} onChange={e => setArchived(e.target.checked)} />Show archived</label></div>
      {loading ? <div className="space-y-4 p-5">{[1, 2, 3].map(i => <div key={i} className="skeleton h-12 rounded-lg" />)}</div> : sessions.length ? <div>{sessions.map(s => <button key={s.id} className="overview-row" onClick={() => onSelect(s.id)}><div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-line bg-code"><SquareTerminal className="size-4 text-muted" /></div><div className="min-w-0 flex-1"><p className="truncate text-[13px] font-medium">{s.title}</p><div className="mt-1 flex items-center gap-2 text-[11px] text-faint"><span className="truncate">{project(s.cwd)}</span><span aria-hidden>·</span><span className="truncate">{s.model || "Codex"}</span></div></div><div className="hidden w-28 shrink-0 sm:block"><Status status={s.status} /></div><span className="w-20 shrink-0 text-right text-[11px] text-faint" title={exactTime(s.updated_at)}>{ago(s.updated_at)}</span><ChevronRight className="size-3.5 shrink-0 text-faint" /></button>)}</div> : <div className="flex flex-col items-center px-6 py-16 text-center"><div className="mb-4 flex size-12 items-center justify-center rounded-2xl border border-line bg-code"><SquareTerminal className="size-6 text-muted" /></div><h3 className="text-sm font-semibold">{filtered ? "No sessions match your filters" : archived ? "No archived sessions" : "Your next agent starts here"}</h3><p className="mt-2 max-w-sm text-xs leading-relaxed text-muted">{filtered ? "Try a different search or select another project." : "Connect Claude Code to see its Codex agents and join their conversations as they work."}</p><button className="secondary-button mt-5" onClick={filtered ? clear : setup}>{filtered ? "Clear filters" : "Connect Claude"}</button></div>}
    </section><p className="mt-5 flex items-center gap-1.5 text-[11px] text-faint"><span className={`size-1 rounded-full ${connection?.connected ? "bg-emerald-500" : "bg-faint"}`} />{connection?.connected ? "Live updates are on. Your place in the conversation is preserved." : "Your saved sessions remain available while disconnected."}</p>
  </div></div>;
}

function Setup({ connection, close }: { connection?: Snapshot["connection"]; close: () => void }) {
  const [config, setConfig] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api<{ config: unknown }>("/api/setup").then(data => setConfig(JSON.stringify(data.config, null, 2))).catch(e => setError(e.message)); }, []);
  return <Dialog title="Connect Claude Code" close={close}><div className="space-y-5 p-6"><p className="text-sm leading-relaxed text-muted">Give Claude a shared place to launch Codex agents. New sessions appear here with live history, queued messages and steering.</p><div className="rounded-lg border border-line bg-code p-3"><div className="mb-2 flex items-center gap-2 text-xs font-medium"><span className={`size-1.5 rounded-full ${connection?.connected ? "bg-emerald-500" : "bg-amber-500"}`} />{connection?.connected ? "Connected to your local Codex daemon" : "Codex daemon is disconnected"}</div><p className="break-all font-mono text-[11px] text-faint">{connection?.home}</p>{connection?.error && <p className="mt-2 break-words text-xs text-muted">{connection.error}</p>}</div><div><h3 className="mb-2 text-sm font-medium">1. Add this MCP configuration in Claude Code</h3><div className="relative rounded-lg border border-line bg-code"><div className="absolute right-2 top-2"><CopyButton text={config} label="Copy Claude configuration" /></div><pre className="max-h-64 overflow-auto p-4 pr-12 font-mono text-[11px] leading-relaxed">{config || error || "Loading configuration..."}</pre></div></div><div><h3 className="text-sm font-medium">2. Reconnect the Codex MCP server in Claude</h3><p className="mt-1.5 text-xs leading-relaxed text-muted">Keep this dashboard running, then reconnect the MCP server or restart Claude Code. Your next Codex agent will appear under From Claude.</p></div><p className="text-[11px] leading-relaxed text-faint">Existing sessions are already discoverable. Sessions owned by an older, separate process can be read here; live messaging requires the shared connection.</p></div></Dialog>;
}
