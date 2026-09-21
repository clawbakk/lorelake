---
title: "Ingest Cursor Helpers"
description: "hooks/lib/ingest-cursor.sh — advance last-ingest-sha and the .state/last-ingest-at clock together, and seed the clock in fresh clones"
tags: [hooks, shell, ingest, cursor, post-merge]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[ingest-gate]]"
  - "[[post-merge-hook]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[runtime-layout]]"
  - "[[post-merge-lock]]"
  - "[[bash-3-2-portability]]"
---
# Ingest Cursor Helpers

## Overview

`hooks/lib/ingest-cursor.sh` is a small sourced bash library with two functions. Together they keep the ingest **cursor** (`llake/last-ingest-sha`) and the ingest **clock** (`llake/.state/last-ingest-at`) moving together. The cursor records *which commit* was last ingested. The clock records *when* the wiki was last known to be current.

The clock exists for the batching gate's age arm ([[ingest-gate]]). If the two files drifted, the gate would measure age from the wrong moment. For example, a run that advanced only the SHA would leave an old clock behind, and the next small merge would trip the age arm immediately.

Before this library, each writer did `echo "$CURRENT_SHA" > "$SHA_FILE"` inline, at three sites in `post-merge.sh` and one in `ingest-v2.sh`. All of them now route through `advance_ingest_cursor`.

## Required environment

The caller sets:

- `LLAKE_ROOT`: `<project>/llake`
- `STATE_DIR`: `<project>/llake/.state`

`hooks/post-merge.sh` sources the library at top level (`hooks/post-merge.sh:45`). That makes both functions visible inside the forked background subshells and inside `run_ingest_v2`, which does not source it itself.

## `advance_ingest_cursor <sha>`

```bash
advance_ingest_cursor() {
  local sha="$1"
  [ -n "$sha" ] || return 1
  mkdir -p "$STATE_DIR" 2>/dev/null
  echo "$sha" > "$LLAKE_ROOT/last-ingest-sha"
  date +%s > "$STATE_DIR/last-ingest-at"
}
```

It returns 1 on an empty argument, so an unset variable can never blank the cursor. It creates `.state/` if needed.

| Call site | When |
|---|---|
| `hooks/post-merge.sh:162` | Invalid range (e.g. force push): reset to HEAD, no agent |
| `hooks/post-merge.sh:226` | Gate verdict `EMPTY`, while holding the post-merge lock |
| `hooks/post-merge.sh:419` | Legacy agent exited 0 |
| `hooks/lib/ingest-v2.sh:203` | v2 finalize, even with some failed ops |

The `EMPTY` path resets the clock too. `last-ingest-at` means "the wiki is known-current as of T", and after an empty-pile skip that is true.

## `ensure_ingest_clock`

It seeds `last-ingest-at` with the current time only when the file is absent, and it never overwrites. `post-merge.sh` calls it right before the gate.

The reason: `.state/` is gitignored and does not survive a fresh clone. Without seeding, a missing clock would read as overdue and force a full ingest on the first merge in every clone, on every machine. Seeding starts that clone's own batching window instead.

## Writers that bypass it

Bootstrap and `/llake-doctor` repair still write `last-ingest-sha` alone (as documented in `schema/operations.md` and the doctor skill). `ensure_ingest_clock` only covers that when the clock is missing. If a clock already exists, it is left untouched. So after a bootstrap on a machine with an old clock, the age arm may trip on the next merge. That errs toward running an ingest, never toward starving one.

## Constraints

- **Not atomic, and takes no lock itself.** The two writes are sequential. Callers that could race an in-flight agent must hold the [[post-merge-lock]]:
  - The `EMPTY` branch takes the lock explicitly.
  - The legacy and v2 success paths run inside the already-locked subshell.
  - The invalid-range reset runs in the foreground without the lock.
- The clock is epoch seconds from `date +%s`, which `ingest_gate.py` parses as a float.
- Bash 3.2 portable. See [[bash-3-2-portability]].

## Key Points

- The cursor says *which commit*, and the clock says *when*. They must always move together.
- Every hook-side cursor write goes through `advance_ingest_cursor`, so nothing writes `last-ingest-sha` inline anymore.
- An empty SHA argument is refused (return 1).
- `ensure_ingest_clock` seeds a missing clock so fresh clones don't force an ingest. It never overwrites.
- Bootstrap and doctor still write the SHA alone. Any drift only makes the age arm trip earlier.
- The library takes no lock. Callers are responsible for serialization.

## Code References

- `hooks/lib/ingest-cursor.sh:1-27` — header documenting the pairing and the bypassing writers
- `hooks/lib/ingest-cursor.sh:29-35` — `advance_ingest_cursor`
- `hooks/lib/ingest-cursor.sh:43-47` — `ensure_ingest_clock`
- `hooks/post-merge.sh:45` — sourced at top level
- `hooks/post-merge.sh:180` — `ensure_ingest_clock` before the gate
- `tests/hooks/test_ingest_cursor.sh` — unit tests for both functions
- `tests/hooks/test_post_merge_gate.sh` — end-to-end checks that `EMPTY` and v2 success advance both files

## See Also

- [[ingest-gate]] — the age arm that reads the clock
- [[post-merge-hook]] — three of the four call sites
- [[ingest-v2-orchestrator]] — the v2 finalize call site
- [[runtime-layout]] — `last-ingest-sha` and `.state/last-ingest-at` in the project tree
- [[post-merge-lock]] — what serializes cursor writes
