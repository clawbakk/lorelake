---
title: "Post-Merge Hook"
description: "Git post-merge hook: branch guard, SHA cursor, batching gate, concurrency lock, and dispatch to the legacy or v2 ingest pipeline"
tags: [hooks, post-merge, ingest, git]
created: 2026-04-23
updated: 2026-09-20
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
  - "[[ingest-v2-pipeline]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[post-merge-lock]]"
  - "[[hook-log]]"
  - "[[claude-p-tools-flag]]"
  - "[[ingest-gate]]"
  - "[[ingest-cursor]]"
  - "[[watchdog-sleep-orphans]]"
---
## Overview

`hooks/post-merge.sh` fires after `git pull` merges new commits into the local working tree. Its job is **incremental ingest**: detect whether the merge landed on the configured branch, compute the commit range since the last successful ingest, and dispatch a background agent to update the wiki from what changed in the code.

The hook is wired to the git `post-merge` hook (not Claude Code's hook system). It is the only LoreLake hook that uses `git rev-parse --show-toplevel` rather than a `llake/config.json` marker walk to find the project root — `post-merge` fires inside a git repo by definition.

Since the v2 work, the hook is really a **dispatcher for two pipelines**. Everything down to the commit-range computation is shared; then `ingest.pipeline` decides whether control passes to [[ingest-v2-orchestrator]] or falls through to the legacy single-agent path in the same file. See [[ingest-v2-pipeline]] for the v2 architecture and [[adr-plan-apply-split]] for why it exists.

If this hook were removed or broken, the wiki would stop tracking code changes and `last-ingest-sha` would stop advancing, so the next successful run would face a larger-than-expected range.

## Git hook wiring

`post-merge.sh` is not registered in `hooks/hooks.json`. It is wired via a shim in the repo's git hooks directory at install time:

```bash
printf '#!/bin/bash\nexec "$(git rev-parse --show-toplevel)/lorelake/hooks/post-merge.sh" "$@"\n' \
  > "$(git rev-parse --git-common-dir)/hooks/post-merge" \
  && chmod +x "$(git rev-parse --git-common-dir)/hooks/post-merge"
```

Using `--git-common-dir` (rather than `--git-dir`) installs the hook once in the main repo and covers all worktrees. `/llake-lady` and `/llake-doctor` detect an existing `post-merge` hook and offer to **chain** rather than clobber it — the original is backed up to `.git/hooks/post-merge.pre-llake` and invoked after the plugin, with its exit code taking precedence. See [[llake-lady-skill]].

## Libraries it sources

| Library | Provides |
|---|---|
| `hooks/lib/constants.sh` | `LLAKE_DIR_NAME`, `WIKI_DIR_NAME` |
| `hooks/lib/agent-id.sh` | `generate_agent_id` — see [[agent-id]] |
| `hooks/lib/hook-log.sh` | `hook_start` / `hook_end` / `log_render_failure` / `render_err_summary` — see [[hook-log]] |
| `hooks/lib/post-merge-lock.sh` | `acquire_post_merge_lock` / `release_post_merge_lock` — see [[post-merge-lock]] |
| `hooks/lib/ingest-cursor.sh` | `advance_ingest_cursor` / `ensure_ingest_clock` — see [[ingest-cursor]] |
| `hooks/lib/agent-run.sh` | `setup_kill_trap`, `kill_tree` (sourced inside the background subshell) — see [[agent-run]] |
| `hooks/lib/ingest-v2.sh` | `run_ingest_v2` (sourced only when `ingest.pipeline` is `v2`) |

## Shared preamble (both pipelines)

1. **Project root** — `git rev-parse --show-toplevel`, then `LLAKE_PROJECT_ROOT` as a test override (`hooks/post-merge.sh:45-50`).
2. **Install check** — no `llake/config.json` means silent `exit 0`, so the shim can be installed before LoreLake is bootstrapped (`hooks/post-merge.sh:61`).
3. **`hook_start`** — opens the `hooks.log` line, rotating the log and marking any prior unterminated line `CRASHED` (`hooks/post-merge.sh:66`).
4. **Recursion guard** — bail when `IS_LLAKE_AGENT=true`, so agents' own git activity cannot re-trigger ingest (`hooks/post-merge.sh:70-73`). See [[is-llake-agent-guard]].
5. **Master toggle** — `ingest.enabled` (`hooks/post-merge.sh:76-80`).
6. **Pipeline switch** — `ingest.pipeline` is read into `USE_INGEST_V2`; anything other than the literal `v2` means legacy (`hooks/post-merge.sh:85-89`).
7. **Config reads** — budget, timeout, branch, model, effort, `allowedTools`, `include` (`hooks/post-merge.sh:92-115`).
8. **Branch guard** — skip unless `HEAD` is on `ingest.branch` (`hooks/post-merge.sh:135-139`).
9. **SHA cursor** — read `llake/last-ingest-sha`; skip if absent or equal to `HEAD`; on an unreachable range (force push), reset the cursor and the ingest clock to `HEAD` with `advance_ingest_cursor` and skip (`hooks/post-merge.sh:142-165`).
10. **Batching gate** — decide `EMPTY` / `WAIT` / `RUN` before either pipeline spawns (see below).

`COMMIT_RANGE` (`<last7>..<current7>`) is computed at `hooks/post-merge.sh:167` and used in every log line from here on.

## Batching gate (both pipelines)

Before either pipeline spawns, `hooks/lib/ingest_gate.py` measures the net `git diff --numstat <last>..<current>` of the range under `ingest.include` and returns one verdict (`hooks/post-merge.sh:169-238`). See [[ingest-gate]] for the full decision table.

| Verdict | Action | Cursor |
|---|---|---|
| `EMPTY`: nothing under the include paths changed | take the post-merge lock, `advance_ingest_cursor`, log `skipped: no relevant file changes` | advanced (held if the lock is busy) |
| `WAIT`: pile below `ingest.schedule.minChangedLines` **and** younger than `ingest.schedule.maxAgeHours` | log `deferred: ...` and exit | held, so the next merge sees a wider range |
| `RUN`: either arm tripped, or schedule disabled, forced, or gate error | continue to the pipeline switch | per the pipeline's own contract |

Three details:

- `ensure_ingest_clock` runs first. It seeds `.state/last-ingest-at` in fresh clones, so a clone does not force an ingest on its first merge. See [[ingest-cursor]].
- The gate **fails open**. A nonzero gate exit becomes `RUN reason=gate-error`, and a sanitized stderr summary is appended only to the spawn log line.
- `LLAKE_IGNORE_SCHEDULE=1` forces `RUN` for a manual flush, and `ingest.schedule.enabled: false` disables batching. Neither one overrides `EMPTY`.

The gate replaced the legacy branch's old pre-flight relevance check (`git diff --name-only ... | head -1`). The v2 branch never had such a check and spawned even for ranges that touched no included path. It now shares the gate's `EMPTY` skip.

## Branch A — the v2 pipeline

When `ingest.pipeline` is `v2` (and the gate returned `RUN`), `hooks/post-merge.sh:243-286` takes over and **nothing below it runs**. The hook:

- sources `hooks/lib/ingest-v2.sh`;
- pre-generates the agent ID, agent dir, and `agent.log` path *before* forking, because the watchdog needs `AGENT_LOG` and `MAX_TIMEOUT_SEC` visible when it fires (`hooks/post-merge.sh:174-188`);
- forks a subshell that installs the kill trap, acquires the post-merge lock, starts a watchdog on `ingest.v2.timeoutSeconds`, calls `run_ingest_v2`, then reaps the watchdog with `kill_tree` (`hooks/post-merge.sh:253-277`). See [[watchdog-sleep-orphans]];
- logs `done: spawned v2 agent (range: ..., timeout: ...s, gate: <detail>)` and exits.

The v2 branch has its own timeout key (`ingest.v2.timeoutSeconds`), separate from `ingest.timeoutSeconds`. Everything after dispatch — context building, planning, applying, the fixer, the cursor write, and the completion log line — belongs to [[ingest-v2-orchestrator]].

## Branch B — the legacy pipeline

The legacy path spawns one `claude -p` agent that reads diffs and writes wiki pages itself.

- **Pathspec**: `PATHSPEC_INCLUDE` is built from `ingest.include` for the agent's own git commands. The legacy branch no longer has its own pre-flight relevance check. The shared batching gate's `EMPTY` verdict handles that case before this branch is reached.
- **Prompt rendering** — `render-prompt.py` fills `AGENT_ID`, `PROJECT_ROOT`, `LAST_SHA`, `CURRENT_SHA`, `COMMIT_RANGE`, `PATHSPEC_INCLUDE`, `LLAKE_ROOT`, `WIKI_ROOT`, `SCHEMA_DIR`. Renderer stderr goes to `$AGENT_DIR/render-stderr.tmp` (per-agent, not `/tmp`, so it survives for post-mortem and does not accumulate). A render failure is written to the agent log by `log_render_failure` and summarised into `hooks.log` by `render_err_summary` (`hooks/post-merge.sh:258-282`). See [[ingest-template]] and [[render-prompt]].
- **Background subshell** — kill trap, lock acquisition, watchdog, then the agent (`hooks/post-merge.sh:284-378`).

## The claude invocation

```
claude $MODEL_FLAG $EFFORT_FLAG -p "$INGEST_PROMPT"
  --tools "$ALLOWED_TOOLS"
  --allowedTools "$ALLOWED_TOOLS"
  --strict-mcp-config
  --max-budget-usd "$MAX_BUDGET_USD"
  --output-format stream-json --verbose
```

`--tools` is what actually restricts the built-in tool set in `-p` mode; `--allowedTools` only pre-approves the permission prompt. `--strict-mcp-config` blocks ambient user-global MCP servers from leaking into the agent. Passing only `--allowedTools` — as this hook did before — silently granted the agent every built-in plus every configured MCP server. See [[claude-p-tools-flag]].

`$MODEL_FLAG` and `$EFFORT_FLAG` are empty strings when the config values are empty, so no flag is passed and the CLI default applies (`hooks/post-merge.sh:125-132`).

## Exit-code dispatch and the cursor contract

The agent runs inside an inner subshell so that `PIPESTATUS[0]` (claude's exit code) becomes the subshell's exit code — a bare `wait` would return the formatter's, which is always 0. `PIPESTATUS` is copied into a plain array (`_pstat=("${PIPESTATUS[@]}")`) because bash 3.2 resets it on the next assignment (`hooks/post-merge.sh:311-326`). See [[bash-3-2-portability]].

If the formatter exits nonzero, its code is written to `$AGENT_DIR/formatter-exit`; the outer subshell reads and removes that sidecar before dispatching (`hooks/post-merge.sh:345-349`).

| Condition | `hooks.log` line | `last-ingest-sha` |
|---|---|---|
| Formatter crashed (sidecar present) | `failed: formatter crashed ... — cursor held` | held |
| Agent exit 0 | `completed: agent <id> ... sha: advanced to <sha7>` | advanced (together with `.state/last-ingest-at`) |
| Agent exit 137/143 without the trap firing | `killed: agent <id> ... external` | held |
| Any other nonzero exit | `failed: agent <id> (exit N, ...)` | held |

A formatter crash makes `claude` take SIGPIPE and exit 141, which lands in the FAILED branch — the cursor is deliberately held until the formatter bug is fixed. See [[format-agent-log]].

**The hook writes the cursor, not the agent** (`hooks/post-merge.sh:419`). It does so through `advance_ingest_cursor`, which also stamps `.state/last-ingest-at` for the gate's age arm. When the agent finishes, the watchdog is reaped with `kill_tree` rather than `kill` (`hooks/post-merge.sh:395`). Re-processing a held range is safe because ingest is idempotent: it updates pages rather than duplicating them.

## Concurrency

Both pipelines acquire the post-merge lock inside their background subshell before touching `last-ingest-sha` or the wiki, and release it from an `EXIT` trap. A second invocation that loses the race logs `skipped: post-merge lock held; <v2|legacy> agent abandoned` and exits 0. A lock older than one hour whose owner PID is dead is reclaimed. See [[post-merge-lock]].

## Test / debug escape hatch

`LLAKE_POST_MERGE_SYNC=1` makes the hook `wait` for the background subshell instead of disowning it, so tests can assert on the final state of `hooks.log`, `agent.log`, and the SHA file (`hooks/post-merge.sh:381-387`). `tests/hooks/test_post_merge.sh`, `tests/hooks/test_post_merge_v2.sh`, and `tests/hooks/test_post_merge_gate.sh` all rely on it.

`LLAKE_IGNORE_SCHEDULE=1` forces the batching gate to `RUN` unless the pile is empty. Use it to flush a deferred pile by hand. See [[ingest-gate]].

## Runtime state layout

```
<project>/llake/
  last-ingest-sha                  # cursor: last successfully ingested commit
  .state/
    hooks.log                      # one line per hook invocation
    last-ingest-at                 # clock: epoch seconds of the last cursor advance (gate age arm)
    post-merge.lock.d/owner.pid    # concurrency lock (present only while held)
    agents/<agent-id>/
      agent.log                    # full trace (format-agent-log output)
      ingest.pid                   # legacy: PID during the run
      orchestrator.pid             # v2: PID of the orchestrator subshell
      render-stderr.tmp            # legacy: renderer stderr, on failure
      formatter-exit               # transient sidecar on formatter crash
      context/ plan.json applied.json failed.json   # v2 only
```

## hooks.log format

```
2026-04-23 10:14:00 | post-merge    | started → done: spawned agent calm-owl-101400-1f3a (commits: a1b2c3d..e4f5a6b, timeout: 1200s, gate: lines=1830 files=12 age_h=3.1 reason=lines)
2026-04-23 10:19:42 | agent-done    | completed: agent calm-owl-101400-1f3a finished (exit 0, commits: a1b2c3d..e4f5a6b, sha: advanced to e4f5a6b)
```

A skipped run:

```
2026-04-23 11:00:01 | post-merge    | started → skipped: not on main (feature/my-branch)
```

Gate outcomes that spawn no agent:

```
2026-04-23 12:00:01 | post-merge    | started → deferred: lines=42 files=3 age_h=2.5 need_lines=1500 need_age_h=24 (a1b2c3d..e4f5a6b)
2026-04-23 12:05:01 | post-merge    | started → skipped: no relevant file changes (a1b2c3d..e4f5a6b)
2026-04-23 12:06:01 | post-merge    | started → skipped: no relevant file changes, lock held — cursor not advanced (a1b2c3d..e4f5a6b)
```

A gate failure shows up on the spawn line as `gate: reason=gate-error err=<sanitized summary>`.

## Key Points

- Fires on git `post-merge`; only acts on `ingest.branch`, and only when `llake/config.json` exists.
- `ingest.pipeline` selects the pipeline: `v2` hands off to `run_ingest_v2` and returns; anything else runs the legacy in-file path.
- Project root comes from git, not the marker walk used by the session hooks.
- **The hook writes `last-ingest-sha`**, and only on a clean run. Kills, nonzero exits, and formatter crashes all hold the cursor.
- Force-pushed / unreachable ranges reset the cursor to `HEAD` without running an agent.
- A batching gate runs before both pipelines. `EMPTY` advances the cursor under the lock and skips. `WAIT` holds the cursor and skips. `RUN` spawns. It fails open to `RUN` on any gate error.
- Every cursor write goes through `advance_ingest_cursor`, which also stamps `.state/last-ingest-at`.
- Watchdogs are reaped with `kill_tree`, never a bare `kill`.
- `--tools` plus `--strict-mcp-config` are what actually constrain the agent; `--allowedTools` alone does not.
- Both pipelines serialize behind a `mkdir`-based lock with a 1-hour stale reclaim.
- `LLAKE_POST_MERGE_SYNC=1` forces synchronous execution for tests and debugging.

## Code References

- `hooks/post-merge.sh:1-449` — full hook
- `hooks/post-merge.sh:36-42` — sourced libraries
- `hooks/post-merge.sh:45-61` — project root, env override, install check
- `hooks/post-merge.sh:70-73` — recursion guard
- `hooks/post-merge.sh:85-89` — `ingest.pipeline` switch
- `hooks/post-merge.sh:135-165` — branch guard, SHA cursor, invalid-range reset, `COMMIT_RANGE`
- `hooks/post-merge.sh:243-286` — v2 dispatch (pre-generated agent dir, lock, watchdog, `run_ingest_v2`, `kill_tree` reap)
- `hooks/post-merge.sh:45` — sources `ingest-cursor.sh`
- `hooks/post-merge.sh:169-217` — batching gate: clock seeding, `ingest.schedule.*` reads, gate call, fail-open
- `hooks/post-merge.sh:219-238` — `EMPTY` (locked cursor advance) and `WAIT` (deferred) branches
- `hooks/post-merge.sh:419` — legacy success cursor advance via `advance_ingest_cursor`
- `hooks/post-merge.sh:258-282` — prompt rendering and render-failure handling
- `hooks/post-merge.sh:291-296` — lock acquisition and release trap
- `hooks/post-merge.sh:311-326` — claude invocation, `PIPESTATUS` copy, formatter-exit sidecar
- `hooks/post-merge.sh:345-377` — exit-code dispatch and cursor advancement
- `tests/hooks/test_post_merge.sh` — legacy-path integration tests, including flag assertions
- `tests/hooks/test_post_merge_v2.sh` — v2-path integration tests
- `tests/hooks/test_post_merge_lock.sh` — lock acquire / contend / stale-reclaim tests
- `tests/hooks/test_post_merge_gate.sh` — batching-gate integration tests (both pipelines)

## See Also

- [[ingest-v2-pipeline]] — what happens after the v2 hand-off
- [[ingest-v2-orchestrator]] — `run_ingest_v2`, the function this hook calls
- [[post-merge-lock]] — the concurrency lock both pipelines take
- [[hook-log]] — `hook_start` / `hook_end` and the render-failure helpers
- [[claude-p-tools-flag]] — why `--allowedTools` alone was not enough
- [[ingest-template]] — the legacy ingest prompt
- [[agent-run]] — kill trap and watchdog mechanics
- [[agent-id]] — readable agent IDs
- [[format-agent-log]] — `stream-json` → agent.log, and the SIGPIPE contract
- [[is-llake-agent-guard]] — the recursion guard
- [[adr-001-post-merge-trigger]] — why post-merge and not post-commit
- [[config-schema]] — all `ingest.*`, `ingest.schedule.*`, and `ingest.v2.*` keys
- [[ingest-gate]] — the batching gate's decision table and failure modes
- [[ingest-cursor]] — how the SHA cursor and ingest clock move together
- [[watchdog-sleep-orphans]] — why the watchdog is reaped with `kill_tree`
