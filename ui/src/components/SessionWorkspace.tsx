import { lazy, Suspense, useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { ChevronRight, FileDiff, FileText, Flower2, GitBranch, Link2, Maximize2, Minimize2, PanelRight, Plus, X } from "lucide-react";
import { ago, exactTime, project, type Child, type Session } from "../api";
import type { SessionSource } from "../sessionSources";
import { ChangeCounts } from "./Diff";
import { FileLink, FileReferenceScope } from "./FileLinks";
import { Loading, Status } from "./shared";
import { SessionView, type SessionResources, type SessionViewProps } from "./SessionView";

const Changes = lazy(() => import("./Changes").then(module => ({ default: module.Changes })));
const FilePreview = lazy(() => import("./FilePreview").then(module => ({ default: module.FilePreview })));
type Tab = { key: string; kind: "agents" | "sources" | "changes" | "agent"; sessionId: string; runId?: string };
const working = (agent: Child) => agent.status === "active" || agent.status === "starting";
const sessionTitle = (tab: Tab, sessions: Session[], resources: Record<string, SessionResources>) => resources[tab.sessionId]?.session?.title || sessions.find(session => session.id === tab.sessionId)?.title || Object.values(resources).flatMap(value => value.children).find(child => child.thread_id === tab.sessionId)?.name || "Agent";
const tabTitle = (tab: Tab, sessions: Session[], resources: Record<string, SessionResources>, rootId: string) => tab.kind === "agent" ? sessionTitle(tab, sessions, resources) : ({ agents: "Subagents", sources: "Sources", changes: "Changes" }[tab.kind] + (tab.sessionId === rootId ? "" : ` · ${sessionTitle(tab, sessions, resources)}`));
const tabIcon = (kind: Tab["kind"]) => kind === "changes" ? FileDiff : kind === "sources" ? Link2 : kind === "agent" ? Flower2 : GitBranch;

export function SessionWorkspace({ sessions, ...props }: SessionViewProps & { sessions: Session[] }) {
  const [resources, setResources] = useState<Record<string, SessionResources>>({});
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [rail, setRail] = useState(() => window.innerWidth >= 1200);
  const [menu, setMenu] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const [split, setSplit] = useState(50);
  const [focus, setFocus] = useState<"main" | "dock">("main");
  const [preview, setPreview] = useState<{ source: SessionSource; sessionId: string } | null>(null);
  const layout = useRef<HTMLDivElement>(null);
  useEffect(() => {
    layout.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.closest<HTMLElement>(".workspace-tab")?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [selected]);
  useEffect(() => () => document.body.classList.remove("resizing"), []);
  useEffect(() => {
    if (!menu) return;
    const dismiss = (event: PointerEvent) => { if (!(event.target as Element).closest(".workspace-add-tab")) setMenu(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [menu]);
  const root = resources[props.id];
  const active = tabs.find(tab => tab.key === selected);
  const agentIds = [...new Set(tabs.map(tab => tab.sessionId).filter(id => id !== props.id))];
  const accept = useCallback((value: SessionResources) => setResources(current => current[value.id] === value ? current : { ...current, [value.id]: value }), []);
  const open = (kind: Tab["kind"], sessionId = props.id, runId?: string) => {
    const tab = { key: `${kind}:${sessionId}`, kind, sessionId, runId };
    setTabs(current => current.some(value => value.key === tab.key) ? current.map(value => value.key === tab.key && runId !== undefined ? tab : value) : [...current, tab]);
    setSelected(tab.key); setMenu(false); setFocus("dock");
  };
  const openAgent = (id: string) => {
    if (id === props.id) { setSelected(null); setMaximized(false); setFocus("main"); return; }
    open("agent", id);
  };
  const attachSources = (sessionId: string) => {
    openAgent(sessionId);
    resources[sessionId]?.addSources();
  };
  const closeTab = (key: string) => {
    const index = tabs.findIndex(tab => tab.key === key);
    const remaining = tabs.filter(tab => tab.key !== key);
    setTabs(remaining);
    if (selected === key) setSelected(remaining[Math.max(0, index - 1)]?.key ?? null);
    if (!remaining.length) { setMaximized(false); setFocus("main"); }
  };
  const moveTab = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex(tab => tab.key === selected);
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault(); setSelected(tabs[next].key);
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };
  const toggleResources = () => { if (active) { setSelected(null); setMaximized(false); setRail(true); } else setRail(value => !value); };
  return <div ref={layout} className={`session-workspace ${active ? "workspace-split" : ""} ${maximized ? "workspace-maximized" : ""} ${rail ? "workspace-rail-open" : ""}`} style={{ "--workspace-split": `${split}%` } as CSSProperties}>
    <section className="workspace-main" aria-label="Parent conversation" onFocusCapture={() => setFocus("main")} onPointerDownCapture={() => setFocus("main")}>
      <div className="workspace-conversation" data-session-pane={props.id}>
        <SessionView {...props} keyboardActive={focus === "main"} onResources={accept} onReviewChanges={run => open("changes", props.id, run)} select={openAgent}
          headerActions={<button type="button" className="icon-button" aria-label="Session resources" aria-expanded={!active && rail} title="Session resources" onClick={toggleResources}><PanelRight className="size-4" /></button>} />
      </div>
      <aside className="workspace-resources" aria-label="Session resources">
        <ResourceCard resources={root} session={props.summary} open={kind => open(kind)} addAgent={props.launchChild} preview={source => setPreview({ source, sessionId: props.id })} />
      </aside>
    </section>
    {active && <>
      <div role="separator" tabIndex={0} aria-label="Conversation panel width" aria-orientation="vertical" aria-valuemin={30} aria-valuemax={70} aria-valuenow={split} className="workspace-divider"
        onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); document.body.classList.add("resizing"); }}
        onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId) && layout.current) { const rect = layout.current.getBoundingClientRect(); setSplit(Math.max(30, Math.min(70, (event.clientX - rect.left) / rect.width * 100))); } }}
        onPointerUp={event => { event.currentTarget.releasePointerCapture(event.pointerId); document.body.classList.remove("resizing"); }}
        onLostPointerCapture={() => document.body.classList.remove("resizing")} onDoubleClick={() => setSplit(50)}
        onKeyDown={event => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); setSplit(event.key === "Home" ? 30 : event.key === "End" ? 70 : Math.max(30, Math.min(70, split + (event.key === "ArrowLeft" ? -2 : 2)))); } }} />
      <aside className="workspace-dock" aria-label="Session tabs" onFocusCapture={() => setFocus("dock")} onPointerDownCapture={() => setFocus("dock")}>
        <div className="workspace-tabbar">
          <div role="tablist" aria-label="Session panels" className="workspace-tabs" onKeyDown={moveTab}>{tabs.map(tab => {
            const Icon = tabIcon(tab.kind), title = tabTitle(tab, sessions, resources, props.id);
            return <div key={tab.key} className={`workspace-tab ${selected === tab.key ? "is-selected" : ""}`}>
              <button type="button" role="tab" id={`tab-${tab.key}`} aria-controls={`panel-${tab.key}`} aria-selected={selected === tab.key} tabIndex={selected === tab.key ? 0 : -1} title={title} onClick={() => { setSelected(tab.key); setMenu(false); }}><Icon className="size-3.5 shrink-0" /><span>{title}</span></button>
              <button type="button" aria-label={`Close ${title} tab`} className="workspace-tab-close" onClick={() => closeTab(tab.key)}><X className="size-3" /></button>
            </div>;
          })}</div>
          <div className="workspace-add-tab"><button type="button" className="icon-button" aria-label="Open a tab" aria-expanded={menu} onClick={() => setMenu(!menu)}><Plus className="size-4" /></button>{menu && <div className="workspace-tab-menu" role="menu" onKeyDown={event => { if (event.key === "Escape") setMenu(false); }}>{(["agents", "sources", "changes"] as const).map(kind => <button type="button" role="menuitem" key={kind} onClick={() => open(kind, active.sessionId)}>{kind === "agents" ? "Subagents" : kind === "sources" ? "Sources" : "Changes"}</button>)}</div>}</div>
          <button type="button" className="icon-button ml-auto" aria-label={maximized ? "Restore split view" : "Maximize panel"} onClick={() => setMaximized(!maximized)}>{maximized ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}</button>
          <button type="button" className="icon-button" aria-label="Close side panel" onClick={() => { setSelected(null); setMaximized(false); setFocus("main"); }}><PanelRight className="size-4" /></button>
        </div>
        {agentIds.map(id => <div key={id} id={`panel-agent:${id}`} role="tabpanel" aria-labelledby={tabs.some(tab => tab.key === `agent:${id}`) ? `tab-agent:${id}` : undefined} hidden={selected !== `agent:${id}`} className="workspace-tabpanel">
          <div className="workspace-agent" data-session-pane={id}><SessionView id={id} summary={sessions.find(session => session.id === id)} connected={props.connected} onChange={props.onChange} pinned={false} togglePin={() => {}} embedded active={active.sessionId === id} keyboardActive={focus === "dock" && selected === `agent:${id}`} onResources={accept} onReviewChanges={run => open("changes", id, run)} select={openAgent}
            headerActions={<button type="button" className="icon-button" aria-label="Agent resources" title="Agent resources" onClick={() => open("agents", id)}><PanelRight className="size-4" /></button>} /></div>
        </div>)}
        {tabs.filter(tab => tab.kind !== "agent").map(tab => <div key={tab.key} id={`panel-${tab.key}`} role="tabpanel" aria-labelledby={`tab-${tab.key}`} hidden={selected !== tab.key} className="workspace-tabpanel">
          {tab.kind === "agents" ? <AgentsPanel id={tab.sessionId} resources={resources[tab.sessionId]} sessions={sessions} select={openAgent} />
            : tab.kind === "sources" ? <SourcesPanel resources={resources[tab.sessionId]} sessionId={tab.sessionId} addSources={() => attachSources(tab.sessionId)} preview={source => setPreview({ source, sessionId: tab.sessionId })} />
              : <Suspense fallback={<div className="p-5"><Loading label="Opening changes" /></div>}><Changes key={tab.runId ?? "latest"} embedded runs={resources[tab.sessionId]?.runs ?? []} initialRun={tab.runId} close={() => closeTab(tab.key)} /></Suspense>}
        </div>)}
      </aside>
    </>}
    {preview?.source.file && <FileReferenceScope sessionId={preview.sessionId} itemId={preview.source.itemId}><Suspense fallback={<Loading label="Opening source" />}><FilePreview file={preview.source.file} close={() => setPreview(null)} /></Suspense></FileReferenceScope>}
  </div>;
}

