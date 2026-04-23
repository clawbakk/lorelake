# Hooks

Shell entry-point hooks and bash library modules. The hooks are wired into Claude Code and git; the lib modules are sourced by the hooks.

## Entry-point hooks

| Page | Description |
|---|---|
| [[session-start-hook]] | Context injection hook — loads session-preamble and llake/index.md at session start |
| [[session-end-hook]] | Two-pass triage→capture hook that records session knowledge to the wiki |
| [[post-merge-hook]] | Git post-merge hook that triggers the ingest agent on the configured branch |

## Shell library modules

| Page | Description |
|---|---|
| [[agent-id]] | Generates human-readable <adj>-<noun>-HHMMSS agent IDs for log lines and working dirs |
| [[agent-run]] | Kill-trap helpers, timeout watchdog, and cleanup for background agent processes |
| [[constants]] | Shared shell constants used across LoreLake hooks |
| [[detect-project-root]] | Three-level strategy to locate the project's llake/config.json from any shell context |
