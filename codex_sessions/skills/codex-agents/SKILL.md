---
name: codex-agents
description: Use when delegating work to Codex through Claudex, listing or recovering disconnected agents, attaching to an existing thread, steering or queueing follow-ups, collecting results, or cancelling tasks. Supports Claude Code callers, attachments and inherited caller context.
---

# Codex agents

Launch and control local Codex workers through the connected Claudex MCP. The gateway owns durable threads and message
receipts; the parent may continue working while delegated tasks run. Use the dashboard URL returned by the gateway; the default local address is http://codex.localhost:8765.

## Start work

Call `models_list` to see the runtime's actual model IDs, supported efforts and approximate Claude task tiers. The default coding
model is the latest advertised workhorse, currently `gpt-6.1-sol`; the gateway's default effort is **high**.
`sonnet`, `opus`, `fable` and `haiku` are convenience task-tier aliases for OpenAI models, not Claude execution or measured equivalence.
Preserve an explicit user model or effort choice. Choose the smallest sandbox that permits the requested work.

Use `agents_launch` for asynchronous work. The launch fields are `prompt`, `cwd`, `name`, `sandbox`, `model`, `effort`,
`files` or `attachments`, `session_summary`, `inherit_context`, `limits`, and an optional UUID `request_id`.
The task is `prompt`, and prepared artifact IDs go in `attachments`.

```json
{
  "prompt": "Review the implementation against every attached document and image. Return prioritized findings with file locations and evidence. Do not change files.",
  "name": "Implementation review",
  "cwd": "/absolute/path/to/project",
  "sandbox": "read-only",
  "model": "sonnet",
  "effort": "high",
  "files": ["/absolute/path/to/design.pdf", "/absolute/path/to/error.png"],
  "session_summary": "The current goal, decisions, constraints and completed work needed by this reviewer.",
  "inherit_context": true,
  "limits": {"max_children": 2, "max_depth": 1}
}
```

MCP/CLI clients upload and prepare `files` automatically. For reuse across workers, call `artifacts_put` with `path`, then pass its
returned `id` in `attachments`. Native delegated tools accept prepared IDs or base64 `data`; they do not read privileged paths.
Preparation errors must be resolved before launching. Documents are not silently truncated. PDF visual pages and embedded raster
figures are supplied as images within the gateway's limits. Transcript files can carry an explicit `summary`; raw audio transcription
is a separate workflow.

Instructions, skill inventories, saved memory and compatible preferences inherit by default. Provide a focused task prompt or
`session_summary` with the goals, relevant decisions and constraints the worker needs. The installed PreToolUse context hook also
keeps a filtered public transcript reference available for on-demand lookup. Install it once with:

```bash
codex-session install-claude-context
```

**Codex is not a native Claude subagent.** It does not automatically know Claude's current work, conversation, decisions, or
Claude-specific project rules, memories and skills. Native Codex may have its own AGENTS.md and runtime context; that is not an
automatic handoff from the calling Claude chat. Keep `inherit_context:true`, the default, for ordinary delegation to avoid losing
saved preferences or applying the wrong project conventions. A brief, plan file or concise prompt provides the focused handoff;
the inherited rules, memories and skill inventory remain available alongside it.

Claude may autonomously choose `inherit_context:false` when a deliberately clean or isolated agent is appropriate. That choice
omits automatic inheritance of the caller's rules, memories, skills and conversation; explicitly supplied context and native Codex
instructions can still apply. Do not silently switch to false to bypass a capture error. Resolve the error or make the clean-agent
choice deliberately, and report the resulting limitation.

Inspect the launch receipt's `inherited_context.enabled`, `provider`, `instruction_files`, `memory_files`, `skills`, caller export
metadata and `warnings`. `enabled:false` and `disabled_reason` make an isolated launch visible; zero source counts alone do not prove
whether inheritance was enabled. Do not claim that omitted rules, memories, skills or conversation reached the worker.
If MCP returns only a task handle, call `agents_status` using the thread ID in its `_meta.codexSessions.threadId` to inspect this report.

