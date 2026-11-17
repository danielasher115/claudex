import { expect, test } from "vitest";
import { workDuration, type WorkBlock } from "./timeline";

test("native work recaps use explicit item times, completed turns and recorded durations", () => {
  const block: WorkBlock = { id: "w", type: "work", turnId: "t", items: [], active: false,
    turn: { id: "t", type: "turn", started_at: "2026-11-17T14:00:00+02:00", completed_at: "2026-11-17T12:01:05Z", duration_ms: 65000 },
    final: { id: "f", type: "agentMessage", _started_at: Date.parse("2026-11-17T12:01:00Z") },
  };
  expect(workDuration(block, 0)).toBe(60);
  expect(workDuration({ ...block, final: { id: "f", type: "agentMessage" } }, 0)).toBe(65);
  expect(workDuration({ ...block, turn: { id: "t", type: "turn", duration_ms: 65000 } }, 0)).toBe(65);
  expect(workDuration({ ...block, turn: undefined }, 0)).toBeNull();
});
