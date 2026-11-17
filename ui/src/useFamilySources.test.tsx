import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Child, Page, Session } from "./api";
import type { SessionResources } from "./components/SessionView";
import { useFamilySources } from "./useFamilySources";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const child = (id: string, origin: Child["origin"] = "native"): Child => ({ thread_id: id, parent_id: "root", name: `Agent ${id}`, depth: 1, cwd: "/repo", origin });
const page = (name: string, cursor: string | null = null): Page => ({ items: [{ id: "turn:file", type: "agentMessage", text: `[${name}](/repo/${name}.md)` }], next_cursor: cursor, source: "codex", warning: null });
const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
const known = (id: string, name: string) => ({ id, sources: [{ id: `link:/repo/${name}.md`, name, href: `/repo/${name}.md`, itemId: "turn:known", origin: "referenced" }] }) as SessionResources;
const options = { rootId: "root", children: [child("native"), child("gateway", "gateway")], sessions: [] as Session[], known: {} as Record<string, SessionResources>, enabled: true };

test("includes unopened native and gateway children, immediate known sources and exact origin scopes", async () => {
  const fetch = vi.fn(async (url: string) => response(url.includes("native") ? page("native", "items:older") : {
    ...page("gateway", "items:older"), items: [{ id: "turn:file", type: "imageGeneration", images: [{
      id: "prepared", name: "gateway", mime: "image/png", kind: "image", content_url: "/api/artifacts/prepared/content",
    }] }],
  }));
  vi.stubGlobal("fetch", fetch);
  const { result, rerender } = renderHook(useFamilySources, { initialProps: { ...options, known: { root: known("root", "root"), native: known("native", "already-open") } } });
  expect(result.current.sources.map(source => [source.name, source.sessionId, source.itemId])).toEqual([["root", "root", "turn:known"], ["already-open", "native", "turn:known"]]);
  await waitFor(() => expect(result.current.sources).toHaveLength(4));
  expect(result.current.sources.find(source => source.name === "gateway")).toMatchObject({ id: "gateway:file:prepared", sessionId: "gateway", itemId: "turn:file", sessionName: "Agent gateway", file: { content_url: "/api/artifacts/prepared/content" } });
  expect(fetch.mock.calls.map(([url]) => url).sort()).toEqual(["/api/sessions/gateway/sources", "/api/sessions/native/sources"]);
  rerender({ ...options, children: [...options.children], known: { root: known("root", "root"), native: known("native", "already-open") } });
  await act(async () => {});
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(result.current.hasEarlier).toBe(true);
});

test("limits concurrency to two and reads one bounded older batch only when requested", async () => {
  const pending: { url: string; resolve: (response: Response) => void; signal?: AbortSignal }[] = [];
  const fetch = vi.fn((url: string, init: RequestInit) => new Promise<Response>(resolve => pending.push({ url, resolve, signal: init.signal ?? undefined })));
  vi.stubGlobal("fetch", fetch);
  const { result, rerender, unmount } = renderHook(useFamilySources, { initialProps: { ...options, children: [child("a"), child("b"), child("c"), child("d")] } });
  await waitFor(() => expect(pending).toHaveLength(2));
  rerender({ ...options, children: [child("a"), child("b"), child("c"), child("d")] });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { pending[0].resolve(response(page("a", "items:older"))); pending[1].resolve(response(page("b", "items:older"))); });
  await waitFor(() => expect(pending).toHaveLength(4));
  await act(async () => { pending[2].resolve(response(page("c", "items:older"))); pending[3].resolve(response(page("d", "items:older"))); });
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(fetch).toHaveBeenCalledTimes(4);
  act(() => result.current.loadEarlier());
  await waitFor(() => expect(pending).toHaveLength(6));
  expect(pending.slice(4).every(request => request.url.endsWith("/sources?cursor=items%3Aolder"))).toBe(true);
  await act(async () => { pending[4].resolve(response(page("old-a", "still-older"))); pending[5].resolve(response(page("old-b", "still-older"))); });
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(fetch).toHaveBeenCalledTimes(6);
  unmount();
});

