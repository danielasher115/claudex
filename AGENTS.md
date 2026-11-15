# Maintaining Claudex

- Treat this checkout as the source of truth for the installed gateway, MCP adapter, hook, skill, and UI.
- Keep runtime data separate from source. Preserve existing session data and attachments when changing an installation.
- Run relevant tests before committing a fix. Rebuild the packaged dashboard with `npm run build` in `ui/` after UI changes.
- Commit and push completed fixes unless the user asks for review-only work. Do not force-push without specific authorization.
- Keep credentials, exported caller conversations, local state, and full recordings out of Git.
- Keep module boundaries small and cohesive. Fix shared behavior where it is owned, with regression coverage for meaningful bugs.
