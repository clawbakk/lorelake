---
title: "Post-Merge Lock"
description: "mkdir-based mutex serializing post-merge ingest runs, with a 1-hour stale reclaim"
tags: [hooks, shell, concurrency, locking, post-merge]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[post-merge-hook]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[bash-3-2-portability]]"
  - "[[runtime-layout]]"
  - "[[ingest-gate]]"
  - "[[ingest-cursor]]"
---
# Post-Merge Lock

## Overview

`hooks/lib/post-merge-lock.sh` provides a per-project mutex so only one post-merge ingest run mutates state at a time. Both the v2 and legacy pipelines take it.

## The race it prevents

Two `git pull`s in quick succession — or a pull in two worktrees sharing one hooks directory — fire `post-merge` twice. Without a lock, two agents concurrently:

- read and write `llake/last-ingest-sha`, where the loser's write can move the cursor **backwards** or skip a range entirely; and
- write the same wiki pages, where the applier's atomic `os.replace` guarantees each file is internally consistent but says nothing about two runs' edits interleaving across files.

The cursor is the more serious of the two: a lost update there means commits are never ingested and nothing reports it.

## Why `mkdir`

`flock` is not available on stock macOS, and LoreLake's shell code targets bash 3.2 (see [[bash-3-2-portability]]). `mkdir` is atomic on every POSIX filesystem and is the portable primitive: the directory's *existence* is the lock, and `mkdir` succeeding is the test-and-set.

```bash
if mkdir "$lockdir" 2>/dev/null; then
  echo $$ > "$lockdir/owner.pid"
  return 0
fi
```

The lock lives at `<llake_root>/.state/post-merge.lock.d/`, with the holder's PID in `owner.pid`.

## Stale reclaim

An agent killed by `SIGKILL` cannot run its release trap, so the lock would otherwise be permanent — ingest silently stops forever. A lock is reclaimed when **both** conditions hold:

1. The directory's mtime is at least `LLAKE_LOCK_STALE_SECONDS` (3600) old, and
2. `kill -0 "$(cat owner.pid)"` fails — the owner is gone.

Requiring both is what keeps the reclaim safe. A long-running but healthy ingest (the v2 timeout default is 1200s, and a large range can approach it) must never have its lock stolen, so age alone is insufficient. A PID check alone is also insufficient, because PIDs are recycled. A reclaim writes a `reclaiming stale lock (age=Ns, owner=PID)` line to `hooks.log` before deleting the directory — an event worth seeing, since it means something died badly.

`stat` differs between macOS (`-f %m`) and GNU (`-c %Y`); the age helper tries the former and falls back to the latter, defaulting to "now" (age 0) if both fail, which conservatively declines to reclaim.

## Release

```bash
release_post_merge_lock() {
  if [ -f "$lockdir/owner.pid" ] && [ "$(cat "$lockdir/owner.pid")" = "$$" ]; then
    rm -rf "$lockdir"
  fi
}
```

The **ownership check is essential**. Without it, a process whose lock had already been reclaimed as stale would delete the *new* owner's lock on exit, and both would then run concurrently — a worse outcome than having no lock at all.

## Usage

Both pipelines acquire inside the already-forked background subshell, not in the foreground:

```bash
if ! acquire_post_merge_lock; then
  printf "%s | %-13s | skipped: post-merge lock held; v2 agent abandoned\n" \
    "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" >> "$LOG_FILE"
  exit 0
fi
trap 'release_post_merge_lock' EXIT
```

The loser **exits 0**. A concurrent merge is normal operation, not an error, and the work is not lost — the cursor was not advanced, so the next `post-merge` picks up the same range.

Two caveats for anyone modifying this:

- Acquisition must happen *inside* the subshell, because `$$` inside a subshell is still the parent shell's PID in bash 3.2; the `owner.pid` written is the value the release check later compares against, so both must be evaluated in the same context.
- The `EXIT` trap must be installed immediately after a successful acquire. Any early `return`/`exit` between the two leaks the lock for an hour.

## Contract with callers

The library reads `STATE_DIR`, `LOG_FILE`, and `HOOK_NAME` from the caller's scope rather than taking parameters — consistent with the other hook libraries, and they are already set by the time it is sourced.

## Key Points

- `mkdir` is the lock primitive: atomic, POSIX, and available where `flock` is not.
- Lock path is `.state/post-merge.lock.d/` with `owner.pid` inside.
- Reclaim requires **both** mtime ≥ 1 hour and a dead owner PID — either alone would be unsafe.
- A reclaim is logged, because it means a previous run died without running its trap.
- Release checks PID ownership, so a reclaimed process cannot delete its successor's lock.
- The losing invocation exits 0 and its range is retried, since the cursor never advanced.
- Acquire inside the background subshell and install the `EXIT` trap immediately after.

## Code References

- `hooks/lib/post-merge-lock.sh:19` — `LLAKE_LOCK_STALE_SECONDS`
- `hooks/lib/post-merge-lock.sh:25-35` — `_llake_lock_age_seconds` and the macOS/GNU `stat` fallback
- `hooks/lib/post-merge-lock.sh:37-45` — `_llake_lock_owner_alive`
- `hooks/lib/post-merge-lock.sh:47-69` — `acquire_post_merge_lock`, including reclaim
- `hooks/lib/post-merge-lock.sh:71-78` — `release_post_merge_lock` and the ownership check
- `hooks/post-merge.sh:190-195` — v2 acquisition
- `hooks/post-merge.sh:291-296` — legacy acquisition
- `tests/hooks/test_post_merge_lock.sh` — acquire, contend, and stale-reclaim tests

## See Also

- [[post-merge-hook]] — both call sites
- [[ingest-v2-orchestrator]] — runs entirely inside the lock
- [[bash-3-2-portability]] — why not `flock`
- [[runtime-layout]] — where the lock sits in `.state/`