function AgentAvatar({ id }: { id: string }) {
  const color = ["#ce77a0", "#d4bb51", "#cd8075", "#9c7dcc", "#68af99", "#7b9bcc"][[...id].reduce((sum, letter) => sum + letter.charCodeAt(0), 0) % 6];
  return <Flower2 aria-hidden className="agent-avatar size-5 shrink-0" style={{ color, fill: color }} />;
}

function ResourceCard({ resources, session, open, addAgent, preview }: { resources?: SessionResources; session?: Session; open: (kind: "changes" | "agents" | "sources") => void; addAgent?: () => void; preview: (source: SessionSource) => void }) {
  const agents = resources?.children ?? [], sources = resources?.sources ?? [], latest = resources?.runs.filter(run => run.files.length).at(-1);
  const count = agents.filter(working).length;
  return <div className="resource-card">
    <div className="resource-project" title={resources?.session?.cwd || session?.cwd}>{project(resources?.session?.cwd || session?.cwd || "")}</div>
    <button type="button" className="resource-changes" onClick={() => open("changes")}><FileDiff className="size-3.5" /><span>Changes</span>{latest && <ChangeCounts files={latest.files} />}<ChevronRight className="ml-auto size-3.5 text-faint" /></button>
    <div className="resource-section"><div className="resource-heading"><button type="button" onClick={() => open("agents")}>Subagents</button>{addAgent && <button type="button" className="icon-button" aria-label="Launch subagent" title="Launch subagent" onClick={addAgent}><Plus className="size-3.5" /></button>}</div>
      <button type="button" className="resource-agent-summary" aria-label={`Open subagents (${agents.length})`} onClick={() => open("agents")}><span className="resource-avatars">{agents.slice(0, 4).map(agent => <AgentAvatar key={agent.thread_id} id={agent.thread_id} />)}</span><span>{count ? `${count} working` : agents.length ? `${agents.length} done` : "No subagents"}</span>{count > 0 && agents.length > count && <span className="text-faint">{agents.length - count} done</span>}</button>
    </div>
    <div className="resource-section"><div className="resource-heading"><button type="button" onClick={() => open("sources")}>Sources</button><button type="button" className="icon-button" aria-label="Add sources" title="Attach sources to your next message" disabled={!resources} onClick={resources?.addSources}><Plus className="size-3.5" /></button></div>
      {sources.slice(0, 3).map(source => <SourceRow key={source.id} source={source} sessionId={resources?.id || session?.id || ""} preview={preview} compact />)}
      {!sources.length && <p className="resource-empty">Files, images, and links appear here</p>}
      <button type="button" className="resource-view-all" onClick={() => open("sources")}><Link2 className="size-3.5" />View all{sources.length > 0 && <span>{sources.length}</span>}</button>
    </div>
  </div>;
}

