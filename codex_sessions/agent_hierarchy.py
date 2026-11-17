"""Display lineage from native spawn metadata and gateway delegation records."""
import json
from hashlib import sha256
from collections import defaultdict


def enrich(sessions, metadata, agents):
    indexed = {session["id"]: session for session in sessions}
    for session in sessions:
        identity = session["id"]
        row = metadata.get(identity, {})
        source = row.get("source")
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except ValueError:
                source = None
        subagent = (source.get("subagent") or source.get("subAgent")) if isinstance(source, dict) else None
        spawn = (subagent.get("thread_spawn") or subagent.get("threadSpawn")) if isinstance(subagent, dict) else None
        spawn = spawn if isinstance(spawn, dict) else {}
        agent = agents.get(identity)
        parent = agent["parent_id"] if agent else row.get("native_parent_id") or spawn.get("parent_thread_id") or spawn.get("parentThreadId")
        session.update(parent_id=parent if isinstance(parent, str) and parent else None,
                       agent_path=row.get("agent_path") or row.get("agentPath") or spawn.get("agent_path"),
                       agent_origin="gateway" if agent else "native")
        if agent:
            session.update(title=agent["name"], sandbox=agent["sandbox"], source="agents")

    resolved = {}
    for identity in indexed:
        trail, positions = [], {}
        current = identity
        while current in indexed and current not in resolved:
            if current in positions:
                for cyclic in trail[positions[current]:]:
                    indexed[cyclic]["parent_id"] = None
                    resolved[cyclic] = (cyclic, 0, indexed[cyclic]["source"])
                break
            positions[current] = len(trail)
            trail.append(current)
            current = indexed[current]["parent_id"]
        for member in reversed(trail):
            if member in resolved:
                continue
            parent = indexed[member]["parent_id"]
            root, depth, source = resolved.get(parent, (parent or member, -1 if parent is None else 0, indexed[member]["source"]))
            resolved[member] = (root, depth + 1, "agents" if member in agents else source)
    for identity, (root, depth, source) in resolved.items():
        indexed[identity].update(root_id=root, depth=depth, source=source)
    families = defaultdict(list)
    for session in sessions:
        parent = session["parent_id"]
        while parent in indexed:
            families[parent].append([session[key] for key in ("id", "parent_id", "revision", "status", "title", "model")])
            parent = indexed[parent]["parent_id"]
    for identity, members in families.items():
        revision = sha256(json.dumps(sorted(members), separators=(",", ":")).encode()).hexdigest()[:16]
        indexed[identity]["revision"] += f":family:{revision}"


def descendants(sessions, parent_id):
    children = defaultdict(list)
    for session in sessions:
        children[session.get("parent_id")].append(session)
    for siblings in children.values():
        siblings.sort(key=lambda row: (row["created_at"], row["id"]))
    result, visited = [], {parent_id}
    pending = list(reversed(children[parent_id]))
    while pending:
        session = pending.pop()
        if session["id"] in visited:
            continue
        visited.add(session["id"])
        result.append({"thread_id": session["id"], "parent_id": session["parent_id"], "name": session["title"],
                       "depth": session["depth"], "cwd": session["cwd"], "status": session["status"], "model": session["model"],
                       "origin": session["agent_origin"], "agent_path": session.get("agent_path")})
        pending.extend(reversed(children[session["id"]]))
    return result
