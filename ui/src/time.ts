/** Native timestamps arrive as Unix seconds, milliseconds, or ISO strings with an explicit offset. */
export function timestamp(value: unknown): number | null {
  if (typeof value === "string") {
    if (!value.trim()) return null;
    if (/^-?\d+(\.\d+)?$/.test(value)) return timestamp(Number(value));
    else if (/T.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) value = Date.parse(value);
    else return null;
  } else if (typeof value === "number") {
    value = Math.abs(value) < 100_000_000_000 ? value * 1000 : value;
  } else return null;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Number.isFinite(new Date(value).getTime()) ? value : null;
}

const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const formats = new Map<string, { clock: Intl.DateTimeFormat; exact: Intl.DateTimeFormat; day: Intl.DateTimeFormat }>();
function formatters(zone = localZone) {
  let cached = formats.get(zone);
  if (!cached) {
    cached = {
      clock: new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: zone }),
      exact: new Intl.DateTimeFormat("en-GB", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "short", timeZone: zone }),
      day: new Intl.DateTimeFormat("en-GB", { year: "numeric", month: "short", day: "numeric", timeZone: zone }),
    };
    formats.set(zone, cached);
  }
  return cached;
}

export function localTime(value: unknown, zone?: string): string {
  const at = timestamp(value);
  return at === null ? "" : formatters(zone).clock.format(at);
}

export function exactTime(value: unknown, zone?: string): string {
  const at = timestamp(value);
  return at === null ? "Time unavailable" : formatters(zone).exact.format(at);
}

export function timeSeparator(value: unknown, previous: unknown, zone?: string): string | null {
  const at = timestamp(value), before = timestamp(previous);
  if (at === null) return null;
  const date = formatters(zone).day.format(at);
  if (before === null || date !== formatters(zone).day.format(before)) return date;
  const minutes = Math.floor((at - before) / 60_000);
  if (minutes < 30) return null;
  const gap = minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
  return `${localTime(at, zone)} · ${gap} later`;
}
