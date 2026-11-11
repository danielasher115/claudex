# Security

Claudex is a local, single-user tool. It reads selected project instructions and saved session metadata and can ask your Codex
installation to execute tasks. Native Codex permissions and approval policy remain authoritative for agent execution.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/danielasher115/claudex/security/advisories/new).
Do not put credentials, private transcripts, or an actionable unpatched disclosure in a public issue.
For a report, provide the affected version, relevant configuration, expected boundary, and a reproduction using synthetic files.
The current alpha is the supported release line; fixes are applied to `main`.

## Trust boundaries

The gateway binds to loopback and rejects unrecognized Host headers. Browser mutations require the same origin; MCP/CLI protocol
mutations require the gateway's local token. This is not a multi-user service and must not be exposed directly to the internet or a LAN.
All local processes that can reach the loopback listener must be trusted. Origin checks are browser defenses, not OS-user authentication.
Use a private single-user workstation; browser data and saved context can contain sensitive project text.

For native Codex WebSockets, use a loopback endpoint and a capability token when supported by your Codex version.
Pass the token file with `--codex-token-file`; do not put a bearer token in a URL or repository configuration.
The gateway does not approve native permission prompts on your behalf.

Context capture rejects private credential locations and recognizable private-key content. Project imports are confined to their
working tree and curated context scopes. Symlinks do not grant access outside the selected scope. Automatic context traversal skips Windows reparse points before following their targets. Trusted user instructions may
explicitly reference external nonprivate documents, but entering project rules discards that external trust.
A project setting cannot select an arbitrary home directory as its memory store. Rejections produce notices without disclosing file contents.

Saved memories and conversation text are intentionally inherited when enabled. Redaction and filename checks do not guarantee that
arbitrary prose is free of secrets. Review context sources and attachments before delegating sensitive work; use `inherit_context=false`
for an isolated task. Do not grant untrusted agents a broader Codex sandbox than their work requires.

On POSIX systems, application state directories use mode `0700` and local tokens use `0600`. Windows state inherits the user-profile's
ACLs. If you choose a custom directory, restrict its ACLs to your account.

## Development and release

Tests use synthetic keys and transcripts. Do not point integration tests at a normal account's daemon or MCP servers.
Public screenshots are rendered from fictional fixtures. Dependency notices ship with built assets.
Runtime state, environment files, local logs and databases are excluded from version control.
