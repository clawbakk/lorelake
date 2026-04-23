---
title: "Post-Merge Hook"
description: "Git post-merge hook that triggers the ingest agent on the configured branch"
tags: [hooks, post-merge, ingest, git]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[three-writer-model]]"
  - "[[ingest-template]]"
  - "[[agent-run]]"
  - "[[agent-id]]"
  - "[[detect-project-root]]"
  - "[[format-agent-log]]"
  - "[[is-llake-agent-guard]]"
  - "[[adr-001-post-merge-trigger]]"
  - "[[config-schema]]"
---

## Overview

`hooks/post-merge.sh` fires after `git pull` merges new commits into the local working tree. Its job is **incremental ingest**: it detects whether the merge landed on the configured branch, computes the new commit range since the last successful ingest, and spawns a background Claude CLI agent to update wiki pages based on what changed in the codebase.

The hook is wired to the git `post-merge` hook (not to Claude Code's hook system). It is the only hook in LoreLake that uses `git rev-parse --show-toplevel` rather than a `llake/config.json` marker walk to find the project root — because `post-merge` fires inside a git repo by definition.

If this hook were removed or broken, wiki pages would not automatically update when code changes are merged. The wiki would drift from the codebase over time until a user manually ran `/llake-bootstrap`. The `last-ingest-sha` cursor would also stop advancing, meaning the next successful run would process a larger-than-expected commit range.

## Git hook wiring

Unlike the Claude Code hooks, `post-merge.sh` is not registered in `hooks/hooks.json`. It is wired via a shim placed in the repo's `.git/hooks/post-merge` directory at install time:

```bash
printf '#!/bin/bash\nexec "$(git rev-parse --show-toplevel)/lorelake/hooks/post-merge.sh" "$@"\n' \
  > "$(git rev-parse --git-common-dir)/hooks/post-merge" \
  && chmod +x "$(git rev-parse --git-common-dir)/hooks/post-merge"
```

The shim delegates to the plugin's script. Using `--git-common-dir` (rather than `--git-dir`) means the hook is installed once in the main repo and automatically covers all worktrees.

## Step-by-step walkthrough

### 1. Project-root detection

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

if [ -n "${LLAKE_PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$LLAKE_PROJECT_ROOT"
fi
```

Git provides the project root directly. The env override `LLAKE_PROJECT_ROOT` is checked afterward for test scenarios where it is desirable to point the hook at a different directory without being inside a git repo.

### 2. LoreLake install check

```bash
[ -f "$CONFIG_FILE" ] || exit 0
```

If `llake/config.json` is absent, the hook exits 0 silently. This allows the git hook shim to be installed in a repo before LoreLake is bootstrapped without any error noise.

### 3. Recursion guard

```bash
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${AGENT_ID:-unknown}]"
  exit 0
fi
```

Background ingest agents sometimes run `git` commands. If those commands trigger a `post-merge` event in the same repo (unlikely but possible in edge cases), this guard prevents infinite recursion. See [[is-llake-agent-guard]] for the full pattern.

### 4. Master toggle

```bash
INGEST_ENABLED=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.enabled")
if [ "$INGEST_ENABLED" = "false" ]; then
  hook_end "skipped: ingest disabled"
  exit 0
fi
```

The `ingest.enabled` flag in [[config-schema]] lets users disable ingest without removing the git hook shim.

### 5. Config reads

All tunable parameters are read from `config.json` (with fallback to `config.default.json`):

| Config key | Purpose |
|---|---|
| `ingest.maxBudgetUsd` | USD cap on the ingest agent |
| `ingest.timeoutSeconds` | Watchdog timeout |
| `ingest.branch` | The branch that triggers ingest (e.g., `main`) |
| `ingest.model` | Model for the ingest agent (optional; omitted if empty) |
| `ingest.effort` | Effort level for the ingest agent (optional; omitted if empty) |
| `ingest.allowedTools` | JSON array; converted to comma-separated for `--allowedTools` |
| `ingest.include` | JSON array of paths to scope git diffs and agent reads |

### 6. Branch guard

```bash
CURRENT_BRANCH=$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$CURRENT_BRANCH" != "$INGEST_BRANCH" ]; then
  hook_end "skipped: not on $INGEST_BRANCH ($CURRENT_BRANCH)"
  exit 0
fi
```

`post-merge` fires on every merge regardless of branch. This check ensures ingest only runs on the configured branch (typically `main`). Feature-branch merges are ignored. See [[adr-001-post-merge-trigger]] for the rationale behind using `post-merge` rather than a server-side hook.

### 7. SHA cursor check

```bash
if [ ! -f "$SHA_FILE" ]; then
  hook_end "skipped: no last-ingest-sha file"
  exit 0
fi
LAST_SHA=$(cat "$SHA_FILE" | tr -d '[:space:]')
CURRENT_SHA=$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null)

