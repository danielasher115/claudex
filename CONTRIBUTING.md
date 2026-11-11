# Contributing to Claudex

Small, focused contributions are welcome. For a bug, include the host OS, Python and Codex versions, the command you ran,
and a minimal reproduction with private paths, prompts, keys and account information removed.

## Development

See [the development guide](docs/development.md) for installation, tests, the UI build, and fictional screenshot fixtures.
Run the Python and UI suites before submitting a pull request. The isolated native lifecycle test uses a local model fixture,
requires no account credentials, and must never target your normal Codex daemon.

## Design

Read [the architecture guide](docs/architecture.md) before changing a module boundary. Put a rule in one place and expose the
operation a caller wants to perform. The gateway owns agent state and task lifetimes; context capture owns trust decisions;
transport owns framing and correlation; the platform module owns operating-system differences.

A new interface should hide meaningful complexity. Avoid forwarding-only services, parallel implementations of one policy,
and splitting a coherent implementation merely to reduce a file's line count. Test behavior at the owning module's boundary.

Keep unrelated refactors separate. Changes to permissions, context inheritance, delivery, and cancellation need regression tests.
Describe the resulting behavior, validation, and any platform you could not exercise. Do not represent untested macOS support as verified.

## Pull requests

Use an imperative title and explain the problem, resulting behavior and relevant checks. Include a sanitized screenshot for visible
UI changes. Do not submit real transcripts, local runtime databases, tokens, screenshots with private content, or generated virtual environments.

By contributing, you agree to license your contribution under the project's MIT license. Do not submit code you cannot authorize for release.