function SourceRow({ source, sessionId, preview, compact = false }: { source: SessionSource; sessionId: string; preview: (source: SessionSource) => void; compact?: boolean }) {
  const icon = source.file?.kind === "image" ? <img src={source.file.content_url} alt="" loading="lazy" /> : source.file ? <FileText className="size-4" /> : <Link2 className="size-4" />;
  const body = <>{icon}<span className="source-label"><span className="truncate">{source.name}</span>{!compact && <><small title={source.file?.source_path || source.href}>{source.file?.source_path || source.href || source.file?.mime}</small><small className="source-origin">{{ attached: "Attached to the conversation", generated: "Generated by the agent", viewed: "Viewed by the agent", referenced: "Referenced in the conversation" }[source.origin]}</small></>}</span></>;
  return <div className={`workspace-source ${compact ? "is-compact" : ""}`} title={source.file?.source_path || source.href || source.name}>{source.file
    ? <button type="button" aria-label={`Preview ${source.name}`} onClick={() => preview(source)}>{body}</button>
    : <FileReferenceScope sessionId={sessionId} itemId={source.itemId}><FileLink href={source.href || ""} title={source.name}>{body}</FileLink></FileReferenceScope>}</div>;
}

function SourcesPanel({ resources, sessionId, addSources, preview }: { resources?: SessionResources; sessionId: string; addSources: () => void; preview: (source: SessionSource) => void }) {
  return <div className="workspace-sources"><div className="workspace-panel-heading"><span>Sources{resources?.sources.length ? ` · ${resources.sources.length}` : ""}</span><button type="button" className="icon-button" aria-label="Add sources" title="Attach sources to your next message" disabled={!resources} onClick={addSources}><Plus className="size-4" /></button></div>
    {resources?.sources.map(source => <SourceRow key={source.id} source={source} sessionId={sessionId} preview={preview} />)}
    {!resources?.sources.length && <p className="workspace-empty">Screenshots, files, and links from the conversation will appear here.</p>}
    {resources?.hasEarlier && <button type="button" className="workspace-load-more" disabled={resources.loadingEarlier} onClick={resources.loadEarlier}>{resources.loadingEarlier ? "Loading earlier sources..." : "Load sources from earlier conversation"}</button>}
  </div>;
}