if [ "$LAST_SHA" = "$CURRENT_SHA" ]; then
  hook_end "skipped: no new commits"
  exit 0
fi
```

`llake/last-ingest-sha` is the commit cursor. Its absence causes the hook to skip — the file must be created as part of the initial bootstrap or first install. If `LAST_SHA` equals `HEAD`, no new commits exist and the hook exits.

The cursor file is **not updated by the hook script itself** before spawning the agent. It is the ingest agent's responsibility to write the new SHA after successfully processing the commit range. This means: if the agent is killed or fails, `last-ingest-sha` is not advanced, and the next `post-merge` will re-process the same range (or a superset of it). This is safe because the ingest agent is designed to be idempotent — re-processing already-documented commits updates pages rather than duplicating them.

**Exception**: if the commit range is invalid (e.g., after a force push that rewrote history), the hook resets the cursor to `HEAD` immediately:

```bash
if ! git -C "$PROJECT_ROOT" log --oneline "$LAST_SHA..$CURRENT_SHA" > /dev/null 2>&1; then
  echo "$CURRENT_SHA" > "$SHA_FILE"
  hook_end "skipped: invalid range, reset SHA to $CURRENT_SHA"
  exit 0
fi
```

This prevents the hook from repeatedly failing on an unreachable SHA.

### 8. Pre-flight relevance check

```bash
if [ ${#INCLUDE_PATHS[@]} -gt 0 ]; then
  RELEVANT_FILES=$(git -C "$PROJECT_ROOT" diff --name-only "$LAST_SHA".."$CURRENT_SHA" -- "${INCLUDE_PATHS[@]}" 2>/dev/null | head -1)
else
  RELEVANT_FILES=$(git -C "$PROJECT_ROOT" diff --name-only "$LAST_SHA".."$CURRENT_SHA" 2>/dev/null | head -1)
fi

if [ -z "$RELEVANT_FILES" ]; then
  echo "$CURRENT_SHA" > "$SHA_FILE"
  hook_end "skipped: no relevant file changes ($COMMIT_RANGE)"
  exit 0
fi
```

Before spawning any agent, the hook checks whether the commit range touched any file in `ingest.include`. If no relevant files changed (e.g., a merge that only touched documentation or config outside the scoped paths), the cursor is advanced to `HEAD` immediately and the hook exits. This avoids running an agent when there is nothing for it to document.

### 9. Prompt rendering

```bash
INGEST_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$INGEST_PROMPT_TMPL" \
  "$CONFIG_FILE" \
  "AGENT_ID=$AGENT_ID" \
  "PROJECT_ROOT=$PROJECT_ROOT" \
  "LAST_SHA=$LAST_SHA" \
  "CURRENT_SHA=$CURRENT_SHA" \
  "COMMIT_RANGE=$COMMIT_RANGE" \
  "PATHSPEC_INCLUDE=$PATHSPEC_INCLUDE" \
  "LLAKE_ROOT=$LLAKE_ROOT" \
  "WIKI_ROOT=$WIKI_ROOT" \
  "SCHEMA_DIR=$SCHEMA_DIR")
```

`COMMIT_RANGE` is the abbreviated form `<last7>...<current7>` used in log messages. `LAST_SHA` and `CURRENT_SHA` are the full SHAs passed to the agent for its own `git log` / `git diff` commands. `PATHSPEC_INCLUDE` is a pre-built `-- 'path/' 'path2/'` string the agent can append to git commands to scope its diffs. See [[ingest-template]] for how these variables are used in the prompt.

### 10. Background subshell and watchdog

```bash
(
  source "$LIB_DIR/agent-run.sh"
  MY_PID=$(sh -c 'echo $PPID')
  HOOKS_LOG_FILE="$LOG_FILE"
  CURRENT_PID_FILE="$AGENT_DIR/ingest.pid"
  echo "$MY_PID" > "$CURRENT_PID_FILE"
  setup_kill_trap

  (
    sleep "$MAX_TIMEOUT_SEC"
    if kill -0 "$MY_PID" 2>/dev/null; then
      kill -USR1 "$MY_PID" 2>/dev/null
    fi
  ) &
  WATCHDOG_PID=$!

  claude $MODEL_FLAG $EFFORT_FLAG -p "$INGEST_PROMPT" \
    --allowedTools "$ALLOWED_TOOLS" \
    --max-budget-usd "$MAX_BUDGET_USD" \
    --output-format stream-json --verbose 2>&1 \
    | python3 "$LIB_DIR/format-agent-log.py" >> "$AGENT_LOG" &
  CLAUDE_PID=$!

  wait "$CLAUDE_PID" 2>/dev/null
  ...
) &
disown
```

The subshell is detached with `disown`. The watchdog sends `USR1` after `timeoutSeconds`, triggering a tree-kill via `setup_kill_trap`. See [[agent-run]] for the kill-trap mechanics and [[agent-id]] for the readable ID format.

Note that `$MODEL_FLAG` and `$EFFORT_FLAG` may be empty strings — the hook omits `--model` and `--effort` flags entirely when the config values are empty, letting Claude CLI use its defaults.

### 11. Completion and SHA advancement

When the agent exits with code 0, the hook logs completion. The ingest agent itself is responsible for writing the new `last-ingest-sha`. The hook does not write it on success — that write happens inside the agent's workflow as its final step, after all wiki pages are updated.

If the agent exits with code 137 or 143 (SIGKILL / SIGTERM), it was killed by the watchdog or the OS; the timeout path is already logged. Any other nonzero exit is logged as a failure. In both failure cases, `last-ingest-sha` is not advanced, so the range will be re-processed on the next merge.

## Runtime state layout

```
<project>/llake/
  last-ingest-sha                     # cursor: last successfully ingested commit SHA
  .state/
    hooks.log                         # one line per hook invocation (start + outcome)
    agents/<agent-id>/
      agent.log                       # full ingest trace (format-agent-log output)
      ingest.pid                      # PID file during ingest run (removed after exit)
```

## hooks.log format

```
2026-04-23 10:14:00 | post-merge    | started → done: spawned agent calm-river-101400 (commits: a1b2c3d..e4f5a6b, timeout: 300s)
2026-04-23 10:19:42 | agent-done    | completed: agent calm-river-101400 finished (exit 0, commits: a1b2c3d..e4f5a6b)
```

A skipped run looks like:

```
2026-04-23 11:00:01 | post-merge    | started → skipped: not on main (feature/my-branch)
```

## last-ingest-sha lifecycle

| Event | SHA file state |
|---|---|
| Initial bootstrap | Written by bootstrap agent or manually seeded |
| Successful ingest | Advanced to `CURRENT_SHA` by the ingest agent |
| Failed / killed ingest | Unchanged — next run re-processes the range |
| Force push (invalid range) | Reset to `HEAD` immediately by the hook, skipping the agent |
| No relevant file changes | Advanced to `HEAD` immediately by the hook, skipping the agent |

## Key Points

- Fires on `git post-merge`; only processes merges on the configured branch (`ingest.branch`).
- Uses `git rev-parse --show-toplevel` for project-root detection (not the marker-walk used by session hooks).
- The `last-ingest-sha` cursor is the commit boundary; the agent advances it after success.
- If `last-ingest-sha` is missing, the hook skips — file must be seeded at install/bootstrap time.
- Pre-flight relevance check: if no `ingest.include`-scoped files changed, the cursor advances and no agent runs.
- Invalid commit ranges (force push) reset the cursor to `HEAD` without running an agent.
- Recursion guard (`IS_LLAKE_AGENT=true`) prevents ingest agent git operations from re-triggering the hook.
- Single watchdog covers the entire ingest run; timeout triggers a tree-kill.
- `--model` and `--effort` flags are omitted when config values are empty (allows CLI defaults).
- Agent log retained at `.state/agents/<id>/agent.log`; no session directory equivalent.

## Code References

- `hooks/post-merge.sh:1-277` — full hook implementation
- `hooks/post-merge.sh:41-57` — project-root detection and config file check
- `hooks/post-merge.sh:77-82` — recursion guard
- `hooks/post-merge.sh:134-139` — branch guard
- `hooks/post-merge.sh:141-163` — SHA cursor read, equality check, and invalid-range reset
- `hooks/post-merge.sh:184-196` — pre-flight relevance check
- `hooks/post-merge.sh:209-221` — prompt rendering with all template variables
- `hooks/post-merge.sh:223-274` — background subshell, watchdog, agent invocation, and completion logging

## See Also

- [[ingest-template]] — the prompt the ingest agent receives; shows how `COMMIT_RANGE`, `PATHSPEC_INCLUDE`, and other variables are used
- [[agent-run]] — `setup_kill_trap` and watchdog pattern shared by all three hooks
- [[agent-id]] — readable agent ID generation (`calm-river-101400` format)
- [[format-agent-log]] — converts `stream-json` to the human-readable agent log
- [[is-llake-agent-guard]] — the `IS_LLAKE_AGENT` recursion guard pattern
- [[adr-001-post-merge-trigger]] — decision record explaining the post-merge trigger choice
- [[config-schema]] — all `ingest.*` config keys and their defaults
