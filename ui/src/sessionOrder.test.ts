import { expect, test } from "vitest";
import type { AgentActivity, Session } from "./api";
import { orderSessions } from "./sessionOrder";

const running = (id: string, started: number, updated = started, turn = id): Session => ({
  id, status: "active", updated_at: updated, activity: { turn_id: turn, turn_started_at: started } as AgentActivity,
} as Session);
const ids = (value: ReturnType<typeof orderSessions>) => value.sessions.map(session => session.id);

test("progress stays put, a new turn in the same running session promotes once, and completions stay below running work", () => {
  let sessions = [running("a", 200), running("b", 100), { id: "c", status: "idle", updated_at: 400 } as Session];
  let state = orderSessions(sessions, new Map(), 500);
  expect(ids(state)).toEqual(["a", "b", "c"]);
  sessions = [running("a", 200, 900), running("b", 100, 1000), sessions[2]];
  state = orderSessions(sessions, state.positions, 600);
  expect(ids(state)).toEqual(["a", "b", "c"]);
  sessions[1] = running("b", 700, 1100, "new-b");
  state = orderSessions(sessions, state.positions, 800);
  expect(ids(state)).toEqual(["b", "a", "c"]);
  sessions[0] = running("a", 200, 1500);
  state = orderSessions(sessions, state.positions, 900);
  expect(ids(state)).toEqual(["b", "a", "c"]);
  sessions[1] = { ...sessions[1], status: "idle" };
  state = orderSessions(sessions, state.positions, 1000);
  expect(ids(state)).toEqual(["a", "b", "c"]);
  sessions[0] = { ...sessions[0], status: "idle" };
  state = orderSessions(sessions, state.positions, 1000);
  expect(ids(state)).toEqual(["a", "b", "c"]);
});

test("late activity metadata, older history pages and a temporary disconnection do not reorder running rows", () => {
  const a = { id: "a", status: "active", updated_at: 500 } as Session;
  let state = orderSessions([a, running("b", 100)], new Map(), 600);
  expect(ids(state)).toEqual(["a", "b"]);
  state = orderSessions([running("a", 50, 700), running("b", 100, 800)], state.positions, 900);
  expect(ids(state)).toEqual(["a", "b"]);
  state = orderSessions([running("a", 50, 950), running("b", 40, 1000, "older-b")], state.positions, 1100);
  expect(ids(state)).toEqual(["a", "b"]);
  state = orderSessions([{ ...running("a", 50), status: "unavailable" }, running("b", 100)], state.positions, 1200);
  expect(ids(state)).toEqual(["a", "b"]);
  state = orderSessions([running("a", 50), running("b", 100)], state.positions, 1300);
  expect(ids(state)).toEqual(["a", "b"]);
});

test("multiple starts and completions in one snapshot retain their actual chronological order", () => {
  let state = orderSessions([{ ...running("a", 100), status: "idle" }, { ...running("b", 100), status: "idle" }], new Map(), 100);
  state = orderSessions([running("a", 300), running("b", 200)], state.positions, 400);
  expect(ids(state)).toEqual(["a", "b"]);
  state = orderSessions([{ ...running("a", 300, 800), status: "idle" }, { ...running("b", 200, 700), status: "idle" }], state.positions, 900);
  expect(ids(state)).toEqual(["a", "b"]);
});

test("a status transition with stale previous-turn metadata does not promote again when current metadata arrives", () => {
  let state = orderSessions([{ ...running("a", 100), status: "idle" }, { ...running("b", 100), status: "idle" }], new Map(), 100);
  state = orderSessions([running("a", 100), { ...running("b", 100), status: "idle" }], state.positions, 200);
  state = orderSessions([running("b", 300, 300, "new-b"), running("a", 100)], state.positions, 300);
  expect(ids(state)).toEqual(["b", "a"]);
  state = orderSessions([running("a", 200, 400, "new-a"), running("b", 300, 300, "new-b")], state.positions, 400);
  expect(ids(state)).toEqual(["b", "a"]);
});

test("late completed-turn metadata does not promote an already finished session twice", () => {
  let state = orderSessions([{ ...running("a", 100), status: "idle" }, running("b", 100)], new Map(), 100);
  state = orderSessions([running("a", 100), running("b", 100)], state.positions, 200);
  state = orderSessions([{ ...running("a", 100, 300), status: "idle" }, running("b", 100)], state.positions, 300);
  state = orderSessions([{ ...running("b", 100, 400), status: "idle" }, { ...running("a", 100, 300), status: "idle" }], state.positions, 400);
  expect(ids(state)).toEqual(["b", "a"]);
  state = orderSessions([{ ...running("a", 200, 500, "new-a"), status: "idle" }, { ...running("b", 100, 400), status: "idle" }], state.positions, 500);
  expect(ids(state)).toEqual(["b", "a"]);
});