function AgentsPanel({ id, resources, sessions, select }: { id: string; resources?: SessionResources; sessions: Session[]; select: (id: string) => void }) {
  const [limit, setLimit] = useState(10);
  const agents = resources?.children ?? [], running = agents.filter(working), done = agents.filter(agent => !working(agent));
  const depth = resources?.session?.depth ?? 0;
  return <div className="workspace-agents">{[["Active", running], ["Done", done]] .map(([title, values]) => {
    const group = values as Child[], active = title === "Active";
    return <section key={String(title)} aria-label={`${title} subagents`}><h2>{String(title)} · {group.length}</h2>{!group.length && <p className="text-xs text-faint">No {active ? "active" : "completed"} subagents</p>}{group.slice(0, active ? group.length : limit).map(agent => {
      const session = sessions.find(value => value.id === agent.thread_id), parent = agents.find(value => value.thread_id === agent.parent_id);
      return <button key={agent.thread_id} type="button" className="workspace-agent-row" aria-label={`Open chat for ${agent.name}`} title={agent.agent_path || agent.cwd} style={{ paddingLeft: Math.max(0, Math.min(6, agent.depth - depth - 1)) * 16 }} onClick={() => select(agent.thread_id)}><AgentAvatar id={agent.thread_id} /><span className="min-w-0 flex-1"><span className="block truncate font-medium">{agent.name}</span>{agent.parent_id !== id && parent && <small className="mt-1 block truncate text-faint">{parent.name}<ChevronRight className="inline size-3" />{agent.agent_path?.split("/").pop() || "Subagent"}</small>}</span>{active ? <Status status={agent.status || "active"} /> : session?.updated_at ? <time title={exactTime(session.updated_at)}>{ago(session.updated_at)}</time> : <Status status={agent.status || "saved"} />}</button>;
    })}{!active && group.length > limit && <button type="button" className="workspace-load-more" onClick={() => setLimit(group.length)}>Show {group.length - limit} more</button>}</section>;
  })}</div>;
}
