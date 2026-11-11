---
name: codex-agents
description: Use when delegating local coding, review, research or document work to Codex through the Claudex MCP, monitoring its agents, sending follow-ups, retrieving transcripts or cancelling their tasks. Supports Claude Code callers, attachments and inherited caller context.
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

Instructions, skill inventories, saved memory and compatible preferences inherit by default. Claude Code can additionally export
the exact launching session automatically through the installed PreToolUse context hook. Install it once with:

```bash
codex-session install-claude-context
```

After installation, restart Claude and resume the same conversation so the hook is loaded, then reconnect the Codex MCP.
Reconnecting MCP alone refreshes the bridge and tools; it does not guarantee that a running Claude session loaded a new hook.
The hook is scoped to this gateway's `agents_launch` and compatibility `codex` tools. It preserves the task and explicit target cwd,
captures current user/project rules and memory, and exports the exact caller's public history, including an exact Claude subagent
transcript when a subagent is the caller. It never chooses a different session by modification time. An unreadable required source
denies that launch with a reason; `inherit_context:false` is an explicit opt-out, not a silent fallback.

The rule bundle includes user-root/global and applicable ancestor/project `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, rule files
and recursive `@file` imports. Conditional rule paths remain conditional. Project memory is shared across Git worktrees, and a
configured memory directory is respected. Skill bodies stay at their original paths and are read before use.

The public conversation is reference evidence, not developer instructions. Larger histories use a private immutable snapshot;
the gateway verifies its path, size and SHA256 before launch, and the worker must read the complete snapshot before working.
Private reasoning, system records, credential configuration and binary image/document blocks are not included in this text export.
Binary omissions are reported; supply original files through `files`/`attachments` when the task depends on them. Historical public
text is preserved, so content already pasted into the public conversation remains part of the exported evidence.

An optional `session_summary` adds a concise task handoff; the installed hook makes manual summaries unnecessary for carrying
persisted public conversation history. Without the hook, supply an explicit handoff for live conversation state. Inspect
`inherited_context` for `caller_session_id`, `conversation_messages`, `conversation_sha256`, `conversation_snapshot`, `export_id`,
rule/memory source paths and capture warnings. Saved files alone do not prove that caller history was exported. An unchanged
auto-mode retry keeps its verified export instead of continually changing the tool arguments.

Children inherit the saved bundle and history snapshot. A Claude plan-mode caller is constrained to a read-only Codex sandbox.
Compatible effort preferences translate to native supported levels; explicit task or Codex parent choices take precedence.
Claude tool permission patterns are guidance, not native enforcement. Claude-only hook execution, slash commands, connected tools,
credentials and model internals are not impersonated. Report missing capabilities instead of claiming identical runtime behavior.

## Continue, observe and collect

- `agents_list` returns compact live agents by default: active, idle and starting sessions, excluding archived history.
- Completed gateway agents with no queued follow-ups are automatically unsubscribed. Native Codex unloads them after its inactivity grace period; a saved thread remains resumable with `agents_send`. Another client's subscription can keep it loaded. Result and transcript reads do not reload it.
  Use `filter:"saved"` for unloaded saved sessions, `filter:"archived"` for archived sessions, or `filter:"all"` for every status.
  Pages are ordered by `thread_id`, with `limit` defaulting to 25 and bounded to 1-100. Pass `next_cursor` as `cursor` with the same filter
  to continue; a null cursor means the last page. `count` is the number of agents in this page, and `connection` reports runtime availability.
  Rows contain `thread_id` (`id` is a compatibility alias), `name`, `status`, `cwd`, `model`, `parent_id`, `origin` and `updated_at`.
  Use `agents_status` or `agents_transcript` when more detail is needed.
- Save the returned `thread_id` and `turn_id`. For MCP task-capable clients, save the returned `taskId` instead and use `tasks/get`.
- Continue independent parent work. `agents_wait` waits at most 60 seconds per call and can return `queued`, `starting` or `running`.
- Retrieve `agents_result` for the final report, changed files, commands, attachments, child references and structured JSON.
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
