import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Markdown } from "./Markdown";
import { FileReferenceScope, isLocalFileLink } from "./FileLinks";

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("a local report link opens the existing file popup and keeps the chat URL", async () => {
  const initial = window.location.href;
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === "POST" ? { id: "report", name: "report.md", mime: "text/markdown", kind: "document", size: 32, characters: 32, pages: null, image_count: 0, status: "prepared", content_url: "/api/artifacts/report/content" } : { text: "# SDK report\n\nComplete report.", summary: "", images: [] }), { headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  render(<FileReferenceScope sessionId="session" itemId="turn:message"><Markdown text="[Full report](/tmp/claude-1000/report.md)" /></FileReferenceScope>);
  fireEvent.click(screen.getByRole("button", { name: "Full report" }));
  expect(await screen.findByRole("dialog", { name: "Preview of report.md" })).toBeTruthy();
  expect(await screen.findByText(/Complete report/)).toBeTruthy();
  expect(screen.getByRole("dialog").closest("p")).toBeNull();
  expect(screen.getByRole("button", { name: "Copy file content" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Download" }).getAttribute("download")).toBe("report.md");
  expect(fetch.mock.calls[0][0]).toBe("/api/sessions/session/files/preview");
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual({ item_id: "turn:message", href: "/tmp/claude-1000/report.md" });
  expect(window.location.href).toBe(initial);
  expect(document.querySelector('a[target="_blank"]')).toBeNull();
});

test("missing files show an error inside the popup without navigating", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "The referenced file is no longer available." }), { status: 404, headers: { "content-type": "application/json" } })));
  render(<FileReferenceScope sessionId="session" itemId="turn:message"><Markdown text="[Missing report](/tmp/missing.md)" /></FileReferenceScope>);
  fireEvent.click(screen.getByRole("button", { name: "Missing report" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "The referenced file is no longer available.");
  fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("web and application navigation links retain their normal behavior", () => {
  render(<FileReferenceScope sessionId="session" itemId="turn:message"><Markdown text="[Website](https://example.com/report.md) [Chat](/sessions/other)" /></FileReferenceScope>);
  expect(screen.getByRole("link", { name: "Website" }).getAttribute("target")).toBe("_blank");
  expect(screen.getByRole("link", { name: "Chat" }).getAttribute("href")).toBe("/sessions/other");
  expect(isLocalFileLink("http://localhost:8767/tmp/report.md", "http://localhost:8767")).toBe(true);
  expect(isLocalFileLink("http://localhost:9000/report.md", "http://localhost:8767")).toBe(false);
  expect(isLocalFileLink("file:///tmp/report.md")).toBe(true);
  expect(isLocalFileLink("C:\\Users\\name\\report.md")).toBe(true);
  expect(isLocalFileLink("C:%5CUsers%5Cname%5Creport.md")).toBe(true);
  expect(isLocalFileLink("../reports/summary.md#L12")).toBe(true);
  expect(isLocalFileLink("javascript:alert(1)")).toBe(false);
});
