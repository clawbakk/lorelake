---
title: "Ingest v3 Orchestrator"
description: "How post-merge runs ingest v3 — the v3 lock claim, watchdog and kill trap with revert, run.py's exit codes and hooks.log lines, and the ingest-v3.py subcommands"
tags:
  - "hooks"
  - "ingest"
  - "v3"
  - "orchestration"
  - "process-management"
  - "shell"
  - "python"
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[post-merge-hook]]"
  - "[[post-merge-lock]]"
  - "[[agent-run]]"
  - "[[hook-log]]"
  - "[[ingest-gate]]"
  - "[[watchdog-sleep-orphans]]"
  - "[[lock-owner-pid-is-hook-pid]]"
  - "[[v3-agents-escape-tree-kill]]"
  - "[[ingest-v3-snapshots]]"
  - "[[ingest-v3-gap-record]]"
---
# Ingest v3 Orchestrator

## Overview

Three layers run a v3 ingest:

- the v3 branch of `hooks/post-merge.sh`;
- the bash glue in `hooks/lib/ingest-v3.sh`;
- the Python orchestrator `hooks/lib/ingest_v3/run.py`, entered as `python3 hooks/lib/ingest-v3.py run`.

Bash owns the process lifecycle: background subshell, post-merge lock, watchdog and kill trap. Python owns the run itself: planning, agents, finalize and the cursor. This page covers how a run starts, how it ends and how it is killed. The stages in between are described in [[ingest-v3-pipeline]].

## The post-merge v3 branch

`hooks/post-merge.sh:305-355` is reached only when `ingest.pipeline` is `v3` and the batching gate returned `RUN`. That includes the two v3 overrides for owed gaps, `reason=gaps` and `reason=gaps-only` (see [[ingest-gate]]). Nothing after this branch runs. In order, it:

1. sources `hooks/lib/ingest-v3.sh` and reads `ingest.v3.timeoutSeconds` as the run deadline. The watchdog time is deadline + `LLAKE_V3_WATCHDOG_GRACE_SECONDS` (default 300);
2. generates the agent ID and creates `.state/agents/<id>/` with `agent.log` and `orchestrator.pid`;
3. forks a subshell that sources [[agent-run]] and computes its own PID as `MY_PID=$(sh -c 'echo $PPID')`, because bash 3.2 has no `$BASHPID`. It writes that PID to `orchestrator.pid`;
4. **arms the kill traps first**: `TERM`/`INT` call `_ingest_v3_on_kill user`, and `USR1` calls `_ingest_v3_on_kill timeout`;
5. takes the [[post-merge-lock]]. If the lock is held, it logs `skipped: post-merge lock held; v3 agent abandoned` and exits 0;
6. installs `trap 'release_v3_lock' EXIT` and calls `claim_v3_lock`;
7. starts the watchdog subshell, which sends `USR1` to `MY_PID` after the watchdog time. It then calls `run_ingest_v3` and afterwards reaps the watchdog with `kill_tree` (see [[watchdog-sleep-orphans]]).

The foreground hook disowns the subshell, or waits for it when `LLAKE_POST_MERGE_SYNC=1`. It logs `done: spawned v3 agent (range: <range>, deadline: <N>s, gate: <detail>)`.

## `hooks/lib/ingest-v3.sh`

| Function / variable | Purpose |
|---|---|
| `LLAKE_V3_WATCHDOG_GRACE_SECONDS` (300) | Hard watchdog grace after the soft deadline. The deadline itself is handled in Python; the watchdog only catches a hung process. Env override for tests. |
| `LLAKE_V3_KILL_GRACE_SECONDS` (20) | How long the Python run gets after its own `SIGTERM` to stop its agents and revert before the tree kill. |
| `run_ingest_v3` | Writes the `agent.log` header and computes the deadline in epoch seconds. Runs `ingest-v3.py run --project-root --agent-id --agent-dir --deadline` with `IS_LLAKE_AGENT=true` **in the background** and `wait`s, so a trap fires at once instead of after Python exits. |
| `claim_v3_lock` | Rewrites `owner.pid` with `MY_PID`, so the lock names a process that lives as long as the run. |
| `release_v3_lock` | Removes the lock only when `owner.pid` is `MY_PID` or the hook's `$$`, which is the value written between acquire and claim. |
| `_ingest_v3_on_kill` | The kill trap, described below. |

[[lock-owner-pid-is-hook-pid]] explains why the lock is claimed a second time.

## Kill path

When `TERM`, `INT` or the watchdog's `USR1` reaches the subshell, `_ingest_v3_on_kill`:

1. runs `set +e`, because `kill_tree` and `pkill` return nonzero in normal use. It then ignores further signals;
2. sends `SIGTERM` to the Python run and polls every 0.2 s for up to `LLAKE_V3_KILL_GRACE_SECONDS`;
3. tree-kills `MY_PID`, sleeps 1 s and `pkill -KILL`s any child left;
4. runs `ingest-v3.py revert-run --project-root --agent-dir`. A failure is logged to `agent.log` (`revert-run failed ... run journal left for the next run's recovery`) and never stops the trap;
5. calls `release_v3_lock`, then `_agent_cleanup <reason>`, which writes the kill marker and the `hooks.log` line.

Python gets the first chance because writer agents run in their own session. Once Python is gone, the tree kill cannot reach them (see [[v3-agents-escape-tree-kill]]).

