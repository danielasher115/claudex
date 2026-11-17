# Agent protocol

The dashboard, MCP adapter, CLI, and native agent delegation tools share one local gateway. Launches return a thread ID immediately;
the same thread is visible in the dashboard. Legacy `codex` and `codex-reply` MCP tools remain available for callers that wait for an answer.

## Operations

| Operation | Purpose |
| --- | --- |
| `models.list` | Read the live Codex catalog, reasoning efforts, image support, and approximate Claude task aliases |
| `artifacts.put` / `artifacts.get` | Prepare or inspect an immutable attachment |
| `agents.launch` | Start a root or delegated child agent asynchronously |
| `agents.send` | Steer an active turn by default, or queue a new turn when idle |
| `agents.steer` | Send an existing queued message into a specified active turn |
| `agents.status` | Inspect runtime state, pending messages, children, and context inheritance |
| `agents.attach` | Recover an existing thread's current state and task handles without starting, resuming, or adopting it |
| `agents.wait` | Wait up to 60 seconds for a specified message or turn |
| `agents.result` | Read the final report, structured output, changed files, commands, attachments, and child references |
| `agents.children` | List explicit gateway parent-child relationships |
| `agents.stop` | Interrupt an agent and its gateway descendants; cancel their queued follow-ups |
| `agents.cancel` | Cancel an exact message or turn without stopping newer work |
| `agents.transcript` | Read paginated recorded history |
| `agents.list` | Discover local conversations with compact, paginated rows |

MCP and native tools use underscores, for example `agents_launch`. Read `protocol_guide` before delegating; the same guide is
available as the `codex-sessions://skills/codex-agents` MCP resource and is bundled with the Python package.

## CLI and MCP

```bash
claudex models
claudex call agents.launch --json request.json
```