test("does no hidden work and cancels pending requests when the family changes or unmounts", async () => {
  const pending: { resolve: (response: Response) => void; signal: AbortSignal }[] = [];
  vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise<Response>(resolve => pending.push({ resolve, signal: init.signal as AbortSignal }))));
  const { result, rerender, unmount } = renderHook(useFamilySources, { initialProps: { ...options, enabled: false } });
  expect(pending).toHaveLength(0);
  rerender(options);
  await waitFor(() => expect(pending).toHaveLength(2));
  rerender({ ...options, rootId: "different", children: [child("new")] });
  await waitFor(() => expect(pending).toHaveLength(3));
  expect(pending[0].signal.aborted && pending[1].signal.aborted).toBe(true);
  await act(async () => { pending[0].resolve(response(page("stale"))); pending[1].resolve(response(page("stale"))); });
  expect(result.current.sources).toEqual([]);
  unmount(); expect(pending[2].signal.aborted).toBe(true);
});

test("refreshes changed revisions at most once per ten seconds and leaves stable revisions cached", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(async () => response(page("new")));
  vi.stubGlobal("fetch", fetch);
  const initial = { ...options, children: [child("native")], sessions: [{ id: "native", revision: "one" } as Session] };
  const { rerender } = renderHook(useFamilySources, { initialProps: initial });
  await act(async () => {});
  expect(fetch).toHaveBeenCalledTimes(1);
  rerender({ ...initial, sessions: [{ id: "native", revision: "two" } as Session] });
  await act(async () => { await vi.advanceTimersByTimeAsync(9_999); });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("restarts earlier pagination when the latest source page advances beyond its previous overlap", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(async (_url: string) => response({ ...page("first", "old-cursor"), items: [{ id: "first", type: "agentMessage", text: "[First](/first.md)" }] }))
    .mockImplementationOnce(async () => response({ ...page("first", "old-cursor"), items: [{ id: "first", type: "agentMessage", text: "[First](/first.md)" }] }))
    .mockImplementationOnce(async () => response(page("old", null)))
    .mockImplementationOnce(async () => response({ ...page("new", "gap-cursor"), items: [{ id: "new", type: "agentMessage", text: "[New](/new.md)" }] }));
  vi.stubGlobal("fetch", fetch);
  const initial = { ...options, children: [child("native")], sessions: [{ id: "native", revision: "one" } as Session] };
  const { result, rerender } = renderHook(useFamilySources, { initialProps: initial });
  await act(async () => {});
  act(() => result.current.loadEarlier());
  await act(async () => {});
  expect(result.current.hasEarlier).toBe(false);
  rerender({ ...initial, sessions: [{ id: "native", revision: "two" } as Session] });
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(result.current.hasEarlier).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(3);
  act(() => result.current.loadEarlier());
  await act(async () => {});
  expect(fetch.mock.calls[3][0]).toBe("/api/sessions/native/sources?cursor=gap-cursor");
});

test("reports failed pages without repeated requests and retries the same failed cursor explicitly", async () => {
  const fetch = vi.fn(async (_url: string) => response(page("older")))
    .mockRejectedValueOnce(new Error("Latest unavailable"))
    .mockResolvedValueOnce(response(page("latest", "older")))
    .mockRejectedValueOnce(new Error("Earlier unavailable"));
  vi.stubGlobal("fetch", fetch);
  const { result } = renderHook(useFamilySources, { initialProps: { ...options, children: [child("native")] } });
  await waitFor(() => expect(result.current.error).toBe("Latest unavailable"));
  expect(result.current.loading).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(1);
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.sources).toHaveLength(1));
  act(() => result.current.loadEarlier());
  await waitFor(() => expect(result.current.error).toBe("Earlier unavailable"));
  expect(fetch).toHaveBeenCalledTimes(3);
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.sources).toHaveLength(2));
  expect(fetch.mock.calls[3][0]).toBe("/api/sessions/native/sources?cursor=older");
  expect(result.current.error).toBe("");
});
