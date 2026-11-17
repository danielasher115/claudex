import { useMemo, useState } from "react";
import { ChevronRight, GitBranch } from "lucide-react";
import type { Child } from "../api";
import { Status } from "./shared";

export function Subagents({ id, agents, select }: { id: string; agents: Child[]; select?: (id: string) => void }) {
  const children = useMemo(() => {
    const ids = new Set(agents.map(agent => agent.thread_id));
    const groups = new Map<string, Child[]>();
    for (const agent of agents) {
      const parent = ids.has(agent.parent_id) ? agent.parent_id : id;
      if (!groups.has(parent)) groups.set(parent, []);
      groups.get(parent)!.push(agent);
    }
    return groups;
  }, [id, agents]);
  if (!agents.length) return null;
  return <details open className="mb-4 rounded-xl border border-line bg-panel p-3">
    <summary className="text-xs font-medium">Subagents ({agents.length})</summary>
    <ul aria-label="Subagents" className="mt-2">{children.get(id)?.map(agent => <Branch key={agent.thread_id} agent={agent} childrenByParent={children} select={select} />)}</ul>
  </details>;
}

function Branch({ agent, childrenByParent, select }: { agent: Child; childrenByParent: Map<string, Child[]>; select?: (id: string) => void }) {
  const [open, setOpen] = useState(true);
  const children = childrenByParent.get(agent.thread_id) ?? [];
  return <li>
    <div className="flex items-center gap-1 rounded-md hover:bg-hover">
      {children.length ? <button type="button" className="icon-button !size-6 shrink-0" aria-label={`Subagents of ${agent.name}`} aria-expanded={open} onClick={() => setOpen(!open)}><ChevronRight className={`size-3 transition-transform ${open ? "rotate-90" : ""}`} /></button> : <GitBranch className="mx-1.5 size-3 shrink-0 text-faint" />}
      <button type="button" className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-2 text-left text-xs" aria-label={`Open chat for ${agent.name}`} title={agent.agent_path || agent.cwd} onClick={() => select?.(agent.thread_id)}>
        <span className="min-w-0 flex-1 truncate font-medium">{agent.name}</span>
        {agent.model && <span className="hidden truncate text-[10px] text-faint sm:block">{agent.model}</span>}
        {agent.status && <Status status={agent.status} />}
      </button>
    </div>
    {children.length > 0 && open && <ul aria-label={`Subagents of ${agent.name}`} className="ml-3 border-l border-line pl-3">{children.map(child => <Branch key={child.thread_id} agent={child} childrenByParent={childrenByParent} select={select} />)}</ul>}
  </li>;
}
