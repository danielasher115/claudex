import { expect, test } from "vitest";
import type { Artifact, Item } from "./api";
import { collectSources } from "./sessionSources";

const image = (id: string): Artifact => ({ id, name: `${id}.png`, mime: "image/png", kind: "image", size: 10, characters: 0, pages: null, image_count: 1, status: "ready", content_url: `/api/artifacts/${id}/content` });

test("collects sent, generated and viewed files with stable item anchors and strongest provenance", () => {
  const shared = image("shared"), sent = image("sent"), tool = image("tool");
  const items: Item[] = [
    { id: "view", type: "imageView", images: [shared] },
    { id: "generation", type: "record", record: { type: "imageGeneration", savedPath: "/tmp/generated.png", images: [shared] } },
    { id: "user", type: "userMessage", attachments: [sent, shared], images: [sent] },
    { id: "tool", type: "mcpToolCall", images: [tool] },
  ];
  expect(collectSources(items, [shared, image("pending")]).map(({ file, origin, itemId }) => [file?.id, origin, itemId])).toEqual([
    ["shared", "generated", "view"], ["sent", "attached", "user"], ["tool", "viewed", "tool"], ["pending", "attached", ""],
  ]);
  expect(collectSources(items)[0].file?.source_path).toBe("/tmp/generated.png");
});

test("extracts public Markdown, reference and plain web links while retaining the recorded item ID", () => {
  const sources = collectSources([
    { id: "outer", type: "record", record: { id: "inner", type: "message", role: "assistant", content: [{ type: "output_text", text: "[Report](</tmp/my report.md:12>) [Docs][docs] https://example.com/plain).\n\n[docs]: https://example.com/docs\n\n[Windows](C:%5CUsers%5Cme%5Creport.md:3)" }] } },
    { id: "search", type: "webSearch", action: { type: "openPage", url: "https://example.com/docs" }, results: [{ title: "Result", url: "https://example.com/result" }] },
    { id: "citation", type: "agentMessage", text: "Evidence", citations: [{ url: "https://example.com/cited", title: "Cited" }] },
  ]);
  expect(sources.map(({ href, name, itemId }) => [href, name, itemId])).toEqual([
    ["/tmp/my report.md:12", "Report", "outer"], ["https://example.com/docs", "Docs", "outer"],
    ["https://example.com/plain", "https://example.com/plain", "outer"], ["C:%5CUsers%5Cme%5Creport.md:3", "Windows", "outer"],
    ["https://example.com/result", "Result", "search"], ["https://example.com/cited", "Cited", "citation"],
  ]);
});

test("ignores code examples, unsafe URLs and private tool, command or system text", () => {
  expect(collectSources([
    { id: "assistant", type: "agentMessage", text: "`[private](/secret.md)`\n\n```md\n[example](/example.md) https://example.com/code\n```\n\n    [indented](/indented.md)\n\n\\[escaped](/escaped.md) [bad](javascript:alert) [data](data:text/plain,bad) [remote](file://remote/secret.md)", annotations: [{ type: "url_citation", url: "javascript:alert(1)" }] },
    { id: "command", type: "commandExecution", command: "curl https://example.com/secret", aggregatedOutput: "[token](/token.md)" },
    { id: "system", type: "message", role: "system", content: "[System](/system.md)" },
    { id: "tool", type: "mcpToolCall", arguments: { url: "https://example.com/token" }, result: { url: "https://example.com/token" } },
  ])).toEqual([]);
});

test("accepts prepared record-wrapper attachments and structured content citations without scanning unrelated metadata", () => {
  const file = image("attached");
  expect(collectSources([{ id: "record", type: "record", attachments: [file], record: { type: "message", role: "user", content: [{ type: "input_text", text: "See this", annotations: [{ type: "url_citation", url_citation: { url: "https://example.com/source", title: "Source" } }] }], metadata: { url: "https://example.com/private" } } }]).map(({ file, href, origin, itemId }) => [file?.id, href, origin, itemId])).toEqual([
    ["attached", undefined, "attached", "record"], [undefined, "https://example.com/source", "referenced", "record"],
  ]);
});

test("includes Markdown images and image references when prepared bytes are unavailable", () => {
  expect(collectSources([{ id: "images", type: "agentMessage", text: "![Screenshot](/tmp/screen.png) ![Generated][image]\n\n[image]: https://example.com/generated.png" }]).map(({ name, href, itemId }) => [name, href, itemId])).toEqual([
    ["Screenshot", "/tmp/screen.png", "images"], ["Generated", "https://example.com/generated.png", "images"],
  ]);
});

test("reuses unchanged items, refreshes replacement objects and isolates cached artifacts from result mutation", () => {
  let textReads = 0;
  const file = image("cached");
  const item: Item = { id: "cached-item", type: "agentMessage", attachments: [file], get text() { textReads++; return "[First](https://example.com/first)"; } };
  const first = collectSources([item]);
  first[0].file!.name = "mutated.png";
  first[1].name = "Mutated";
  const second = collectSources([item]);
  expect(textReads).toBe(1);
  expect(second.map(source => source.name)).toEqual(["cached.png", "First"]);
  expect(file.name).toBe("cached.png");
  expect(collectSources([{ ...item, text: "[Updated](https://example.com/updated)" }]).map(source => source.name)).toEqual(["cached.png", "Updated"]);
});
