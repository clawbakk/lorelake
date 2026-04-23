---
title: "Three-Writer Model"
description: "How bootstrap, ingest, and capture divide write surface and responsibilities"
tags: [architecture, writers, agents, hooks]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[session-start-hook]]"
  - "[[session-end-hook]]"
  - "[[post-merge-hook]]"
  - "[[llake-bootstrap-skill]]"
  - "[[runtime-layout]]"
  - "[[plugin-project-duality]]"
---

## Overview

LoreLake maintains its wiki through exactly three writers. Each writer has a distinct trigger, a distinct write surface, and a distinct execution model. Together they cover the full lifecycle of project knowledge: initial population (bootstrap), code change tracking (ingest), and conversation capture (capture). No two writers own the same wiki category without explicit coordination rules, and all three share a common set of safety rails.

Understanding the three-writer model is prerequisite to understanding almost everything else in LoreLake — it determines what gets written when, why costs are bounded, and why a background session cannot accidentally re-trigger itself.

---

## The Three Writers

### Bootstrap

| Property | Value |
|---|---|
| Kind | In-session skill (runs in the user's foreground Claude Code session) |
| Trigger | User manually invokes `/llake-bootstrap` |
| Entry point | `skills/llake-bootstrap/SKILL.md` |
| Execution | Dispatches subagents via the `Task` tool — no `claude -p`, no background process |
| Write surface | Full wiki — can create pages in any category |
| Config keys | None specific to bootstrap; model/budget/timeout come from the user's CC session |

Bootstrap is the one-time population writer. It reads `ingest.include` from the project's `config.json` to determine which paths are in scope, decomposes the codebase into logical units, and dispatches parallel `Task` subagents to write the initial wiki pages. Because it runs inside the user's active Claude Code session, it inherits the session's model, effort level, and budget rather than reading its own config keys.

Bootstrap is not automatic. It runs exactly once (or manually when a full re-bootstrap is desired) and does not respond to any hook event.

### Ingest

| Property | Value |
|---|---|
| Kind | Background `claude -p` agent |
| Trigger | Git `post-merge` hook (fires after `git pull` or merge commit on the configured branch) |
| Entry point | `hooks/post-merge.sh` |
| Workflow source | `hooks/prompts/ingest.md.tmpl` rendered by `hooks/lib/render-prompt.py` |
| Write surface | Code-derived pages; excludes `discussions/` |
| Config keys | `ingest.*` (model, effort, budget, timeout, branch, include paths) |

Ingest fires on `post-merge` rather than `post-commit` by deliberate design — `post-commit` fires on every local commit (too noisy), while `post-merge` fires exactly when new code arrives from a remote. The hook compares `last-ingest-sha` to `HEAD` to build a commit range, runs a pre-flight diff to check whether any `ingest.include` paths actually changed, and only then spawns the background agent. If the diff touches nothing in scope, the hook advances `last-ingest-sha` and exits silently without launching an agent at all.

The `post-merge` hook is a git hook, wired into the project's `.git/hooks/post-merge` as a shim that delegates to `hooks/post-merge.sh` in the plugin. This is done once during install by `/llake-lady`. Worktrees share the main repo's `hooks/` directory, so one install covers all worktrees.

**The hooks.json wiring does not include post-merge** — that hook is git-native, not a Claude Code hook. The Claude Code hooks (SessionStart, SessionEnd) are declared in `hooks/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" }] }
    ],
    "SessionEnd": [
      { "hooks": [{ "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-end.sh" }] }
    ]
  }
}
```

### Capture

| Property | Value |
|---|---|
| Kind | Background `claude -p` agent (two-pass) |
| Trigger | Claude Code `SessionEnd` event |
| Entry point | `hooks/session-end.sh` |
| Workflow source | `hooks/prompts/triage.md.tmpl` → `hooks/prompts/capture.md.tmpl` |
| Write surface | `discussions/`, `decisions/`, `gotchas/`, `playbook/` (configurable via `sessionCapture.writableCategories`) |
| Config keys | `sessionCapture.*` (models, efforts, budgets, timeouts, writable categories, min turns/words) |

Capture is the continuous writer. Every time a Claude Code session ends, `session-end.sh` fires and evaluates whether the session is worth capturing. It is by far the most complex of the three hooks because it must handle high session volume cheaply.

---

## Two-Pass Triage (Capture)

The capture workflow is **two-pass by design** to keep background costs bounded:

**Pass 1 — Triage agent:**
- Model: `sessionCapture.triageModel` (a cheaper/smaller model)
- Effort: `sessionCapture.triageEffort`
- Budget cap: $0.50 (hardcoded ceiling)
- Allowed tools: `Read` only
- Output: a classification of `CAPTURE`, `PARTIAL`, or `SKIP`

The triage agent reads the extracted transcript and decides whether the session contains anything worth capturing. If the result is `SKIP`, the session directory is cleaned up and no further work is done.

**Pass 2 — Capture agent:**
- Only runs if triage returned `CAPTURE` or `PARTIAL`
- Model: `sessionCapture.captureModel`
- Budget: `sessionCapture.maxBudgetUsd`
- Allowed tools: configurable via `sessionCapture.allowedTools`
- Prompt: `capture.md.tmpl` receives the triage classification and reason, so it knows the confidence level

From `session-end.sh` (lines 257–263 and 311–317), the two agents are launched sequentially within a single background subshell:

```bash
# Pass 1 — Triage (Read-only, $0.50 cap)
IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$TRIAGE_AGENT_ID" \
  claude --model "$TRIAGE_MODEL" --effort "$TRIAGE_EFFORT" \
  -p "$TRIAGE_PROMPT" \
  --allowedTools "Read" \
  --max-budget-usd 0.50 \
  --output-format stream-json --verbose 2>&1 \
  | python3 "$FORMATTER" --extract-result "$TRIAGE_RESULT_FILE" >> "$AGENT_LOG"

# Pass 2 — Capture (full tools, full budget, only if not SKIP)
IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$CAPTURE_AGENT_ID" \
  claude --model "$CAPTURE_MODEL" --effort "$CAPTURE_EFFORT" \
  -p "$CAPTURE_PROMPT" \
  --allowedTools "$ALLOWED_TOOLS" \
  --max-budget-usd "$MAX_BUDGET_USD" \
  ...
```

Both passes share a single watchdog timer. If the combined triage + capture duration exceeds `sessionCapture.timeoutSeconds`, the watchdog sends `USR1` to kill the whole subshell.

---

## Shared Safety Rails

### Recursion Guard

Every background agent is spawned with `IS_LLAKE_AGENT=true` set in its environment. All three hooks bail immediately when this variable is set:

```bash
# From session-end.sh and post-merge.sh
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${AGENT_ID:-unknown}]"
  exit 0
fi
```

This prevents an agent-driven Claude Code session (e.g., a capture agent that opens files) from re-triggering another capture cycle at its own `SessionEnd`. The guard is structural — it does not rely on the agent behaving correctly.

### Master Toggles

Each writer has a config-level on/off switch:
- `ingest.enabled` — checked by `post-merge.sh`
- `sessionCapture.enabled` — checked by `session-end.sh`

Bootstrap has no toggle; it is only ever user-initiated.

### Thin-Session Filters (Capture)

Before spawning any agent at all, `session-end.sh` filters out short sessions:
- Sessions below `sessionCapture.minTurns` are dropped silently
- Sessions below `sessionCapture.minWords` are dropped silently

These checks use `.turns` and `.words` sidecar files written by `hooks/lib/extract_transcript.py`.

### Session Deduplication Lock

`session-end.sh` writes a `meta` file into the session directory before spawning any agent. If another `SessionEnd` fires for the same `session_id` while the first agent is running (possible with multi-window sessions), the second invocation detects the lock and skips. Stale locks (older than `sessionCapture.lockStalenessSeconds`) are broken automatically.

### Budget Caps and Timeouts

Every `claude -p` invocation carries both `--max-budget-usd` and a watchdog timer that sends `USR1` to tree-kill the agent subshell after `timeoutSeconds`. This is handled by `hooks/lib/agent-run.sh`.

### Write Surface Enforcement

- The capture agent's prompt (`capture.md.tmpl`) explicitly lists which wiki categories it may write to, sourced from `sessionCapture.writableCategories`.
- The ingest agent's prompt (`ingest.md.tmpl`) excludes `discussions/` — that category is capture-owned.
- Bootstrap has full write access but runs in-session (the user can see what it does).
- At the shell level, `--allowedTools` restricts what filesystem operations each background agent can perform.

---

## Write Surface Summary

| Writer | Categories | Can create new categories? |
|---|---|---|
| bootstrap | All | Yes |
| ingest | All except `discussions/` | Yes |
| capture | `discussions/`, `decisions/`, `gotchas/`, `playbook/` (configurable) | No (by default) |

---

## Interaction Between Writers

The three writers do not communicate at runtime — they share only the wiki files on disk and the conventions defined in `schema/`. Potential conflicts are managed by:

1. **Disjoint write surfaces**: capture stays in conversation-derived categories; ingest stays in code-derived categories; bootstrap runs once and is not concurrent with anything.
2. **Append semantics in `discussions/`**: capture appends continuations to existing files (one topic arc = one entry) rather than replacing them, reducing merge conflicts.
3. **Schema-enforced content standards**: both ingest and bootstrap must produce pages that pass the new-employee test; capture must produce entries with immutable Key Facts blocks.
4. **No concurrent ingest runs**: the `last-ingest-sha` cursor is written by the hook shell script after verifying the commit range, and the agent is spawned after the cursor is established.

---

## Key Points

- Bootstrap is in-session (foreground, `Task` tool), ingest and capture are background `claude -p` agents.
- Capture is two-pass: a cheap triage agent classifies first; the full capture agent runs only on `CAPTURE`/`PARTIAL`.
- `IS_LLAKE_AGENT=true` is the recursion guard — all hooks check it before doing anything.
- `hooks/hooks.json` wires only SessionStart and SessionEnd into Claude Code; `post-merge` is a git hook installed by `/llake-lady`.
- Budget caps (`--max-budget-usd`) and watchdog timers bound the cost and duration of every background agent.
- Write surfaces are enforced both at the shell level (`--allowedTools`) and in prompt instructions.

---

## Code References

- `hooks/hooks.json` — Claude Code hook declarations (SessionStart, SessionEnd)
- `hooks/session-end.sh:64-67` — recursion guard
- `hooks/session-end.sh:221-346` — background subshell spawning both triage and capture passes
- `hooks/session-end.sh:257-263` — triage agent invocation (Read-only, $0.50 cap)
- `hooks/session-end.sh:311-317` — capture agent invocation
- `hooks/post-merge.sh:79-83` — recursion guard in post-merge
- `hooks/post-merge.sh:184-196` — pre-flight diff check (skip if no relevant files changed)
- `hooks/post-merge.sh:242-246` — ingest agent invocation
- `hooks/lib/agent-run.sh` — shared watchdog and kill-trap helpers
- `hooks/lib/extract_transcript.py` — transcript extraction and `.turns`/`.words` sidecars
- `skills/llake-bootstrap/SKILL.md` — bootstrap skill specification

---

## See Also

- [[session-start-hook]] — context injection (fourth hook; spawns no agent)
- [[session-end-hook]] — full capture hook walkthrough
- [[post-merge-hook]] — full ingest hook walkthrough
- [[llake-bootstrap-skill]] — bootstrap skill specification
- [[runtime-layout]] — where agent logs and session directories live
- [[plugin-project-duality]] — what the plugin repo contains vs. what a project install contains
