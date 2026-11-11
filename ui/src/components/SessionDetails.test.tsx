import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SessionDetails } from "./SessionDetails";
import type { Session } from "../api";

const session = { id: "agent-one", title: "Review the SDK", cwd: "/repo", model: "gpt-6.1-sol", source: "agents", status: "saved", created_at: 1, updated_at: 2 } as Session;
const details = {
  session_id: session.id,
  recorded: { status: "available", source: "Recorded session", settings: { model: "gpt-6.1-sol", effort: "high", sandbox: "workspace-write" }, configured_settings: { effort: "low" }, metadata: { parent_id: "parent", git: { branch: "feature/sdk" } }, context: { tokens_used: 1200, session_summary: "Continue the SDK review", model_context_window: 200000 }, instructions: [{ source: "/repo/AGENTS.md", text: "Use the existing test runner." }], memories: [], skills: [{ name: "sdk-review", path: "/skills/sdk-review/SKILL.md", description: "Review API clients" }], sources: { "settings.effort": "Latest recorded turn context" } },
  current: {
    scope: "current_workspace_runtime", cwd: "/repo",
    config: { status: "available", source: "Current workspace configuration", settings: { model_reasoning_effort: "low" }, layers: [] },
    skills: { status: "available", source: "Workspace skill inventory", items: [{ name: "python-review", description: "Review Python changes", path: "/skills/python/SKILL.md", enabled: true, scope: "user" }] },
    mcp: { status: "available", source: "Thread runtime", scope: "current_thread_runtime", items: [{ name: "docs", auth_status: "oauth", runtime_status: "ready", tools: [{ name: "search_docs", description: "Search official docs" }], resource_count: 2, resource_template_count: 0 }] },
    plugins: { status: "unavailable", source: "Runtime plugin inventory", message: "Plugin inventory is not supported by this runtime" },
    apps: { status: "available", source: "Runtime apps", items: [] },
  }, warnings: [],
};
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(details), { headers: { "content-type": "application/json" } })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("recorded effort is shown independently of current workspace defaults", async () => {
  render(<SessionDetails session={session} close={vi.fn()} />);
  const [effort] = await screen.findAllByText("Reasoning effort");
  expect(effort.nextElementSibling?.textContent).toContain("high");
  expect(effort.nextElementSibling?.getAttribute("title")).toBe("Latest recorded turn context");
  expect(screen.getByText("feature/sdk")).toBeTruthy();
  expect(screen.getByText("Current thread configuration")).toBeTruthy();
  expect(screen.getByText("low", { selector: "span" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Context" }));
  expect(screen.getByText("Continue the SDK review")).toBeTruthy();
  fireEvent.click(screen.getByText("AGENTS.md"));
  expect(screen.getByText("Use the existing test runner.")).toBeTruthy();
  expect(screen.getByText("sdk-review")).toBeTruthy();
  expect(await screen.findByText("python-review")).toBeTruthy();
  expect(screen.getByText("Current workspace defaults")).toBeTruthy();
});

test("integrations show the runtime scope, tool details, and unavailable sections without inventing empty inventories", async () => {
  render(<SessionDetails session={session} close={vi.fn()} />);
  await screen.findAllByText("Reasoning effort");
  fireEvent.click(screen.getByRole("tab", { name: "Integrations" }));
  expect(screen.getByText("Current thread runtime")).toBeTruthy();
  fireEvent.click(await screen.findByText("docs", { selector: "span" }));
  expect(screen.getByText("search_docs")).toBeTruthy();
  expect(screen.getByText("Search official docs")).toBeTruthy();
  expect(screen.getByText("Plugin inventory is not supported by this runtime")).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox", { name: "Filter integrations" }), { target: { value: "unmatched" } });
  expect(screen.queryByText("docs", { selector: "span" })).toBeNull();
  expect(screen.getByText("No matching MCP servers")).toBeTruthy();
});

test("details failures can be retried without closing the session", async () => {
  let failed = true;
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(failed ? { error: "Runtime disconnected" } : details), { status: failed ? 503 : 200, headers: { "content-type": "application/json" } })));
  render(<SessionDetails session={session} close={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Runtime disconnected");
  failed = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh session details" }));
  expect(await screen.findAllByText("Reasoning effort")).toHaveLength(2);
  expect(within(screen.getByRole("dialog")).queryByRole("alert")).toBeNull();
});

test("configured MCP fallback does not claim an empty runtime tool inventory", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...details, current: { ...details.current, mcp: { status: "available", partial: true, source: "config/read configured MCP names", scope: "current_workspace_runtime", message: "Configured servers only. Connection status and tools could not be read from the runtime.", items: [{ name: "docs", enabled: true, runtime_status: "unknown", tools: [] }] } } }), { headers: { "content-type": "application/json" } })));
  render(<SessionDetails session={session} close={vi.fn()} />);
  await screen.findAllByText("Reasoning effort");
  fireEvent.click(screen.getByRole("tab", { name: "Integrations" }));
  expect(await screen.findByText("Configured")).toBeTruthy();
  expect(screen.getByText("Configured servers only.", { exact: false })).toBeTruthy();
  fireEvent.click(await screen.findByText("docs", { selector: "span" }));
  expect(screen.getByText("Tool inventory unavailable")).toBeTruthy();
  expect(screen.queryByText("Tools (0)")).toBeNull();
});

test("shows recorded context before runtime inventories and loads each integration independently", async () => {
  const calls: string[] = [];
  let finishApps!: (response: Response) => void;
  const pendingApps = new Promise<Response>(resolve => { finishApps = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const section = new URL(input, "http://localhost").searchParams.get("section");
    calls.push(section ?? "all");
    if (section === "apps") return pendingApps;
    const current: Record<string, unknown> = { scope: details.current.scope, cwd: details.current.cwd };
    for (const name of ["config", "skills", "mcp", "plugins", "apps"]) current[name] = { status: "not_requested", source: name, items: [] };
    if (section && section !== "recorded") current[section] = details.current[section as "mcp"];
    return new Response(JSON.stringify({ ...details, current }), { headers: { "content-type": "application/json" } });
  }));
  render(<SessionDetails session={session} close={vi.fn()} />);
  await screen.findAllByText("Reasoning effort");
  expect(calls).toEqual(["recorded"]);
  fireEvent.click(screen.getByRole("tab", { name: "Context" }));
  expect(await screen.findByText("Use the existing test runner.")).toBeTruthy();
  expect(await screen.findByText("python-review")).toBeTruthy();
  expect(calls).toContain("skills");
  expect(calls).not.toContain("apps");
  fireEvent.click(screen.getByRole("tab", { name: "Integrations" }));
  expect(await screen.findByText("docs", { selector: "span" })).toBeTruthy();
  expect(screen.getByText("Loading apps")).toBeTruthy();
  finishApps(new Response(JSON.stringify(details), { headers: { "content-type": "application/json" } }));
  expect(await screen.findByText("No apps reported")).toBeTruthy();
});

test("shows the last turn input against its known context limit and retains source provenance", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...details,
    recorded: { ...details.recorded, context: { token_usage: { last: { input_tokens: 50000 }, total: { total_tokens: 900000 }, model_context_window: 200000 } } }
  }), { headers: { "content-type": "application/json" } })));
  render(<SessionDetails session={session} close={vi.fn()} />);
  expect((await screen.findByRole("meter", { name: "Last turn input compared with context window" })).getAttribute("aria-valuenow")).toBe("25");
  expect(screen.getByText("Last turn input")).toBeTruthy();
  expect(screen.getByText("50,000 / 200,000 tokens")).toBeTruthy();
});
