import type { Session } from "./api";

interface RunPosition { running: boolean; turnId: string | null; startedAt: number | null; position: number; awaitingTurn: boolean }

export function orderSessions(sessions: Session[], previous: Map<string, RunPosition>, now = Date.now() / 1000) {
  const positions = new Map<string, RunPosition>();
  const promotions: { id: string; at: number; index: number }[] = [];
  let latest = Math.max(now, ...Array.from(previous.values(), value => value.position));
  sessions.forEach((session, index) => {
    const before = previous.get(session.id);
    const running = session.status === "active" || session.status === "unavailable" && Boolean(before?.running);
    const startedAt = session.activity?.turn_started_at ?? null;
    const olderTurn = before?.startedAt != null && startedAt !== null && startedAt < before.startedAt;
    const incomingTurn = olderTurn ? null : session.activity?.turn_id ?? null;
    const turnId = incomingTurn ?? before?.turnId ?? null;
    const changedTurn = Boolean(before?.turnId && incomingTurn && before.turnId !== incomingTurn);
    const becameRunning = Boolean(running && before && !before.running);
    const awaitingTurn = !before ? running && !incomingTurn : becameRunning ? before.awaitingTurn || !incomingTurn || incomingTurn === before.turnId
      : before.awaitingTurn && (!incomingTurn || incomingTurn === before.turnId);
    const newRun = running && before && (becameRunning || changedTurn && !before.awaitingTurn);
    const finishedRun = before?.running && !running;
    const unseenRun = !before && running && previous.size > 0;
    const missedRun = before && !running && changedTurn && !before.awaitingTurn;
    const position = before?.position ?? (running ? startedAt ?? session.updated_at : session.updated_at);
    positions.set(session.id, { running, turnId, startedAt: olderTurn ? before!.startedAt : startedAt ?? before?.startedAt ?? null, position, awaitingTurn });
    if (newRun || finishedRun || unseenRun || missedRun) {
      const completedAt = ["completed", "failed", "interrupted", "cancelled"].includes(session.activity?.phase ?? "") ? session.activity?.last_event_at : null;
      promotions.push({ id: session.id, at: running ? (awaitingTurn ? session.updated_at : startedAt ?? session.updated_at) : completedAt ?? session.updated_at, index });
    }
  });
  promotions.sort((a, b) => a.at - b.at || b.index - a.index).forEach(event => { positions.get(event.id)!.position = ++latest; });
  const ordered = [...sessions].sort((a, b) => {
    const left = positions.get(a.id)!, right = positions.get(b.id)!;
    return Number(right.running) - Number(left.running) || right.position - left.position || b.id.localeCompare(a.id);
  });
  return { sessions: ordered, positions };
}
