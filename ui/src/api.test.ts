import { expect, test } from "vitest";
import { mergeItems, reasoningOf, toolOutput } from "./api";

test("refresh updates streaming items without duplicating them", () => {
  expect(mergeItems([{ id: "a", type: "agentMessage", text: "hel" }], [{ id: "a", type: "agentMessage", text: "hello" }, { id: "b", type: "userMessage" }]))
    .toEqual([{ id: "a", type: "agentMessage", text: "hello" }, { id: "b", type: "userMessage" }]);
});

test("older pages prepend without overwriting newer item content", () => {
  expect(mergeItems([{ id: "b", type: "agentMessage", text: "newer" }], [{ id: "a", type: "userMessage" }, { id: "b", type: "agentMessage", text: "old" }], true))
    .toEqual([{ id: "a", type: "userMessage" }, { id: "b", type: "agentMessage", text: "newer" }]);
});

test("reasoning follows native headings and placeholder rules without losing Markdown", () => {
  expect(reasoningOf({ summary: ["**Inspecting images**\n<!-- -->", "", "First **check**.\n\n- One\n- Two"] })).toEqual({ heading: "Inspecting images", content: "First **check**.\n\n- One\n- Two" });
  expect(reasoningOf({ summary: ["**Checking files**\nActual summary"] })).toEqual({ heading: "Checking files", content: "Actual summary" });
  expect(reasoningOf({ summary: ["<!-- -->"], content: ["Fallback content"] }).content).toBe("Fallback content");
  expect(reasoningOf({ summary: ["**Heading only**"] }).content).toBe("**Heading only**");
  expect(reasoningOf({ summary: ["<!-- -->"] }).content).toBe("");
});

test("native multi-part summaries use the first part as their heading", () => {
  expect(reasoningOf({ summary: ["Inspecting delivery", "Checking **ordering** and cancellation."], content: [] })).toEqual({ heading: "Inspecting delivery", content: "Checking **ordering** and cancellation." });
});

test("tool outputs render structured JSON and omit inline image bytes", () => {
  expect(toolOutput({ type: "mcpToolCall", result: { content: [{ type: "image", data: "private-image-bytes" }] } })).toBe("");
  expect(toolOutput({ type: "mcpToolCall", result: { structuredContent: { memory_token: "abc" }, content: [] } })).toBe('{\n  "memory_token": "abc"\n}');
  expect(toolOutput({ type: "dynamicToolCall", contentItems: [{ type: "inputText", text: "Completed" }, { type: "inputImage", imageUrl: "data:image/png;base64,bytes" }] })).toBe("Completed");
  expect(toolOutput({ type: "functionCallOutput", output: [{ type: "input_image", image_url: "data:image/png;base64,bytes" }] })).toBe("");
});
