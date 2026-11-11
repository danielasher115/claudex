import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Markdown } from "./Markdown";

afterEach(cleanup);

test("an unfenced JSON reply becomes a readable highlighted code block", () => {
  const { container } = render(<Markdown text={'{"memory_token":"INHERIT-73","decision":"preserve boundaries"}'} />);
  const code = container.querySelector("pre code.language-json");
  expect(code?.textContent).toBe('{\n  "memory_token": "INHERIT-73",\n  "decision": "preserve boundaries"\n}\n');
});

test("ordinary Markdown and invalid partial JSON are left intact", () => {
  const { container, rerender } = render(<Markdown text="**Keep this emphasis**" />);
  expect(container.querySelector("strong")?.textContent).toBe("Keep this emphasis");
  rerender(<Markdown text={'{"memory_token":'} />);
  expect(container.querySelector("pre")).toBeNull();
});
