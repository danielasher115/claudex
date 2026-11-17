import { expect, test } from "vitest";
import { exactTime, localTime, timeSeparator, timestamp } from "./time";

test("timestamps accept seconds, milliseconds and ISO offsets without inventing missing times", () => {
  const at = Date.parse("2026-11-17T12:05:09Z");
  for (const value of [at / 1000, at, String(at / 1000), "2026-11-17T14:05:09+02:00"]) expect(timestamp(value)).toBe(at);
  for (const value of [undefined, null, "", "unknown", Infinity, {}, "2026-11-17T12:05:09"]) expect(timestamp(value)).toBeNull();
  expect(localTime(undefined)).toBe("");
  expect(exactTime(undefined)).toBe("Time unavailable");
});

test("local clocks and exact hover dates use the viewer zone including daylight saving", () => {
  expect(localTime("2026-07-12T11:22:28Z", "Asia/Jerusalem")).toBe("14:22");
  expect(localTime("2026-11-17T12:05:09Z", "Asia/Jerusalem")).toBe("14:05");
  expect(exactTime("2026-11-17T12:05:09Z", "Asia/Jerusalem")).toContain("14:05:09 GMT+2");
  expect(exactTime("2026-07-12T11:22:28Z", "Asia/Jerusalem")).toContain("14:22:28 GMT+3");
  expect(localTime("2026-11-01T05:30:00Z", "America/New_York")).toBe("01:30");
  expect(localTime("2026-11-01T06:30:00Z", "America/New_York")).toBe("01:30");
  expect(exactTime("2026-11-01T05:30:00Z", "America/New_York")).toContain("GMT-4");
  expect(exactTime("2026-11-01T06:30:00Z", "America/New_York")).toContain("GMT-5");
});

test("timeline separators follow local dates and real elapsed gaps across DST", () => {
  expect(timeSeparator("2026-11-17T21:55:00Z", null, "Asia/Jerusalem")).toBe("17 Nov 2026");
  expect(timeSeparator("2026-11-17T22:05:00Z", "2026-11-17T21:55:00Z", "Asia/Jerusalem")).toBe("18 Nov 2026");
  expect(timeSeparator("2026-11-17T20:25:00Z", "2026-11-17T20:00:00Z", "Asia/Jerusalem")).toBeNull();
  expect(timeSeparator("2026-11-01T06:30:00Z", "2026-11-01T05:30:00Z", "America/New_York")).toBe("01:30 · 1h later");
  expect(timeSeparator(undefined, "2026-11-17T20:00:00Z")).toBeNull();
});
