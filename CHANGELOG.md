# Changelog

## 0.1.0a2

- Install and update the bundled Claude skill alongside the caller-context hook, preserving replaced content.
- Migrate older hook registrations without duplicating exporters or removing unrelated hooks.
- Discover shared personal skills and project skills linked within their working tree.
- Preserve valid compacted Claude conversation ancestry while rejecting real parent cycles.
- Keep the public transcript available by reference and consult it on demand instead of loading it automatically.
- Report whether caller inheritance was enabled, and explain clean launches in the context inspector.
- Recover existing agents with an observational attach tool and documented task handles.
- Capture observed Git workspace changes made through shell commands, scripts, and commits, alongside native file events.
- Separate the task brief and context-usage cards in the inspector.
- Include an optional Linux/WSL startup service for the native Codex backend and gateway.

## 0.1.0a1

Initial public alpha.

- Launch and control local Codex agents from Claude Code through MCP.
- Inspect conversations, readable reasoning summaries, attachments, and recorded file diffs.
- Queue or steer follow-ups, cancel an exact task, and stop agent groups.
- Inspect inherited context with explicit source and trust boundaries.
- Release idle delegated threads and resume them on demand.
- Run with portable local process management and Unix socket or loopback WebSocket transport.
- Include fictional demo screenshots, contribution guidance, dependency notices, and automated checks.

macOS support is experimental and has not been exercised on a macOS machine.
