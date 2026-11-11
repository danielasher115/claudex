# Delegated thread lifecycle

The gateway releases its subscription to idle delegated threads so Codex can unload their runtime resources while retaining saved history.
Only gateway-owned threads are eligible. The policy reconciles after completion and status notifications, connection recovery, and a bounded retry interval.

## Active and saved work

A launch holds its launch lock through the first `turn/start` acknowledgement. Queue delivery holds the per-thread turn lock through resume
and start. Active threads and threads with queued or sending messages remain subscribed.

When an owned thread is idle without pending work, `ThreadLifecycle` sends `thread/unsubscribe` over the gateway's existing connection.
This releases that connection's subscription. Codex unloads the thread after its no-subscriber inactivity grace period, emits `thread/closed`,
and terminates its MCP server children. Another client's subscription can keep a thread loaded.

Delegated launches and resumed queued turns use `config.thread_unload_delay_secs=1`. An explicit launch configuration can override the
launch value. Existing loaded threads retain their native configuration until resumed or the app-server restarts.

Saved history remains readable. Status, result, and transcript reads do not resume the thread. A new `agents.send` queues a turn;
the existing worker resumes the thread and starts generation on demand. Failed release requests are retried after checking state under the turn lock.
The policy does not terminate Codex, archive threads, kill MCP children directly, or release another client's subscription.

## Recovery and discovery

Discovery merges local SQLite history with paginated native `thread/list` metadata using `useStateDbOnly=true`.
Codex history can exist in both `CODEX_HOME/state_*.sqlite` and `CODEX_HOME/sqlite/state_*.sqlite`; neither location alone proves a thread is absent.

An owned thread absent from both saved history and the loaded set remains `starting` for at most 120 seconds while online.
It then becomes `failed` with a persisted error. A definite launch rejection fails immediately. If an idle loaded thread lost its initial
acknowledgement, reconciliation checks for a persisted native turn before failing it. Disconnection alone does not expire a start.
The lifecycle task reconciles independently of dashboard or MCP polling.

`agents.list` defaults to compact live rows (`active`, `idle`, `starting`) and 25 results, capped at 100 per page.
Use `filter: "saved"`, `"archived"`, or `"all"` for history and terminal failures. Pass `next_cursor` back as `cursor`.
Pages use stable ascending thread-ID order, and each cursor is tied to its filter.

## Regression checks

The opt-in [native process regression](development.md) starts an isolated app-server with a temporary Codex home,
a local model fixture, and a sentinel stdio MCP server. It makes no paid model requests or normal-daemon connections.
It checks that a subscribed completed thread retains its MCP child, release closes the thread and child process, and a queued
follow-up resumes the same thread with a new child that is also released. Disabling the policy causes its unload deadline to fail.

For a real deployment, track child PIDs that belong to the selected thread, observe the native closure event, and verify process exit.
An unsubscribe acknowledgement alone does not prove unload. If a thread remains loaded, check for another subscriber or pending messages first.
