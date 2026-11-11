import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowUpRight, Bot, Brain, ChevronRight, FileText, Folder, Layers3, Plug, RefreshCw, Search, ShieldCheck, Sparkles } from "lucide-react";
import { api, exactTime, label, type Session } from "../api";
import { CopyButton, Dialog, Loading } from "./shared";

type Values = Record<string, unknown>;
interface ContextDocument { source: string; text: string; condition?: unknown; imported_from?: string }
interface Section { status: string; source: string; message?: string; scope?: string; items?: Values[]; settings?: Values; layers?: Values[]; errors?: unknown[]; partial?: boolean; catalog_count?: number }
export interface SessionDetailsData {
  session_id: string;
  recorded: {
    status: string; source: string; settings: Values; configured_settings?: Values; metadata: Values; context: Values;
    instructions: ContextDocument[]; memories: ContextDocument[];
    task?: string; attachments?: Values[]; delegation?: Values; skills: Values[]; sources: Record<string, string>; inheritance?: Values; caller_conversation?: Values;
  };
  current: { scope: string; cwd: string; config: Section; skills: Section; mcp: Section; plugins: Section; apps: Section };
  warnings: string[];
}
const titles: Record<string, string> = {
  cwd: "Workspace", model: "Model", effort: "Reasoning effort", model_provider: "Model provider", approval_policy: "Approvals",
  sandbox: "Sandbox", service_tier: "Service tier", personality: "Personality", collaboration_mode: "Collaboration mode",
  id: "Session ID", source: "Source", originator: "Originator", cli_version: "Codex version", parent_id: "Parent session",
  forked_from_id: "Forked from", history_mode: "History mode", memory_mode: "Memory mode", tokens_used: "Recorded tokens",
  model_context_window: "Context window", cached_input_tokens: "Cached input tokens", reasoning_output_tokens: "Reasoning output tokens",
};
const titleOf = (key: string) => titles[key] ?? key.replaceAll("_", " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, letter => letter.toUpperCase());
const present = (value: unknown) => value !== null && value !== undefined && value !== "";
const text = (value: unknown): string => value == null ? "Not recorded" : typeof value === "object" ? JSON.stringify(value, null, 2) : typeof value === "boolean" ? value ? "Yes" : "No" : String(value);
const scopeName = (scope?: string) => scope === "current_thread_runtime" ? "Current thread runtime" : "Current workspace and runtime";
const sourceLabel = (source: string) => ({ "skills/list": "Current workspace skill inventory", "plugin/list": "Installed plugins from Codex", "app/list": "Available apps from Codex", "mcpServerStatus/list": "Current runtime connections", "config/read configured MCP names": "Current workspace configuration" }[source] ?? source);

function Fields({ values, sources, prefix = "" }: { values: Values; sources?: Record<string, string>; prefix?: string }) {
  return <dl className="details-fields">{Object.entries(values).map(([key, value]) => <div key={key}>
    <dt>{titleOf(key)}</dt><dd title={sources?.[`${prefix}${key}`]} className={!present(value) ? "text-faint" : ""}>
      {value && typeof value === "object" && !Array.isArray(value) ? <Fields values={value as Values} /> : <span className="whitespace-pre-wrap break-words">{key.endsWith("_at") && typeof value === "number" && value > 0 ? exactTime(value) : typeof value === "number" ? value.toLocaleString() : text(value)}</span>}
    </dd></div>)}</dl>;
}
function Group({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return <section className="details-group"><div className="mb-3"><h3 className="text-[13px] font-semibold">{title}</h3>{note && <p className="mt-1 text-[11px] leading-relaxed text-faint">{note}</p>}</div>{children}</section>;
}
function Inventory({ section, title, empty, filter = "", children }: { section: Section; title: string; empty: string; filter?: string; children: (item: Values, index: number) => ReactNode }) {
  const items = (section.items ?? []).filter(item => JSON.stringify(item).toLowerCase().includes(filter.toLowerCase()));
  return <Group title={`${title}${section.status === "available" ? ` (${section.items?.length ?? 0})` : ""}`} note={sourceLabel(section.source)}>
    {section.message && section.status === "available" && <p className="mb-3 text-[11px] leading-relaxed text-faint">{section.message}</p>}
    {section.status === "loading" || section.status === "not_requested" ? <Loading label={`Loading ${title.toLowerCase()}`} /> : section.status === "error" ? <p role="alert" className="text-xs text-removed-ink">{section.message}</p> : section.status !== "available" ? <p className="text-xs text-faint">{section.message || "Not available from this runtime"}</p> : items.length ? <div className="divide-y divide-line">{items.map(children)}</div> : <p className="text-xs text-faint">{filter ? `No matching ${empty}` : `No ${empty} reported`}</p>}
    {section.errors?.length ? <p className="mt-2 whitespace-pre-wrap text-xs text-removed-ink">{section.errors.map(text).join("\n")}</p> : null}
  </Group>;
}
function Skill({ skill, index }: { skill: Values; index: number }) {
  return <details className="details-entry" key={`${skill.path}:${index}`}><summary><ChevronRight className="details-chevron" /><span className="min-w-0 flex-1 truncate">{text(skill.name || skill.path)}</span><span className="text-[10px] font-normal text-faint">{skill.enabled === false ? "Disabled" : text(skill.scope || "")}</span></summary>
    <div className="details-entry-body">{present(skill.description) && <p className="mb-2 text-xs leading-relaxed text-muted">{text(skill.description)}</p>}<Fields values={Object.fromEntries(Object.entries(skill).filter(([key]) => !["name", "description"].includes(key)))} /></div></details>;
}
function Documents({ title, files, query = "" }: { title: string; files: ContextDocument[]; query?: string }) {
  return <Group title={`${title} (${files.length})`}>
    {files.length ? files.map((file, index) => <details className="details-entry details-document" key={`${file.source}:${index}`}><summary><FileText className="size-3.5 shrink-0 text-faint" /><span className="min-w-0 flex-1"><span className="block truncate">{file.source.split(/[\\/]/).pop() || file.source}</span><span className="details-document-path" title={file.source}>{file.source}</span></span><span className="details-document-size">{file.text.length.toLocaleString()} chars</span><ChevronRight className="details-chevron" /></summary><div className="details-entry-body"><div className="mb-2 flex items-start gap-2"><p className="min-w-0 flex-1 break-all text-[11px] text-faint">{file.source}</p><CopyButton text={file.text} label={`Copy ${file.source.split(/[\\/]/).pop()}`} /></div>{(file.condition || file.imported_from) && <Fields values={{ ...(file.condition ? { applies_to: file.condition } : {}), ...(file.imported_from ? { imported_from: file.imported_from } : {}) }} />}<pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed">{file.text || "Contents were not recorded"}</pre></div></details>) : <p className="text-xs text-faint">{query ? `No matching ${title.toLowerCase()}` : `No ${title.toLowerCase()} recorded`}</p>}
  </Group>;
}

function AgentSummary({ session, recorded, current, navigate }: { session: Session; recorded: SessionDetailsData["recorded"]; current?: SessionDetailsData["current"]; navigate: (tab: string) => void }) {
  const connections = current?.mcp.status === "available" ? current.mcp.items?.length ?? 0 : null;
  const metrics = [
    { title: "Instructions", value: recorded.instructions.length, icon: FileText, tab: "Context" },
    { title: "Memories", value: recorded.memories.length, icon: Brain, tab: "Context" },
    { title: "Inherited skills", value: recorded.skills.length, icon: Sparkles, tab: "Context" },
    { title: "Connections", value: connections ?? "Explore", icon: Plug, tab: "Integrations" },
  ];
  const effort = recorded.settings.effort;
  return <section className="agent-context-summary" aria-label="Agent summary">
    <div className="agent-context-heading"><div className="agent-context-mark"><Bot /></div><div className="min-w-0 flex-1"><h3>{session.title}</h3><p title={session.cwd}><Folder className="size-3" />{session.cwd || "Workspace not recorded"}</p></div><span className={`agent-context-state ${session.status === "active" ? "is-active" : ""}`}><i />{label(session.status)}</span></div>
    <div className="agent-context-settings"><span>{text(recorded.settings.model || session.model || "Model not recorded")}</span>{present(effort) && <span><Sparkles className="size-3" />{text(effort).replace(/^./, value => value.toUpperCase())} effort</span>}{typeof recorded.settings.sandbox === "string" && <span><ShieldCheck className="size-3" />{recorded.settings.sandbox}</span>}</div>
    <div className="agent-context-metrics">{metrics.map(({ title, value, icon: Icon, tab }) => <button key={title} onClick={() => navigate(tab)}><Icon className="size-3.5" /><strong>{value}</strong><span>{title}</span><ChevronRight className="size-3" /></button>)}</div>
  </section>;
}

function ContextBudget({ context }: { context: Values }) {
  const usage = (context.token_usage ?? {}) as Values;
  const last = (usage.last ?? {}) as Values;
  const input = last.input_tokens;
  const window = usage.model_context_window ?? context.model_context_window;
  if (typeof input !== "number" || typeof window !== "number" || window <= 0 || input < 0) return null;
  const percent = Math.min(100, Math.round(input / window * 100));
  return <section className="context-budget" aria-label="Context usage snapshot"><div><span><Layers3 className="size-3.5" />Last turn input</span><strong>{percent}%</strong></div><div role="meter" aria-label="Last turn input compared with context window" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-valuetext={`${input.toLocaleString()} of ${window.toLocaleString()} tokens`} className={`context-budget-track ${percent >= 85 ? "is-full" : ""}`}><span style={{ width: `${percent}%` }} /></div><p>{input.toLocaleString()} / {window.toLocaleString()} tokens</p><small>Latest observed input, separate from cumulative session usage.</small></section>;
}

export function SessionDetails({ session, close }: { session: Session; close: () => void }) {
  const [data, setData] = useState<SessionDetailsData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("Overview");
  const [filter, setFilter] = useState("");
  const [skillFilter, setSkillFilter] = useState("");
  const [contextFilter, setContextFilter] = useState("");
  const [sectionErrors, setSectionErrors] = useState<Record<string, string>>({});
  const requests = useRef(new Map<string, AbortController>());
  const loaded = useRef(new Set<string>());
  const tabId = useId();
  function load(section: string, refresh = false) {
    if (!refresh && (loaded.current.has(section) || requests.current.has(section))) return;
    requests.current.get(section)?.abort();
    const controller = new AbortController();
    requests.current.set(section, controller);
    if (section === "recorded") setLoading(true);
    setSectionErrors(previous => ({ ...previous, [section]: "" }));
    if (section !== "recorded") setData(previous => previous ? { ...previous,
      current: { ...previous.current, [section]: { ...previous.current[section as "skills"], status: "loading" } } } : previous);
    api<SessionDetailsData>(`/api/sessions/${session.id}/details?section=${section}${refresh ? "&refresh=1" : ""}`, undefined, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        loaded.current.add(section);
        setData(previous => {
          if (!previous || section === "recorded") return previous ? { ...result, current: previous.current } : result;
          const current = { ...previous.current };
          for (const name of ["config", "skills", "mcp", "plugins", "apps"] as const) {
            const incoming = result.current[name];
            if (incoming && incoming.status !== "not_requested") current[name] = incoming;
          }
          return { ...previous, current, warnings: [...new Set([...previous.warnings, ...result.warnings])] };
        });
        if (section === "recorded") setError("");
      })
      .catch(e => {
        if (controller.signal.aborted) return;
        if (section === "recorded") setError(e.message);
        else {
          setSectionErrors(previous => ({ ...previous, [section]: e.message }));
          setData(previous => previous ? { ...previous, current: { ...previous.current,
            [section]: { ...previous.current[section as "skills"], status: "error", message: e.message } } } : previous);
        }
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        requests.current.delete(section);
        if (section === "recorded") setLoading(false);
      });
  }
  useEffect(() => {
    setData(null); setError(""); setTab("Overview"); setSectionErrors({});
    setContextFilter(""); setSkillFilter(""); setFilter("");
    loaded.current.clear();
    load("recorded");
    return () => { for (const controller of requests.current.values()) controller.abort(); requests.current.clear(); };
  }, [session.id]);
  useEffect(() => {
    if (!data) return;
    for (const section of tab === "Context" ? ["config", "skills"] : tab === "Integrations" ? ["mcp", "plugins", "apps"] : []) load(section);
  }, [tab, Boolean(data)]);
  function refreshVisible() {
    load("recorded", true);
    for (const section of tab === "Context" ? ["config", "skills"] : tab === "Integrations" ? ["mcp", "plugins", "apps"] : []) load(section, true);
  }
  const recorded = data?.recorded;
  const current = data?.current;
  const tabs = ["Overview", "Context", "Integrations"];
  const matches = (value: unknown) => JSON.stringify(value).toLowerCase().includes(contextFilter.toLowerCase());
  const tabIcons = [Bot, Layers3, Plug];
  return <Dialog title="Agent context" subtitle={session.title} close={close} className="session-details-dialog" actions={<button type="button" className="icon-button" aria-label="Refresh session details" title="Refresh session details" disabled={loading} onClick={refreshVisible}><RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} /></button>}>
    <div role="tablist" aria-label="Session details sections" className="details-tabs">{tabs.map((name, index) => <button key={name} role="tab" id={`${tabId}-tab-${index}`} aria-controls={`${tabId}-panel`} aria-selected={name === tab} tabIndex={name === tab ? 0 : -1} onClick={() => setTab(name)} onKeyDown={event => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length; setTab(tabs[next]); document.getElementById(`${tabId}-tab-${next}`)?.focus(); } }}>{(() => { const Icon = tabIcons[index]; return <Icon className="size-3.5" />; })()}{name}</button>)}</div>
    <div id={`${tabId}-panel`} role="tabpanel" aria-labelledby={`${tabId}-tab-${tabs.indexOf(tab)}`} tabIndex={0} className="details-scroll" aria-busy={loading}>
      {loading && !data && <Loading label="Loading session context" />}
      {error && <p role="alert" className="mb-4 text-xs text-removed-ink">{error}</p>}
      {recorded && <AgentSummary session={session} recorded={recorded} current={current} navigate={setTab} />}
      {data?.warnings.length ? <details className="mb-5 text-xs text-faint"><summary>{data.warnings.length} context availability note{data.warnings.length === 1 ? "" : "s"}</summary><ul className="mt-2 list-disc space-y-2 pl-4">{data.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details> : null}
      {tab === "Overview" && <>
        {recorded && <>
          {recorded.task && <Group title="Task brief"><details className="details-task"><summary>{recorded.task.split("\n")[0]}<ChevronRight className="details-chevron" /></summary><div className="details-entry-body"><CopyButton text={recorded.task} label="Copy task brief" /><pre>{recorded.task}</pre></div></details></Group>}
          <ContextBudget context={recorded.context} />
          <Group title="Model and execution" note="Saved settings and latest recorded execution values. Hover a value to see its source."><Fields values={recorded.settings} sources={recorded.sources} prefix="settings." /></Group>
          {recorded.configured_settings && Object.keys(recorded.configured_settings).length > 0 && <details className="details-entry mt-3"><summary><ChevronRight className="details-chevron" />Current thread configuration</summary><div className="details-entry-body"><p className="mb-2 text-[11px] text-faint">The configuration Codex reports for this thread now. It can differ from a previous run.</p><Fields values={recorded.configured_settings} /></div></details>}
          <Group title="Context usage"><Fields values={Object.fromEntries(Object.entries(recorded.context).filter(([key]) => !["latest_summary", "session_summary"].includes(key)))} /></Group></>}
        {recorded?.delegation && <Group title="Delegation"><Fields values={Object.fromEntries(Object.entries(recorded.delegation).filter(([key]) => key !== "children"))} />{Array.isArray(recorded.delegation.children) && recorded.delegation.children.map((child: Values, index) => <a className="details-agent-link" href={`/sessions/${child.thread_id}`} key={index}><Bot className="size-3.5" /><span>{text(child.name || child.thread_id)}</span><ArrowUpRight className="size-3" /></a>)}</Group>}
        {recorded?.attachments && recorded.attachments.length > 0 && <Group title={`Attached files (${recorded.attachments.length})`}>{recorded.attachments.map((file, index) => <div className="details-attachment" key={index}><FileText className="size-4 text-faint" /><div><span>{text(file.name)}</span><small>{text(file.mime)}{typeof file.size === "number" ? ` / ${file.size.toLocaleString()} bytes` : ""}</small></div></div>)}</Group>}
        <Group title="Session" note="Recorded session metadata"><Fields values={{ id: session.id, cwd: session.cwd, created_at: session.created_at, updated_at: session.updated_at, status: session.status, ...Object.fromEntries(Object.entries(recorded?.metadata ?? {}).filter(([key, value]) => key !== "title" && !(key === "session_id" && value === session.id))) }} /><CopyButton text={session.id} label="Copy session ID" /></Group>
      </>}
      {tab === "Context" && recorded && current && <>
        {recorded.caller_conversation && Object.keys(recorded.caller_conversation).length > 0 && <Group title="Caller conversation" note="The exact launching Claude session. Its public history is supplied as reference evidence."><details className="details-entry"><summary><ChevronRight className="details-chevron" />Inspect caller history and snapshot</summary><div className="details-entry-body"><Fields values={recorded.caller_conversation} /></div></details></Group>}
        {recorded.inheritance && <Group title="Launch inheritance" note="Sources and translation details captured when this agent was launched."><details className="details-entry"><summary><ChevronRight className="details-chevron" />Inspect inherited sources and coverage</summary><div className="details-entry-body"><Fields values={recorded.inheritance} /></div></details></Group>}
        {["session_summary", "latest_summary"].map(key => recorded.context[key] ? <Group key={key} title={key === "session_summary" ? "Inherited session summary" : "Latest context summary"}><pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs leading-relaxed">{text(recorded.context[key])}</pre></Group> : null)}
        <label className="details-search"><Search className="size-3.5 text-faint" /><input type="search" aria-label="Filter context" placeholder="Search instructions, memories and inherited skills..." value={contextFilter} onChange={event => setContextFilter(event.target.value)} /></label>
        <Documents title="Instructions" files={recorded.instructions.filter(matches)} query={contextFilter} /><Documents title="Memories" files={recorded.memories.filter(matches)} query={contextFilter} />
        <Group title={`Inherited skills (${recorded.skills.length})`} note="Skill references passed to this session at launch.">{recorded.skills.length ? recorded.skills.filter(matches).map((skill, index) => <Skill key={index} skill={skill} index={index} />) : <p className="text-xs text-faint">No inherited skills recorded</p>}</Group>
        <label className="mb-3 flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2"><Search className="size-3.5 text-faint" /><input type="search" aria-label="Filter skills" placeholder="Find a skill..." className="min-w-0 flex-1 bg-transparent text-xs outline-none" value={skillFilter} onChange={event => setSkillFilter(event.target.value)} /></label>
        <Inventory section={current.skills} title="Available skills" empty="skills" filter={skillFilter}>{(skill, index) => <Skill key={index} skill={skill} index={index} />}</Inventory>
        <Group title="Current workspace defaults" note="These are the current defaults for this workspace. They may differ from this session's recorded settings.">{["not_requested", "loading"].includes(current.config.status) ? <Loading label="Loading workspace defaults" /> : current.config.status === "available" ? <><Fields values={current.config.settings ?? {}} /><details className="details-entry"><summary><ChevronRight className="details-chevron" />Configuration sources ({current.config.layers?.length ?? 0})</summary><div className="details-entry-body">{current.config.layers?.map((layer, index) => <div key={index} className="mb-4"><Fields values={layer} /></div>)}</div></details></> : <p className="text-xs text-faint">{current.config.message || "Current configuration is unavailable"}</p>}</Group>
      </>}
      {Object.values(sectionErrors).some(Boolean) && <button className="details-retry" onClick={refreshVisible}>Retry unavailable sections</button>}
      {tab === "Integrations" && current && <>
        <p className="mb-4 text-xs leading-relaxed text-faint">Available connections and extensions now. Availability does not mean a tool or skill was used in this session.</p>
        <label className="mb-5 flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2"><Search className="size-3.5 text-faint" /><input type="search" aria-label="Filter integrations" placeholder="Filter names, tools, descriptions..." className="min-w-0 flex-1 bg-transparent text-xs outline-none" value={filter} onChange={event => setFilter(event.target.value)} /></label>
        <p className="mb-3 text-[11px] text-faint">{scopeName(current.mcp.scope)}</p>
        <Inventory section={current.mcp} title="MCP servers" empty="MCP servers" filter={filter}>{(server, index) => <details className="details-entry" key={index}><summary><ChevronRight className="details-chevron" /><span className="min-w-0 flex-1 truncate">{text(server.name)}</span><span className="text-[10px] font-normal text-faint">{server.enabled === false ? "Disabled" : current.mcp.partial ? "Configured" : text(server.runtime_status || server.auth_status || "")}</span></summary><div className="details-entry-body"><Fields values={Object.fromEntries(Object.entries(server).filter(([key]) => !["name", "tools"].includes(key)))} /><h4 className="mb-2 mt-4 text-xs font-medium">{current.mcp.partial ? "Tool inventory unavailable" : `Tools (${Array.isArray(server.tools) ? server.tools.length : 0})`}</h4>{Array.isArray(server.tools) && server.tools.map((tool: Values, toolIndex) => <div key={toolIndex} className="border-t border-line py-2"><p className="break-all font-mono text-[11px]">{text(tool.name)}</p>{present(tool.description) && <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-faint">{text(tool.description)}</p>}</div>)}</div></details>}</Inventory>
        <Inventory section={current.plugins} title="Plugins" empty="plugins" filter={filter}>{(plugin, index) => <details className="details-entry" key={index}><summary><ChevronRight className="details-chevron" /><span className="min-w-0 flex-1 truncate">{text(plugin.name || plugin.id)}</span><span className="text-[10px] font-normal text-faint">{plugin.enabled === false ? "Disabled" : plugin.enabled === true ? "Enabled" : ""}</span></summary><div className="details-entry-body"><Fields values={plugin} /></div></details>}</Inventory>
        <Inventory section={current.apps} title="Apps" empty="apps" filter={filter}>{(app, index) => <details className="details-entry" key={index}><summary><ChevronRight className="details-chevron" /><span className="min-w-0 flex-1 truncate">{text(app.name || app.id)}</span><span className="text-[10px] font-normal text-faint">{app.enabled === false ? "Disabled" : app.accessible === false ? "Unavailable" : ""}</span></summary><div className="details-entry-body"><Fields values={app} /></div></details>}</Inventory>
      </>}
    </div>
  </Dialog>;
}
