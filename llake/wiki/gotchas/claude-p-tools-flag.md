---
title: "--allowedTools does not restrict tools in -p mode"
description: "In headless claude -p, --allowedTools is only a permission-prompt allowlist; use --tools and --strict-mcp-config to actually constrain an agent"
tags: [gotchas, agents, security, claude-cli, hooks]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[post-merge-hook]]"
  - "[[session-capture-worker]]"
  - "[[config-schema]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[session-end-hook]]"
---
# --allowedTools does not restrict tools in -p mode

## What goes wrong

Every LoreLake hook passed `--allowedTools "$ALLOWED_TOOLS"` to `claude -p` and assumed that constrained the agent to those tools. It does not.

`--allowedTools` is a **permission-prompt allowlist**: it says which tool calls may proceed without asking the user. In headless `-p` mode there is no user to ask, so the flag does not restrict which built-ins the model can call at all.

The practical consequence was that the ingest agent — nominally scoped to `Read, Write, Edit, Glob, Grep, Bash` — was actually receiving **every Claude Code built-in plus every MCP server configured in the user's global settings**: Figma, Google Drive, Atlassian Rovo, whatever happened to be installed. A background agent running unattended on every merge had reach into every third-party integration on the developer's machine, and nothing in the logs said so.

## The fix

Two flags, both required:

```bash
claude -p "$PROMPT" \
  --tools "$ALLOWED_TOOLS" \
  --allowedTools "$ALLOWED_TOOLS" \
  --strict-mcp-config \
  --max-budget-usd "$MAX_BUDGET_USD" \
  --output-format stream-json --verbose
```

- **`--tools`** restricts the built-in tool set. This is the flag that does the thing everyone assumed `--allowedTools` did.
- **`--strict-mcp-config`** blocks ambient MCP servers from the user's global config. Without it, `--tools` constrains the built-ins while MCP servers walk in the side door.

`--allowedTools` is still passed. It is not harmful, it suppresses permission behaviour consistently, and dropping it would be a second change to reason about. Both are passed everywhere.

## Config naming did not change

`ingest.allowedTools`, `sessionCapture.allowedTools`, `ingest.v2.plannerAllowedTools`, and `ingest.v2.fixerAllowedTools` all kept their names and their meaning — "the tools this agent may use". Only the CLI flag the hook derives from them changed. Do not rename the config keys to match the flag; the key name describes intent, and the flag is an implementation detail that has now changed once already.

## Where it is enforced

Every `claude -p` call site in the repo passes all three flags:

- legacy ingest — `hooks/post-merge.sh:311-318`
- v2 planner — `hooks/lib/ingest-v2.sh:127-135`
- v2 fixer — `hooks/lib/ingest-v2.sh:258-266`
- triage — `hooks/lib/session-capture-worker.sh:269-277`
- capture — `hooks/lib/session-capture-worker.sh:361-370`

Two test suites pin it: `tests/hooks/test_post_merge.sh` and `tests/hooks/test_session_capture_worker.sh` each assert that both `--tools` and `--strict-mcp-config` appear in the recorded invocation. Those assertions are the thing standing between this and a silent regression — a new call site that forgets the flags produces a *more capable* agent, which no functional test will notice.

## Rule of thumb

When adding a new `claude -p` call site in this repo, copy an existing invocation's flag block verbatim and add a stub assertion for it. Tool scoping is not a detail you can leave for later: the failure mode is over-permission, and over-permission does not throw.

## Key Points

- `--allowedTools` is a permission-prompt allowlist; in `-p` mode it restricts nothing.
- `--tools` restricts the built-in tool set; `--strict-mcp-config` blocks ambient MCP servers. Both are needed.
- Before the fix, background agents had access to every built-in and every user-global MCP server.
- The `*allowedTools` config keys kept their names — only the derived CLI flag changed.
- Two bash test suites assert the flags are present; keep them updated when adding a call site.
- The failure mode is over-permission, which is silent — no functional test will catch it.

## Code References

- `hooks/post-merge.sh:311-318` — legacy ingest invocation
- `hooks/lib/ingest-v2.sh:127-135` — planner invocation
- `hooks/lib/ingest-v2.sh:258-266` — fixer invocation
- `hooks/lib/session-capture-worker.sh:269-277` — triage invocation
- `hooks/lib/session-capture-worker.sh:361-370` — capture invocation
- `tests/hooks/test_post_merge.sh` — `test_claude_invocation_flags`
- `tests/hooks/test_session_capture_worker.sh` — the mirrored flag assertions

## See Also

- [[post-merge-hook]] — the ingest call sites
- [[session-capture-worker]] — the triage and capture call sites
- [[config-schema]] — the `allowedTools` keys and their meaning
- [[three-writer-model]] — the write-surface boundaries these flags help enforce
