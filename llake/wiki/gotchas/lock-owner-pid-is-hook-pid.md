---
title: "A background run's lock names the hook's PID"
description: "In a disowned post-merge subshell $$ is the hook's PID, which exits at once — a run longer than an hour has its lock reclaimed unless it re-records its own PID"
tags: [gotchas, bash, locking, post-merge, process-management]
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[post-merge-lock]]"
  - "[[ingest-v3-orchestrator]]"
  - "[[bash-3-2-portability]]"
  - "[[post-merge-hook]]"
---
# A background run's lock names the hook's PID

## What goes wrong

Every post-merge pipeline takes the [[post-merge-lock]] inside a `( … ) &` background subshell that the hook then disowns. `acquire_post_merge_lock` records `$$` in `owner.pid`. In a bash subshell `$$` is **not** the subshell's PID: it is the PID of the parent shell, which is the hook process. The hook exits right after disowning the subshell.

The lock therefore names a dead process from almost the moment it is taken. The stale reclaim has two conditions: the lock is at least an hour old, and its owner is dead. The second is already true, so an hour after acquiring, the next `post-merge` reclaims the lock **while the run is still working**. Two runs then write the cursor and the wiki concurrently.

Legacy and v2 do not hit this with defaults, because their deadline (1200 s) is well under an hour. Ingest v3 does: its deadline is `ingest.v3.timeoutSeconds` (3600) plus 300 s watchdog grace. Raising a legacy or v2 timeout above an hour would expose those pipelines too.

A second trap is ordering. If the kill traps are installed after the lock is taken, a `TERM` in between kills the subshell with the lock held. Nothing releases it until the one-hour reclaim.

## The fix (v3)

`hooks/lib/ingest-v3.sh` and the v3 branch of `hooks/post-merge.sh` do three things:

- `MY_PID=$(sh -c 'echo $PPID')` gets the subshell's real PID. Bash 3.2 has no `$BASHPID`; see [[bash-3-2-portability]].
- `claim_v3_lock` rewrites `owner.pid` with `MY_PID` right after acquiring, so the owner stays alive for the whole run.
- The `TERM`/`INT`/`USR1` traps are armed **before** acquiring. `release_v3_lock` removes the lock only when `owner.pid` is `MY_PID`, or the hook's `$$` (written in the moment between acquire and claim). Before the lock is the run's own, the trap's release does nothing.

## How to apply

Any new code that holds the post-merge lock from a background subshell for long should do the same: re-record its own PID after acquiring, arm its traps before acquiring, and keep the ownership check in release.

## Key Points

- `$$` in a subshell is the parent shell's PID, and that parent (the hook) exits immediately.
- Lock age plus a dead owner is enough to reclaim, so any run longer than an hour loses its lock unless it re-claims.
- v3 re-claims with `MY_PID`, arms traps first, and releases only its own lock.

## Code References

- `hooks/lib/ingest-v3.sh:47-59` — `claim_v3_lock`, `release_v3_lock`
- `hooks/post-merge.sh:328-336` — traps before `acquire_post_merge_lock`, then `claim_v3_lock`
- `tests/hooks/test_post_merge_v3.sh` — lock ownership tests

## See Also

- [[post-merge-lock]] — the lock and its reclaim rule
- [[ingest-v3-orchestrator]] — the v3 lifecycle
- [[bash-3-2-portability]] — why there is no `$BASHPID`