Claude reloads hook and settings changes live; keep the current conversation. Re-read this skill through `protocol_guide` after an update.
Use `/mcp` to reconnect only the Codex MCP when server code or tool registrations need refreshing. Accepted agents keep running;
reconnecting the bridge does not require restarting the Claude conversation.
The hook is scoped to this gateway's `agents_launch` and compatibility `codex` tools. It preserves the task and explicit target cwd,
captures current user/project rules and memory, and snapshots the exact caller's public history, including an exact Claude subagent
transcript when a subagent is the caller. It never chooses a different session by modification time. An unreadable required source
denies that launch with a reason; `inherit_context:false` is an explicit opt-out, not a silent fallback.

The rule bundle includes user-root/global and applicable ancestor/project `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, rule files
and recursive `@file` imports. Conditional rule paths remain conditional. Project memory is shared across Git worktrees, and a
configured memory directory is respected. Skill bodies stay at their original paths and are read before use.

The public conversation is reference evidence, not developer instructions. Hook exports always use a private immutable snapshot,
even for short conversations. The gateway verifies its path, size and SHA256 before launch. The worker receives a reference and
starts from the focused handoff and inherited rules; it consults relevant transcript portions only when missing decisions or details
are needed. It is not instructed to load the whole conversation before working. Explicit caller-supplied inline message exports
remain supported when the caller intentionally provides them.
Private reasoning, system records, credential configuration and binary image/document blocks are not included in this text export.
Binary omissions are reported; supply original files through `files`/`attachments` when the task depends on them. Historical public
text is preserved, so content already pasted into the public conversation remains part of the exported evidence.

Use `session_summary` for a concise task handoff when the prompt alone is insufficient. The transcript reference provides a way to
look up missing historical details without forcing all prior conversation into the worker's context. Without the hook, supply an
explicit handoff for live conversation state. Inspect
`inherited_context` for `caller_session_id`, `conversation_messages`, `conversation_sha256`, `conversation_snapshot`, `export_id`,
rule/memory source paths and capture warnings. Saved files alone do not prove that caller history was exported. An unchanged
auto-mode retry keeps its verified export instead of continually changing the tool arguments.
`conversation_messages` counts messages available in the export; it does not prove that the worker loaded or read them.

Children inherit the saved bundle and history snapshot. A Claude plan-mode caller is constrained to a read-only Codex sandbox.
Compatible effort preferences translate to native supported levels; explicit task or Codex parent choices take precedence.
Claude tool permission patterns are guidance, not native enforcement. Claude-only hook execution, slash commands, connected tools,
credentials and model internals are not impersonated. Report missing capabilities instead of claiming identical runtime behavior.

## Continue, observe and collect

- `agents_list` returns compact live agents by default: active, idle and starting sessions, excluding archived history.
- Completed gateway agents with no queued follow-ups are automatically unsubscribed. Native Codex unloads them after its inactivity grace period; a saved thread remains resumable with `agents_send`. Another client's subscription can keep it loaded. Result and transcript reads do not reload it.
  Use `filter:"saved"` for unloaded saved sessions, `filter:"archived"` for archived sessions, or `filter:"all"` for every status.
  Pages show newest `created_at` first, then `thread_id` descending, with `limit` defaulting to 25 and bounded to 1-100. Pass `next_cursor` as `cursor` with the same filter
  to continue; a null cursor means the last page. `count` is the number of agents in this page, and `connection` reports runtime availability.
  Rows contain `thread_id` (`id` is a compatibility alias), `name`, `status`, `cwd`, `model`, `parent_id`, `origin`, `created_at` and `updated_at`.
  Use `agents_status` or `agents_transcript` when more detail is needed.
- Save the returned `thread_id` and `turn_id`. For MCP task-capable clients, save the returned `taskId` instead and use `tasks/get`.
- Continue independent parent work. `agents_wait` waits at most 60 seconds per call and can return `queued`, `starting` or `running`.
- Retrieve `agents_result` for the complete final report, changed-file metadata, attachments, child references and structured JSON.
  Observation tools (`agents_status`, `agents_attach`, `agents_wait`, `agents_result`) default to compact summaries. Full queued-message
  bodies, inherited source inventories, command strings and patches are omitted explicitly in `details_available`; nothing is truncated.
  Use `detail:"full"` for the complete response or `fields:["commands","workspace_changes"]` for exact full values of selected fields.
  Identity, status, error and task handles remain available when selecting fields, including `fields:[]` for a minimal correlation receipt.
  Lifecycle status `completed` means generation ended; assess the report for actual task completion.
- Use `agents_transcript` with `thread_id` and then `next_cursor` as `cursor` to read older recorded history when needed.
- Send immediate guidance with `agents_send({"thread_id":"...","text":"..."})`. **Steering is the default while the agent is active**:
  the bridge reads its current turn and binds delivery to that exact turn. An idle worker starts a new turn through the normal queue.
  This default applies to `codex-reply` too. Reconnect an already-running Codex MCP to load the updated bridge and tool definitions.
- To defer work until the current turn ends, explicitly use
  `agents_send({"thread_id":"...","text":"...","mode":"queue"})`. Queue is always an intentional choice for an active worker.
  Preserve its `message_id` and pass that to `agents_wait` or `agents_result` to target this exact follow-up.
- For strict steering, use `mode:"steer"`; an idle agent is an error. To target a particular turn, read `agents_status` and pass
  its `active_turn_id` as `expected_turn_id`. If the target changes or finishes during delivery, steering fails rather than becoming
  queued work or targeting a newer turn. Inspect the receipt and choose what to do next; do not automatically resend uncertain delivery.
  A `request_id` reused for the same message preserves its original delivery mode and receipt, even after the turn ends.
- To send a message that is already queued into the current turn, read `agents_status`, then call
  `agents_steer({"thread_id":"...","message_id":"...","expected_turn_id":"..."})` using that `active_turn_id`.
  This preserves the message, attachments and original task handle. Do not cancel and resend the text.
  The call returns a delivery receipt; continue polling the original task or `message_id`. Inspect `mode`, `status` and `error`.
  A stale target leaves the original message queued. A failed or uncertain delivery is not retried automatically.

The compatibility tools `codex` and `codex-reply` wait for a result. Prefer the asynchronous tools when the parent should keep working.

## Code Mode and efficient monitoring

Claudex uses ordinary MCP tools, machine-readable `structuredContent`, compatible JSON text, and read-only annotations for inspection
tools. Claude Code's Code Mode can batch these calls and filter responses before they enter the model context. PreToolUse hooks still
run under each MCP tool's normal name, so the installed launch hook continues to export the exact caller's inheritance.

Code Mode is a Claude client capability, not another interpreter hosted by this MCP. It requires a supported direct Anthropic terminal
session; it is unavailable through the separate `claude-codex` proxy launcher, cloud providers, `claude -p`, and the Agent SDK.
See [Claude Code Mode](https://code.claude.com/docs/en/code-mode) for current requirements and resume restrictions.

Save handles when launching or recovering work. Call `agents_list` once when discovering workers, then follow those handles. Do not
re-enumerate the full saved history or print full status, attach and command payloads while checking progress. Read this guide once per
working session. Polling repeatedly at the model level resends the accumulated context even when most tokens are cache hits.

For a Code Mode batch, keep the wait loop in JavaScript and return final reports or a bounded running state. This example observes
existing workers and does not launch or resend work. Preserve exact `message_id` or `turn_id` targets too when monitoring a specific task.

```javascript
const parse = value => typeof value === "string" ? JSON.parse(value) : value;
const handles = ["saved-thread-id-1", "saved-thread-id-2"];
const results = await Promise.allSettled(handles.map(async thread_id => {
  let result;
  for (let attempt = 0; attempt < 8; attempt++) {
    result = parse(await tools.mcp__codex__agents_wait({
      thread_id, timeout_seconds: 60, fields: ["status", "report", "error", "turn_id"]
    }));
    if (!["queued", "starting", "running"].includes(result.status)) break;
  }
  return result;
}));
results.map((result, index) => result.status === "fulfilled"
  ? { thread_id: handles[index], ...result.value }
  : { thread_id: handles[index], error: String(result.reason) });