A protocol call accepts a JSON parameter file or `--json -` to read stdin. The MCP process and CLI resolve the gateway from the
same user data directory and read its local token themselves. Keep `--data-dir` or `CLAUDEX_DATA_DIR` consistent across callers.
See [setup](setup.md#connect-claude-through-mcp) for the stdio MCP configuration.

## Launch

```json
{
  "prompt": "Review the attachments, fix the failing tests, and report the result.",
  "name": "Review and fix",
  "cwd": "/absolute/path/to/project",
  "effort": "high",
  "sandbox": "workspace-write",
  "files": ["/absolute/path/to/design.pdf", "/absolute/path/to/screenshot.png"],
  "inherit_context": true,
  "session_summary": "Current goal, decisions, constraints, and progress from the caller.",
  "limits": {"max_children": 4, "max_depth": 2}
}
```

CLI and MCP clients read and upload each local `files` path. HTTP clients first upload base64 bytes with `artifacts.put`, then pass
the returned IDs in `attachments`. Launch responses include attachment preparation details and a context inheritance report.

An HTTP caller posts this envelope to `/api/protocol` with the local `X-Codex-Token` header:

```json
{
  "protocol_version": "1",
  "method": "agents.launch",
  "params": {"prompt": "Inspect the prepared files", "attachments": ["artifact-id"]}
}
```

Responses include `protocol_version` and `result`. Unsupported operations and versions fail explicitly. Browser actions use
same-origin endpoints and do not receive the protocol token. Keep tokens out of prompts, source control, and bug reports.

## Model selection

`models.list` reads the catalog exposed by your connected runtime. Exact advertised model IDs work. Claude-oriented task aliases
such as `sonnet`, `opus`, and `haiku` choose approximate Codex task roles; they do not execute Claude models or imply benchmark equivalence.
The model picker shows the current mapping. An explicit request takes precedence over defaults, and existing chats retain their model.

The gateway defaults to high reasoning effort when the model supports it. Explicit per-task and compatible inherited preferences
are preserved. Supported efforts and modalities come from the live runtime.

## Files and attachments

Preparation supports UTF-8 text, source, Markdown, CSV, JSON, existing transcripts, PNG/JPEG/WebP/still GIF images, PDF text and
rendered pages, and Office text plus embedded raster images for DOCX/PPTX/XLSX. PDF preparation uses the bundled Python PDFium dependency; no external PDF executables are required.
Unsupported embedded Office vector figures fail explicitly; export those documents as PDF when appropriate.

`artifacts.put` accepts `name`, base64 `data`, optional `kind` (`document`, `image`, `transcript`, `reference`), and an explicit `summary`.
CLI and MCP callers can supply `path`. Images become native image inputs; extracted text and optional transcript summaries accompany
the original downloadable files. Raw audio and video transcription are outside this gateway; attach an existing transcript instead.

Limits are 25 MB per original file, 20 files, 20 supplied images/PDF pages, and 240,000 extracted characters per turn.
Oversized, damaged, protected, unreadable, and unsupported inputs fail before launch instead of being silently truncated.
A preparation manifest records what was extracted and supplied; it cannot prove how a model used that content.

## Message delivery and retries

`agents.send` normally steers: it submits guidance now into the active run without stopping the agent or interrupting its current
tool call. Codex reads the guidance at a native input boundary. Use this for corrections, answers, extra context, and new requirements
that should influence the work already underway. If the agent is idle, omitting mode starts a new iteration.

Choose `mode: "queue"` only when the message should wait until the agent finishes its current work and becomes idle, then start a
follow-up iteration. This can be a long wait: a run may contain many tool calls and waits. Queue does not mean delivery after the
next tool call, reasoning step, or conversational message. Use explicit `mode: "steer"` when an idle agent must be an error.

An optional `expected_turn_id` targets a specific turn. Omitted targets bind to the currently observed turn. A changed or unresolved
active turn fails instead of targeting newer work. The dashboard composer keeps its own Queue/Steer selection.

`agents.send` returns a `message_id`; pass it to `agents.wait` or `agents.result` to follow that exact message.
Without a target, `agents.wait` follows the newest non-cancelled gateway message. Each wait is bounded to 60 seconds and can return
`queued`, `starting`, or `running`. A generation marked `completed` has finished its turn; inspect its report to determine task completeness.

Reuse a stable UUID `request_id` when retrying a launch or send. Retries preserve the original receipt and delivery mode.
Ambiguously accepted sends become `uncertain` and are not automatically repeated. A transport disconnect is not a reason to submit
accepted work again. MCP cancellation resolves pending launch/send ownership before stopping its agent.

## Discover agents

Agent discovery is newest first by creation time, with descending thread ID ties. The opaque cursor carries that ordering and the
selected filter. Keep the filter when loading another page; restart discovery if a cursor from an older gateway version is rejected.

## Compact observations and Code Mode

`agents.status`, `agents.attach`, `agents.wait`, and `agents.result` return compact summaries by default. Final reports and structured
results remain complete. Command strings, patches, queued-message bodies, and inherited file inventories are available explicitly;
`details_available` identifies compacted fields, and command counts, diff availability, source warnings, and task handles remain visible.
Pass `detail: "full"` for the complete response, or `fields: ["report", "workspace_changes"]` for exact values of selected fields.
Identity, status, errors, and task handles are retained when selecting fields. An empty `fields` array requests handles only.

MCP read tools advertise read-only and idempotency hints, and provide structured JSON plus compatible text for older clients.
Claude Code Mode can batch independent calls, filter those values locally, and wait on saved handles without repeated discovery.
The bundled `codex-agents` skill includes a bounded JavaScript monitoring example. No extra code interpreter runs in the gateway.
Native Codex tools, MCP clients, and CLI calls share the same observation policy; dashboard history and internal records remain complete.

Code Mode is a client feature with its own model, account, and resume requirements. See the [official Claude documentation](https://code.claude.com/docs/en/code-mode).
It is supported for eligible direct Anthropic terminal sessions, including this MCP's ordinary launch hooks. It is unavailable through
the separate `claude-codex` proxy launcher. Claudex remains usable through ordinary MCP calls in either setup.

## Recover accepted work

Accepted work belongs to the gateway and Codex runtime. Losing the Claude chat or MCP connection does not prove that the task stopped.
Reconnect to the same gateway data directory and Codex home before trying to recover it; do not launch a replacement task automatically.

1. Call `agents.list` to find live workers. Use `filter: "all"` to include saved or unavailable history, and follow `next_cursor` as needed.
2. Call `agents.attach` with the existing `thread_id`, or `agents_attach` through MCP. It returns current/root turn IDs, the original gateway
   launch turn when recorded, durable `task_handles`, queued message receipts, children, context inheritance, `can_send`, and `dashboard_url`.
3. Continue using an original MCP `taskId` with `tasks/get` if you saved it. Otherwise pass a returned `{thread_id, turn_id}` or
   `{thread_id, message_id}` handle to `agents.wait` or `agents.result`. Attachment does not recreate the original MCP task ID.
4. Send new guidance with `agents.send` only when `can_send` is true. Active workers receive steering by default; use `mode: "queue"` to defer.
   Use `agents.steer` to deliver an existing queued message into a specified active turn without resending it.
5. Use `agents.cancel` with an exact recovered turn/message handle for one task, or `agents.stop` for the agent and its gateway descendants.

Attachment is observational (`attachment_mode: "observe"`). It does not start or resume a turn, subscribe to an unloaded thread,
adopt a native thread into gateway management, replace inherited context, or change parent-child relationships. Known native threads
from the same Codex home can be inspected; `gateway_managed` identifies threads with a gateway agent record. Unknown threads fail.
An unavailable runtime, archived thread, or thread owned by a separate process can still prevent messaging; inspect `can_send` and `reason`.
Attaching to a saved thread does not reload it. An explicit subsequent `agents.send` resumes it through the normal delivery path.

For example, pass this file to `claudex call agents.attach --json request.json`:

```json
{"thread_id": "the-existing-thread-id"}
```

## Caller context

Context inheritance is enabled by default. It can include user and project instructions, rules, saved memory, available skill
names and paths, enabled plugin skills, and portable settings. Skill bodies remain on disk for on-demand reading.
Secret environment values, credential files, and hook executables are not copied into the bundle.

Project imports are confined to the current Git worktree, or the launch directory when no Git worktree exists, plus selected ancestor
rule locations and curated user instruction, skill, and memory roots. Trusted global user instructions may explicitly import external
non-private documents. Project content never gains that user-level trust. Automatic symlink traversal remains inside the selected scope.
User configuration can select an external memory root; project configuration can only select the project or an already selected user scope.
Private credential paths and private-key content are skipped with a path-only warning.

Portable settings include model and effort preferences, language, and output style. Claude permission patterns are retained as guidance;
native Codex sandbox and approval policy remain authoritative. Claude hooks, slash commands, plugin UI state, and connected tools are
not impersonated. The report records settings that could not be translated.

The optional Claude PreToolUse hook exports the exact caller's public conversation, including subagent callers. It does not select
unrelated conversations. Install it with `claudex install-claude-context`. Hook/settings changes reload live, so keep the current
conversation and re-read `protocol_guide`. Reconnect only the Codex MCP through `/mcp` when server code or new tools need refreshing;
accepted agents keep running and the Claude conversation does not need restarting.
`context.caller` identifies the session, workspace, transcript, and permission mode. The hook puts versioned public history in a private
immutable snapshot and supplies its reference even for short conversations. The gateway validates its path, size and SHA256 before launch.
The worker starts from the focused prompt or `session_summary` plus inherited rules, memories and skill inventory. It consults relevant
transcript portions on demand when decisions or details are missing, without loading the entire prior conversation automatically.
Explicit external `context.conversation.messages` exports remain supported for callers intentionally supplying inline history.
Private reasoning is excluded; unavailable binary content is reported. `conversation_messages` counts available messages, not messages read.

`inherited_context` includes sources and caller export metadata for inspection. Import failures do not silently discard required rules.
A `session_summary` provides a focused handoff; the transcript reference supports later lookup when the hook is present. An explicit `context` can include
`instructions` and `memories` arrays of `{source, text}`, a skill inventory, portable settings, and a summary.

Codex is not a native Claude subagent and has no automatic handoff of the caller's work, conversation, decisions or Claude-specific
rules, memories and skills. Keep `inherit_context: true`, the default, for ordinary delegation, including tasks supplied as a brief or
plan file. Claude may deliberately choose `inherit_context: false` for a clean isolated agent without asking for extra permission.
This omits automatic caller inheritance; explicit supplied context and native Codex configuration can still apply. Never silently
switch to false after an export error. Inspect receipt `inherited_context.enabled`, `disabled_reason`, provider, source counts,
caller metadata and warnings to confirm the actual handoff. An empty bundle can still have inheritance enabled.

The caller's configuration roots are respected, and an explicit caller bundle is not supplemented from the gateway's home.
Opt-outs propagate to children, and launch, status and result receipts report the stored choice.

## Recorded changes

Codex's `turn/diff/updated` notification supplies the native aggregate patch; native `fileChange` items remain available in
`changed_files`. These events do not cover every shell or CLI edit. The gateway also takes a bounded Git working-tree baseline before
each turn it starts and compares it when the turn finishes. `agents.result.workspace_changes` contains that patch, its
`observed-workspace` source, and any capture warning. The UI labels this evidence **Observed workspace**.

The comparison excludes unchanged pre-existing dirty content and can detect edits committed during the turn, including the first
commit in a new repository. It includes tracked and non-ignored untracked files, deletions, and executable-bit changes. Binary files
receive metadata rather than a text preview. Linked or unreadable files and files exceeding the capture limits are omitted explicitly.
Limits are 1 MiB per file, 16 MiB of compared content, and 2,000 changed paths.

Workspace observations can include concurrent changes made by another process in the same working tree. Use isolated worktrees when
author attribution matters. Baselines live in gateway memory; completed patches persist. A gateway restart during an active turn,
an externally started turn, or a native continuation without a baseline retains native evidence without reconstructing a baseline.
Non-Git workspaces also retain native evidence only.

## Child agents and stopping

Gateway agents receive native launch and control tools. A child's parent ID is attached automatically. Children inherit the selected
attachment and context bundle; sandbox authority and workspace remain constrained by the parent. No shell call to localhost is needed.

`max_depth` counts further permitted delegation generations. A child can request zero to become a leaf. `max_children` limits the total
number of gateway children its parent can launch. These are gateway delegation limits.

`agents.stop` interrupts the exact active turn, then stops gateway descendants and removes queued follow-ups. A stopped parent cannot
launch more gateway children until explicitly continued. `agents.cancel` targets a specific message or turn, protecting newer work.
The gateway must remain running to service delegation tools. See [thread lifecycle](thread-lifecycle.md) for idle release and resumption.
