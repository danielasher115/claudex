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

`agents.send` steers the observed active turn by default. If the agent is idle, it queues a new turn for the gateway worker.
Use `mode: "queue"` to defer a follow-up until current work finishes. Use `mode: "steer"` when absence of an active turn must be an error.

An optional `expected_turn_id` targets a specific turn. Omitted targets bind to the currently observed turn. A changed or unresolved
active turn fails instead of targeting newer work. The dashboard composer keeps its own Queue/Steer selection.

`agents.send` returns a `message_id`; pass it to `agents.wait` or `agents.result` to follow that exact message.
Without a target, `agents.wait` follows the newest non-cancelled gateway message. Each wait is bounded to 60 seconds and can return
`queued`, `starting`, or `running`. A generation marked `completed` has finished its turn; inspect its report to determine task completeness.

Reuse a stable UUID `request_id` when retrying a launch or send. Retries preserve the original receipt and delivery mode.
Ambiguously accepted sends become `uncertain` and are not automatically repeated. A transport disconnect is not a reason to submit
accepted work again. MCP cancellation resolves pending launch/send ownership before stopping its agent.

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
unrelated conversations. Install it with `claudex install-claude-context`, then restart or resume Claude and reconnect MCP.
`context.caller` identifies the session, workspace, transcript, and permission mode. `context.conversation` contains versioned public
messages and a SHA256 digest, or a gateway-validated immutable snapshot reference. History is supplied as reference evidence separately
from instruction files. Larger snapshots must be read before work begins. Private reasoning is excluded; unavailable binary content is reported.

`inherited_context` includes sources and caller export metadata for inspection. Import failures do not silently discard required rules.
A `session_summary` supplements the automatic export or provides a handoff when the hook is absent. An explicit `context` can include
`instructions` and `memories` arrays of `{source, text}`, a skill inventory, portable settings, and a summary.

Set `inherit_context: false` to omit the caller bundle; native Codex configuration still applies. The caller's configuration roots are
respected, and an explicit caller bundle is not supplemented from the gateway's home. Opt-outs propagate to children.

## Child agents and stopping

Gateway agents receive native launch and control tools. A child's parent ID is attached automatically. Children inherit the selected
attachment and context bundle; sandbox authority and workspace remain constrained by the parent. No shell call to localhost is needed.

`max_depth` counts further permitted delegation generations. A child can request zero to become a leaf. `max_children` limits the total
number of gateway children its parent can launch. These are gateway delegation limits.

`agents.stop` interrupts the exact active turn, then stops gateway descendants and removes queued follow-ups. A stopped parent cannot
launch more gateway children until explicitly continued. `agents.cancel` targets a specific message or turn, protecting newer work.
The gateway must remain running to service delegation tools. See [thread lifecycle](thread-lifecycle.md) for idle release and resumption.