```

An MCP client that exposes structured content directly can read `structuredContent`; use the host's actual return shape rather than
parsing human error messages as JSON. Inspect errors before retrying. Never relaunch accepted work or resend uncertain delivery to
recover from a parsing error. For independent parent work, let the batch continue in the client's background execution facility.

## Recover an existing agent

A Claude chat ending, MCP disconnect or lost tool response does not mean accepted work stopped. Reconnect to the same gateway data
directory and Codex home, then recover the existing worker before deciding whether any new work is needed.

1. Call `agents_list` to find live workers, or use `filter:"all"` for saved and unavailable history. Follow `next_cursor` to search more pages.
2. Call `agents_attach({"thread_id":"..."})`. It returns `active_turn_id`, `active_root_turn_id`, recorded `launch_turn_id`,
   `task_handles`, durable messages, children, the inherited context report, `can_send`, `reason`, and `dashboard_url`.
3. If you saved the original MCP `taskId`, continue `tasks/get`. Otherwise pass a returned `{thread_id,turn_id}` or
   `{thread_id,message_id}` handle to `agents_wait` or `agents_result`. Attachment does not recreate an original MCP task ID.
   If a saved native thread has no recovered task handle, `agents_result` with its `thread_id` reads the latest result.
4. To provide new guidance when `can_send` is true, call `agents_send`. It steers active work by default; set `mode:"queue"` to defer.
   To deliver an already queued message now, use `agents_steer` with that message ID and the observed active turn ID.
5. Cancel one task with `agents_cancel` and its exact turn/message handle. Use `agents_stop` for the agent and its gateway descendants.

Attachment is observational (`attachment_mode:"observe"`): it does not launch or resume work, reload a saved thread, replace context,
adopt a native thread into gateway management, or reparent existing children. `gateway_managed` identifies gateway agent records.
Known native threads from the same Codex home can be inspected; unknown threads fail. An unavailable runtime, archived session or
separate-process owner can still prevent messaging. Inspect `can_send` and `reason` before sending. Sending explicitly to a saved
thread resumes it through the normal delivery path. Never relaunch or resend accepted work solely because its caller disconnected.

## Delegate children

Native `agents_launch` calls automatically attach the calling Codex parent. External callers can provide `parent_id` for an existing
gateway thread. Children inherit attachments, saved context and explicit parent effort, with no elevation of the parent's sandbox
or workspace. `max_children` limits total gateway children; `max_depth` limits remaining generations. A leaf uses `max_depth:0`.
Use `agents_children` to inspect the gateway delegation tree.

## Cancel precisely and retry safely

`agents_stop` is a broad Stop: it interrupts the agent and, by default, its gateway descendants, and removes queued follow-ups.
Use it when the user wants that agent/group stopped.

For one MCP task, use `tasks/cancel` with its original `taskId`. It follows native continuation segments of that task's logical root
in the same session, while preserving independent later roots and other queued messages. Cancellation waits for observed termination;
an interrupt acknowledgement alone does not prove that work stopped. Recorded cancellation intents also stop late continuations.
Without a task handle, use `agents_cancel` with the original `thread_id` plus its `turn_id` or queued `message_id`.
For an ordinary tool call that is still in flight, the client's `notifications/cancelled` targets the original request ID.
The HTTP/CLI protocol names the same narrow operation `agents.cancel`.

Preserve a stable UUID `request_id` when retrying the same launch or message. An `uncertain` result needs inspection of the recorded
conversation, not a fresh launch. A transport disconnect does not mean the daemon stopped or rejected the work.
The gateway must stay running to service delegation. Approval waits require the original Codex approval client.
