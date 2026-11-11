import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FilePreview } from "./FilePreview";
import { FileReferenceScope } from "./FileLinks";
import type { Artifact } from "../api";

const path = "/tmp/claude-1000/project/scratchpad/a-very-long-folder-name/reports/api-contract.md";
const source = "# API contract\n\nKeep **every field**.\n\n| Name | State |\n| --- | --- |\n| Queue | Ready |\n\n```python\nprint('ready')\n```\n";
const file: Artifact & { source_path: string } = { id: "contract", name: "api-contract.md", mime: "text/markdown", kind: "document", size: source.length, characters: source.length, pages: null, image_count: 0, status: "prepared", content_url: "/api/artifacts/contract/content", source_path: path };
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ text: source, summary: "", images: [] }), { headers: { "content-type": "application/json" } })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("Markdown opens rendered with headings and tables, while Source preserves the original document", async () => {
  render(<FilePreview file={file} close={vi.fn()} />);
  expect(await screen.findByRole("heading", { name: "API contract", level: 1 })).toBeTruthy();
  expect(screen.getByRole("table")).toBeTruthy();
  expect(screen.getByText("every field", { selector: "strong" })).toBeTruthy();
  expect(screen.getByText(path).getAttribute("title")).toBe(path);
  fireEvent.click(screen.getByRole("button", { name: "Source" }));
  expect(screen.queryByRole("table")).toBeNull();
  expect(document.querySelector("pre code.language-markdown")?.textContent).toBe(source);
  expect(screen.getByRole("button", { name: "Copy file content" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Download" }).getAttribute("download")).toBe("api-contract.md");
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(screen.getByRole("table")).toBeTruthy();
});

test("relative document links retain their document context and Escape closes only the nested preview", async () => {
  const closed = vi.fn();
  const fetch = vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(init?.method === "POST" ? { ...file, id: "child", name: "child.txt", mime: "text/plain", source_path: "/tmp/child.txt" } : url.includes("/child/") ? { text: "Nested document contents", summary: "", images: [] } : { text: "# API contract\n\n[Related document](../child.txt)", summary: "", images: [] }), { headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  render(<FileReferenceScope sessionId="session" itemId="turn:message"><FilePreview file={file} close={closed} /></FileReferenceScope>);
  fireEvent.click(await screen.findByRole("button", { name: "Related document" }));
  await screen.findByText("Nested document contents");
  const posted = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(posted[1]!.body as string)).toEqual({ item_id: "turn:message", href: "../child.txt", document_id: file.id, document_path: path });
  fireEvent(screen.getByRole("dialog", { name: "Preview of child.txt" }), new Event("cancel", { bubbles: true, cancelable: true }));
  expect(closed).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog", { name: "Preview of child.txt" })).toBeNull();
  expect(screen.getByRole("dialog", { name: "Preview of api-contract.md" })).toBeTruthy();
});

test("Markdown section links scroll within the preview instead of opening another page", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ text: "# API contract\n\n[Go to MCP](#mcp)\n\n## MCP\n\nEndpoint details", summary: "", images: [] }), { headers: { "content-type": "application/json" } })));
  const original = HTMLElement.prototype.scrollIntoView;
  const scroll = vi.fn(); HTMLElement.prototype.scrollIntoView = scroll;
  try {
    render(<FilePreview file={file} close={vi.fn()} />);
    const heading = await screen.findByRole("heading", { name: "MCP", level: 2 });
    fireEvent.click(screen.getByRole("link", { name: "Go to MCP" }));
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.contexts[0]).toBe(heading);
  } finally { HTMLElement.prototype.scrollIntoView = original; }
});