Inside Python:

- `SIGTERM`, `SIGHUP` and `SIGINT` raise `Killed`. It is a `BaseException`, so no `except Exception` on the way swallows it.
- Only the first signal acts. A signal that lands while an agent is being spawned is deferred until the agent is registered.
- The writer pool kills and reverts its own bundles on the way out.
- `_on_killed` then kills any other live agent (analysis, recall) and runs `snapshots.revert_run` (see [[ingest-v3-snapshots]]). It returns 0 only when the kill came after finalize, because the run then stands; otherwise it returns 1.

The bash trap's `revert-run` runs again afterwards. That second revert changes nothing, because the journal is marked `aborted` or `finalized`.

## `run.run` exit codes and `hooks.log`

Every normal outcome writes exactly one `agent-done` line to `.state/hooks.log`, in the [[hook-log]] format. The run's own trace goes to `agent.log` as `[HH:MM:SS] ...` lines.

| Outcome | Exit | `hooks.log` message |
|---|---|---|
| `empty` run | 0 | `empty: nothing to ingest, nothing owed (agent <id> v3, sha <h>)` |
| `skip` run | 0 | `skipped: single-commit range <b>..<h> failed analysis twice (agent <id> v3)` |
| finalized | 0 | `completed: agent <id> v3 <kind> (updated N, created M, gaps G/K major, $S, sha <h>)` |
| held (`HoldRun`: analysis failure, invalid brief, stop-after) | 1 | `held: agent <id> v3 (<reason>) — cursor held` |
| internal error (any other exception) | 1 | `held: agent <id> v3 (internal error: <msg>) — cursor held` |
| killed | 0 or 1 | no Python line; the bash trap's `_agent_cleanup` logs the kill |

In the completed line, `gaps G/K major` is the total gap count over the major count. If writing `report.md` fails after finalize, the failure is logged to `agent.log` and the exit code stays 0.

## `ingest-v3.py` subcommands

`hooks/lib/ingest-v3.py` enters `ingest_v3.cli.main`:

| Command | Used by | Behavior |
|---|---|---|
| `run --project-root --agent-id --agent-dir --deadline` | `run_ingest_v3` | one v3 run |
| `revert-run --project-root --agent-dir` | the kill trap | undo a killed run's writes under `llake/`; prints a JSON result |
| `owed-major --llake-root` | the post-merge gate rule | exit 0 when an open, non-stuck major gap is owed, else 1 |
| `validate-gaps --llake-root` | `/llake-doctor` Check 8.6 | prints `ABSENT`, or `OK: ...` followed by `STUCK:`/`RANGE:` lines, or `INVALID: ...` (exit 1) |

Called without a subcommand, it prints help and exits 2.

## Key Points

- Bash owns the lifecycle (lock, watchdog, kill trap); Python owns the run and the cursor.
- Kill traps are armed before the lock is taken. `release_v3_lock` does nothing until the lock belongs to this run.
- The run re-records itself as the lock owner, so a run longer than an hour is never reclaimed as stale.
- On a kill, Python gets `SIGTERM` and up to 20 s to stop its agents and revert before the tree kill. The bash trap then reverts again (a no-op if Python finished) and always releases the lock.
- `run` exits 0 when it finalized or had nothing to do, and 1 whenever the cursor holds.
- Each run writes exactly one `agent-done` line to `hooks.log`.

## Code References

- `hooks/post-merge.sh:305-355` — v3 branch: watchdog time, subshell, traps, lock, claim, watchdog, run
- `hooks/lib/ingest-v3.sh:14` — `LLAKE_V3_WATCHDOG_GRACE_SECONDS`
- `hooks/lib/ingest-v3.sh:18` — `LLAKE_V3_KILL_GRACE_SECONDS`
- `hooks/lib/ingest-v3.sh:22` — `run_ingest_v3`
- `hooks/lib/ingest-v3.sh:47-59` — `claim_v3_lock`, `release_v3_lock`
- `hooks/lib/ingest-v3.sh:69` — `_ingest_v3_on_kill`
- `hooks/lib/ingest_v3/run.py:44` — `Killed`
- `hooks/lib/ingest_v3/run.py:62` — `_install_kill_handlers`
- `hooks/lib/ingest_v3/run.py:82` — `_on_killed`
- `hooks/lib/ingest_v3/run.py:243` — `run`
- `hooks/lib/ingest_v3/cli.py:53` — `build_parser`, the subcommands
- `tests/hooks/test_post_merge_v3.sh` — hook integration: gate rule, lock ownership, kill revert
- `tests/hooks/test_bash32_portability.sh` — bash 3.2 portability checks for the v3 shell code
- `tests/lib/test_v3_run_e2e.py` — cursor-table rows end to end with the fake `claude`

## See Also

- [[ingest-v3-pipeline]] — the stages `run.py` drives
- [[post-merge-hook]] — the caller
- [[post-merge-lock]] — the lock and its stale reclaim
- [[ingest-v3-snapshots]] — what `revert-run` restores
- [[agent-run]] — `_agent_cleanup` and `kill_tree`
- [[lock-owner-pid-is-hook-pid]] — why `claim_v3_lock` exists
- [[v3-agents-escape-tree-kill]] — why Python is signalled before the tree kill
