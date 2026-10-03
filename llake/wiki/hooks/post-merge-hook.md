---
title: "Post-Merge Hook"
description: "Git post-merge hook: branch guard, SHA cursor, batching gate, concurrency lock, and dispatch to the legacy, v2 or v3 ingest pipeline"
tags: [hooks, post-merge, ingest, git]
created: 2026-04-23
updated: 2026-10-03
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
  - "[[ingest-v3-pipeline]]"
  - "[[ingest-v3-orchestrator]]"
  - "[[ingest-v3-gap-record]]"
  - "[[ingest-v3-finalize]]"
  - "[[lock-owner-pid-is-hook-pid]]"
---
## Overview

`hooks/post-merge.sh` fires after `git pull` merges new commits into the local working tree. Its job is **incremental ingest**: detect whether the merge landed on the configured branch, compute the commit range since the last successful ingest, and dispatch background work to update the wiki from what changed in the code.

The hook is wired to the git `post-merge` hook (not Claude Code's hook system). It is the only LoreLake hook that uses `git rev-parse --show-toplevel` rather than a `llake/config.json` marker walk to find the project root — `post-merge` fires inside a git repo by definition.

The hook is a **dispatcher for three pipelines**. Everything down to the commit-range computation and the batching gate is shared. Then `ingest.pipeline` decides where control goes: to [[ingest-v2-orchestrator]] (`v2`), to [[ingest-v3-orchestrator]] (`v3`), or through to the legacy single-agent path in the same file. See [[ingest-v2-pipeline]] and [[ingest-v3-pipeline]] for the two newer architectures and [[adr-plan-apply-split]] for why v2 exists.

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
| `hooks/lib/agent-run.sh` | `setup_kill_trap`, `kill_tree`, `_agent_cleanup` (sourced inside the background subshell) — see [[agent-run]] |
| `hooks/lib/ingest-v2.sh` | `run_ingest_v2` (sourced only when `ingest.pipeline` is `v2`) |
| `hooks/lib/ingest-v3.sh` | `run_ingest_v3`, `claim_v3_lock`, `release_v3_lock`, `_ingest_v3_on_kill` (sourced only when `ingest.pipeline` is `v3`) — see [[ingest-v3-orchestrator]] |

## Shared preamble (all pipelines)

1. **Project root** — `git rev-parse --show-toplevel`, then `LLAKE_PROJECT_ROOT` as a test override (`hooks/post-merge.sh:46-52`).
2. **Install check** — no `llake/config.json` means silent `exit 0`, so the shim can be installed before LoreLake is bootstrapped (`hooks/post-merge.sh:62`).
3. **`hook_start`** — opens the `hooks.log` line, rotating the log and marking any prior unterminated line `CRASHED` (`hooks/post-merge.sh:68`).
4. **Recursion guard** — bail when `IS_LLAKE_AGENT=true`, so agents' own git activity cannot re-trigger ingest (`hooks/post-merge.sh:70-75`). See [[is-llake-agent-guard]].
5. **Master toggle** — `ingest.enabled` (`hooks/post-merge.sh:77-82`).
6. **Pipeline switch** — `ingest.pipeline` is read into `USE_INGEST_V2` and `USE_INGEST_V3`. Any value other than the literal `v2` or `v3` means legacy (`hooks/post-merge.sh:84-95`).
7. **Config reads** — budget, timeout, branch, model, effort, `allowedTools`, `include`, and the `--model`/`--effort` flags (`hooks/post-merge.sh:97-138`). Only the legacy branch uses the model, effort, budget and tool settings.
8. **Branch guard** — skip unless `HEAD` is on `ingest.branch` (`hooks/post-merge.sh:140-145`).
9. **SHA cursor** — read `llake/last-ingest-sha`; skip if absent or equal to `HEAD`; on an unreachable range (force push), reset the cursor and the ingest clock to `HEAD` with `advance_ingest_cursor` and skip (`hooks/post-merge.sh:147-169`).
10. **Batching gate** — decide `EMPTY` / `WAIT` / `RUN` before any pipeline spawns (see below).

`COMMIT_RANGE` (`<last7>..<current7>`) is computed at `hooks/post-merge.sh:171` and used in every log line from here on.

## Batching gate (all pipelines)

Before any pipeline spawns, `hooks/lib/ingest_gate.py` measures the net `git diff --numstat <last>..<current>` of the range under `ingest.include` and returns one verdict (`hooks/post-merge.sh:173-255`). See [[ingest-gate]] for the full decision table.

| Verdict | Action | Cursor |
|---|---|---|
| `EMPTY`: nothing under the include paths changed | take the post-merge lock, `advance_ingest_cursor`, log `skipped: no relevant file changes` | advanced (held if the lock is busy) |
| `WAIT`: pile below `ingest.schedule.minChangedLines` **and** younger than `ingest.schedule.maxAgeHours` | log `deferred: ...` and exit | held, so the next merge sees a wider range |
| `RUN`: either arm tripped, or schedule disabled, forced, or gate error | continue to the pipeline switch | per the pipeline's own contract |

Three details:

- `ensure_ingest_clock` runs first. It seeds `.state/last-ingest-at` in fresh clones, so a clone does not force an ingest on its first merge. See [[ingest-cursor]].
- The gate **fails open**. A nonzero gate exit becomes `RUN reason=gate-error`, and a sanitized stderr summary is appended only to the spawn log line.
- `LLAKE_IGNORE_SCHEDULE=1` forces `RUN` for a manual flush, and `ingest.schedule.enabled: false` disables batching. Neither one overrides `EMPTY`.

**v3 override** (`hooks/post-merge.sh:218-229`): only when `ingest.pipeline` is `v3` and the verdict is `WAIT` or `EMPTY`, the hook runs `python3 hooks/lib/ingest-v3.py owed-major --llake-root <llake>`. If that exits 0, meaning an open, non-stuck major gap is owed in `llake/ingest-gaps.json`, the verdict becomes `RUN`. The detail gains `reason=gaps` (was `WAIT`) or `reason=gaps-only` (was `EMPTY`). Legacy and v2 never read the gap record. See [[ingest-v3-gap-record]].

The gate replaced the legacy branch's old pre-flight relevance check (`git diff --name-only ... | head -1`). The v2 branch never had such a check and spawned even for ranges that touched no included path. It now shares the gate's `EMPTY` skip.

## Branch A — the v2 pipeline

When `ingest.pipeline` is `v2` (and the gate returned `RUN`), `hooks/post-merge.sh:257-303` takes over and **nothing below it runs**. The hook:

- sources `hooks/lib/ingest-v2.sh`;
- pre-generates the agent ID, agent dir, and `agent.log` path *before* forking, because the watchdog needs `AGENT_LOG` and `MAX_TIMEOUT_SEC` visible when it fires;
- forks a subshell that installs the kill trap, acquires the post-merge lock (`hooks/post-merge.sh:280`), starts a watchdog on `ingest.v2.timeoutSeconds`, calls `run_ingest_v2`, then reaps the watchdog with `kill_tree` (`hooks/post-merge.sh:292`). See [[watchdog-sleep-orphans]];
- logs `done: spawned v2 agent (range: ..., timeout: ...s, gate: <detail>)` and exits.

The v2 branch has its own timeout key (`ingest.v2.timeoutSeconds`), separate from `ingest.timeoutSeconds`. Everything after dispatch belongs to [[ingest-v2-orchestrator]]: context building, planning, applying, the fixer, the cursor write and the completion log line.

## Branch B — the v3 pipeline

When `ingest.pipeline` is `v3` (and the gate returned `RUN`, possibly through the v3 override), `hooks/post-merge.sh:305-355` takes over and nothing below it runs. The hook:

- sources `hooks/lib/ingest-v3.sh`, reads `ingest.v3.timeoutSeconds` as the soft run deadline, and sets the hard watchdog to deadline + `LLAKE_V3_WATCHDOG_GRACE_SECONDS` (300);
- pre-generates the agent ID, `.state/agents/<id>/`, `agent.log` and `orchestrator.pid`;
- forks a subshell that records its own PID (`MY_PID=$(sh -c 'echo $PPID')`) and **arms the TERM/INT/USR1 kill traps before taking the lock**. It then acquires the post-merge lock, installs `trap 'release_v3_lock' EXIT`, and calls `claim_v3_lock` so the lock names the long-lived subshell rather than the hook (see [[lock-owner-pid-is-hook-pid]]). Last, it starts the watchdog, runs `run_ingest_v3`, and reaps the watchdog with `kill_tree`;
- logs `done: spawned v3 agent (range: ..., deadline: <N>s, gate: <detail>)` and exits.

The Python orchestrator owns everything else: planning, agents, finalize, the cursor and the completion line in `hooks.log`. A kill or watchdog timeout reverts the run's writes under `llake/`. See [[ingest-v3-orchestrator]].

## Branch C — the legacy pipeline

The legacy path spawns one `claude -p` agent that reads diffs and writes wiki pages itself.

- **Pathspec**: `PATHSPEC_INCLUDE` is built from `ingest.include` for the agent's own git commands (`hooks/post-merge.sh:367-372`). The shared batching gate's `EMPTY` verdict replaces the old pre-flight relevance check.
- **Prompt rendering** — `render-prompt.py` fills `AGENT_ID`, `PROJECT_ROOT`, `LAST_SHA`, `CURRENT_SHA`, `COMMIT_RANGE`, `PATHSPEC_INCLUDE`, `LLAKE_ROOT`, `WIKI_ROOT`, `SCHEMA_DIR`. Renderer stderr goes to `$AGENT_DIR/render-stderr.tmp`, per agent rather than in `/tmp`, so it survives for post-mortem and does not accumulate. `log_render_failure` writes a render failure to the agent log, and `render_err_summary` summarises it into `hooks.log` (`hooks/post-merge.sh:384-409`). See [[ingest-template]] and [[render-prompt]].
- **Background subshell** — kill trap, lock acquisition (`hooks/post-merge.sh:419`), watchdog, then the agent.

## The claude invocation

```
claude $MODEL_FLAG $EFFORT_FLAG -p "$INGEST_PROMPT"
  --tools "$ALLOWED_TOOLS"
  --allowedTools "$ALLOWED_TOOLS"
  --strict-mcp-config
  --max-budget-usd "$MAX_BUDGET_USD"
  --output-format stream-json --verbose
```

`--tools` is what actually restricts the built-in tool set in `-p` mode; `--allowedTools` only pre-approves the permission prompt. `--strict-mcp-config` blocks ambient user-global MCP servers from leaking into the agent. See [[claude-p-tools-flag]].

`$MODEL_FLAG` and `$EFFORT_FLAG` are empty strings when the config values are empty, so no flag is passed and the CLI default applies (`hooks/post-merge.sh:130-138`).

## Exit-code dispatch and the cursor contract (legacy)

The agent runs inside an inner subshell so that `PIPESTATUS[0]` (claude's exit code) becomes the subshell's exit code — a bare `wait` would return the formatter's, which is always 0. `PIPESTATUS` is copied into a plain array (`_pstat=("${PIPESTATUS[@]}")`) because bash 3.2 resets it on the next assignment (`hooks/post-merge.sh:436-451`). See [[bash-3-2-portability]].

If the formatter exits nonzero, its code is written to `$AGENT_DIR/formatter-exit`; the outer subshell reads and removes that sidecar before dispatching (`hooks/post-merge.sh:474-476`).

| Condition | `hooks.log` line | `last-ingest-sha` |
|---|---|---|
| Formatter crashed (sidecar present) | `failed: formatter crashed ... — cursor held` | held |
| Agent exit 0 | `completed: agent <id> ... sha: advanced to <sha7>` | advanced (together with `.state/last-ingest-at`) |
| Agent exit 137/143 without the trap firing | `killed: agent <id> ... external` | held |
| Any other nonzero exit | `failed: agent <id> (exit N, ...)` | held |

A formatter crash makes `claude` take SIGPIPE and exit 141, which lands in the FAILED branch — the cursor is deliberately held until the formatter bug is fixed. See [[format-agent-log]].

**In the legacy path the hook writes the cursor, not the agent** (`hooks/post-merge.sh:489`). It does so through `advance_ingest_cursor`, which also stamps `.state/last-ingest-at` for the gate's age arm. When the agent finishes, the watchdog is reaped with `kill_tree` rather than `kill` (`hooks/post-merge.sh:464`). Re-processing a held range is safe because ingest is idempotent: it updates pages rather than duplicating them. Under v2 and v3 the orchestrator writes the cursor. v3 writes it from Python with the same SHA-plus-clock pair (see [[ingest-v3-finalize]]).

## Concurrency

All three pipelines acquire the post-merge lock inside their background subshell before touching `last-ingest-sha` or the wiki. A second invocation that loses the race logs `skipped: post-merge lock held; <v3|v2|legacy> agent abandoned` and exits 0. A lock older than one hour whose owner PID is dead is reclaimed. Legacy and v2 release the lock from an `EXIT` trap via `release_post_merge_lock`. v3 re-records the owner as its own subshell (`claim_v3_lock`) and releases with `release_v3_lock`, so a v3 run that lasts longer than an hour is never reclaimed as stale. See [[post-merge-lock]].

## Test / debug escape hatch

`LLAKE_POST_MERGE_SYNC=1` makes the hook `wait` for the background subshell instead of disowning it, so tests can assert on the final state of `hooks.log`, `agent.log`, and the SHA file (`hooks/post-merge.sh:509-515` for legacy; each newer branch has its own copy). `tests/hooks/test_post_merge.sh`, `tests/hooks/test_post_merge_v2.sh`, `tests/hooks/test_post_merge_v3.sh` and `tests/hooks/test_post_merge_gate.sh` all rely on it.

`LLAKE_IGNORE_SCHEDULE=1` forces the batching gate to `RUN` unless the pile is empty. Use it to flush a deferred pile by hand. See [[ingest-gate]].

## Runtime state layout

```
<project>/llake/
  last-ingest-sha                  # cursor: last successfully ingested commit
  ingest-gaps.json                 # v3 gap record (committed)
  .state/
    hooks.log                      # one line per hook invocation
    last-ingest-at                 # clock: epoch seconds of the last cursor advance (gate age arm)
    ingest-failures.json           # v3: analysis failure counter
    post-merge.lock.d/owner.pid    # concurrency lock (present only while held)
    agents/<agent-id>/
      agent.log                    # full trace (format-agent-log output; v3: the run's own trace)
      ingest.pid                   # legacy: PID during the run
      orchestrator.pid             # v2/v3: PID of the orchestrator subshell
      render-stderr.tmp            # legacy: renderer stderr, on failure
      formatter-exit               # transient sidecar on formatter crash
      context/ plan.json applied.json failed.json   # v2 only
      run.json pre/ inputs/ brief/ bundles/ stages/ report.md   # v3 only
```

## hooks.log format

```
2026-04-23 10:14:00 | post-merge    | started → done: spawned agent calm-owl-101400-1f3a (commits: a1b2c3d..e4f5a6b, timeout: 1200s, gate: lines=1830 files=12 age_h=3.1 reason=lines)
2026-04-23 10:19:42 | agent-done    | completed: agent calm-owl-101400-1f3a finished (exit 0, commits: a1b2c3d..e4f5a6b, sha: advanced to e4f5a6b)
```

A v3 run (illustrative values):

```
... | post-merge    | started → done: spawned v3 agent (range: a1b2c3d..e4f5a6b, deadline: 3600s, gate: lines=1830 files=12 age_h=3.1 reason=lines)
... | agent-done    | completed: agent <id> v3 range (updated 6, created 1, gaps 2/0 major, $14.20, sha e4f5a6b)
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
- `ingest.pipeline` selects the pipeline. `v2` hands off to `run_ingest_v2`, `v3` hands off to `run_ingest_v3`, and anything else runs the legacy in-file path.
- Project root comes from git, not the marker walk used by the session hooks.
- In the legacy path **the hook writes `last-ingest-sha`**, and only on a clean run; v2 and v3 write it from their orchestrators. Kills, nonzero exits, and formatter crashes all hold the cursor.
- Force-pushed / unreachable ranges reset the cursor to `HEAD` without running an agent.
- A batching gate runs before every pipeline. `EMPTY` advances the cursor under the lock and skips. `WAIT` holds the cursor and skips. `RUN` spawns. It fails open to `RUN` on any gate error.
- Under v3 only, an owed major gap turns `WAIT` into `RUN reason=gaps` and `EMPTY` into `RUN reason=gaps-only`.
- Every shell cursor write goes through `advance_ingest_cursor`, which also stamps `.state/last-ingest-at`.
- Watchdogs are reaped with `kill_tree`, never a bare `kill`.
- `--tools` plus `--strict-mcp-config` are what actually constrain the agent; `--allowedTools` alone does not.
- All pipelines serialize behind a `mkdir`-based lock with a 1-hour stale reclaim; v3 re-claims the lock under its own PID.
- `LLAKE_POST_MERGE_SYNC=1` forces synchronous execution for tests and debugging.

## Code References

- `hooks/post-merge.sh:1-518` — full hook
- `hooks/post-merge.sh:35-44` — sourced libraries
- `hooks/post-merge.sh:46-52` — project root and env override
- `hooks/post-merge.sh:70-75` — recursion guard
- `hooks/post-merge.sh:84-95` — `ingest.pipeline` switch (`USE_INGEST_V2`, `USE_INGEST_V3`)
- `hooks/post-merge.sh:140-171` — branch guard, SHA cursor, invalid-range reset, `COMMIT_RANGE`
- `hooks/post-merge.sh:173-216` — batching gate: clock seeding, `ingest.schedule.*` reads, gate call, fail-open
- `hooks/post-merge.sh:218-229` — v3 gate rule (`owed-major`)
- `hooks/post-merge.sh:236-255` — `EMPTY` (locked cursor advance) and `WAIT` (deferred) branches
- `hooks/post-merge.sh:257-303` — v2 dispatch
- `hooks/post-merge.sh:305-355` — v3 dispatch (traps before lock, `claim_v3_lock`, watchdog, `run_ingest_v3`)
- `hooks/post-merge.sh:384-409` — legacy prompt rendering and render-failure handling
- `hooks/post-merge.sh:436-451` — legacy claude invocation, `PIPESTATUS` copy, formatter-exit sidecar
- `hooks/post-merge.sh:489` — legacy success cursor advance via `advance_ingest_cursor`
- `tests/hooks/test_post_merge.sh` — legacy-path integration tests, including flag assertions
- `tests/hooks/test_post_merge_v2.sh` — v2-path integration tests
- `tests/hooks/test_post_merge_v3.sh` — v3-path integration tests (gate rule, lock ownership, kill revert)
- `tests/hooks/test_post_merge_lock.sh` — lock acquire / contend / stale-reclaim tests
- `tests/hooks/test_post_merge_gate.sh` — batching-gate integration tests

## See Also

- [[ingest-v3-pipeline]] — the staged v3 pipeline
- [[ingest-v3-orchestrator]] — the v3 branch's bash glue, kill trap and Python run
- [[ingest-v3-gap-record]] — the file behind the v3 gate override
- [[ingest-v2-pipeline]] — what happens after the v2 hand-off
- [[ingest-v2-orchestrator]] — `run_ingest_v2`, the function this hook calls
- [[post-merge-lock]] — the concurrency lock every pipeline takes
- [[hook-log]] — `hook_start` / `hook_end` and the render-failure helpers
- [[claude-p-tools-flag]] — why `--allowedTools` alone was not enough
- [[ingest-template]] — the legacy ingest prompt
- [[agent-run]] — kill trap and watchdog mechanics
- [[agent-id]] — readable agent IDs
- [[format-agent-log]] — `stream-json` → agent.log, and the SIGPIPE contract
- [[is-llake-agent-guard]] — the recursion guard
- [[adr-001-post-merge-trigger]] — why post-merge and not post-commit
- [[config-schema]] — all `ingest.*`, `ingest.schedule.*`, `ingest.v2.*` and `ingest.v3.*` keys
- [[ingest-gate]] — the batching gate's decision table and failure modes
- [[ingest-cursor]] — how the SHA cursor and ingest clock move together
- [[watchdog-sleep-orphans]] — why the watchdog is reaped with `kill_tree`
