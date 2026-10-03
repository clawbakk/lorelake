---
title: "Ingest v3 Run Planning and Run State"
description: "plan.py and state.py — v3 run kinds (empty, gap-only, range, split, skip), the analysis failure counter, the run journal and dead-run recovery"
tags:
  - "lib"
  - "ingest"
  - "v3"
  - "planning"
  - "cursor"
  - "python"
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[ingest-v3-snapshots]]"
  - "[[ingest-v3-gap-record]]"
  - "[[ingest-cursor]]"
  - "[[ingest-v3-finalize]]"
  - "[[runtime-layout]]"
  - "[[ingest-v3-brief]]"
---
# Ingest v3 Run Planning and Run State

## Overview

`hooks/lib/ingest_v3/plan.py` decides what one v3 run ingests. It counts analysis failures, so a range that keeps failing is halved and finally skipped instead of retried forever. It also recovers pages that a dead run left half-written. `hooks/lib/ingest_v3/state.py` holds the per-run state that these decisions and every later stage read and write. Both are pure code: no agent, no cost.

## Run kinds — `plan_run(repo, llake_root, include, base, head)`

`base` is `llake/last-ingest-sha` and `head` is `HEAD`. The function returns a `RunPlan(kind, head)`:

1. If `git diff --name-only base head -- <include>` is empty, the kind is `gap-only` when `gaps.owed_major` finds an open, non-stuck major gap, and `empty` otherwise. Either way the target is HEAD. The failure counter is not consulted.
2. If the failure counter has the same `base` and `count >= 2` (`SPLIT_AFTER_FAILURES`), the target is the counter's `lastHead`, or HEAD when it is missing. `chain = git rev-list --first-parent base..target` lists commits newest first. If git cannot list it (for example, `lastHead` is gone), the chain is recomputed against HEAD. Then:
   - an empty chain gives `range` to HEAD;
   - one commit gives `skip` that commit;
   - more commits give `split` at `chain[len // 2]`.
3. Otherwise the kind is `range` to HEAD.

In a newest-first chain, `chain[len // 2]` is strictly older than `lastHead`. Each further failure therefore halves the range again, until one commit remains and is skipped. A `skip` run records the range under `ranges` in the gap record, with removed-name leads, and advances the cursor without spawning any agent (see [[ingest-v3-finalize]]).

## The failure counter — `.state/ingest-failures.json`

The counter file holds `{"base": <sha>, "count": N, "lastHead": <sha>}`.

- `record_failure(llake, base, head, cls)` counts only class `work`; infra failures leave the file untouched. A failure on the same `base` increments the count, and a new `base` restarts it at 1. `lastHead` is the head that failed.
- The orchestrator calls it when analysis ends in any class other than `none`, so only work failures count. It also calls it, as `work`, when brief assembly raises `InvalidBrief`.
- `clear_failures` deletes the file at the end of finalize, after `finalized` is set. A kill revert can therefore never lose the counter of a run it undoes.

The file lives in gitignored `.state/`, so the counter belongs to one clone.

## Run state — `RunState(project_root, agent_dir)`

Three JSON files in the run's agent dir, loaded on construction and written together by `save()`:

| File | Holds |
|---|---|
| `run.json` (journal) | `kind`, `base`, `head`. `inFlight` maps a page to the snapshot dir it can be restored from. Also `owned` (pages this run snapshotted), `changed` (pages it really changed), `changedFrom` (each changed page's first snapshot), `finalizeWrites`, `logEntry`, `finalized`, `aborted`, and after recovery `recovered` / `unrecovered`. |
| `ledger.json` | `stages` holds one entry per finished agent: stage, kind, class, reason, cost, turns, token classes, first-turn cache read/write, peak context, wall time, kill reason, denial count and denied paths. Also `spent`, `otherStale`, `surface` (out-of-surface actions), `unrestored`, `writeStop` and `fixStop`. |
| `pages.json` | One record per dispatched page: severity, carried, since, attempts, create, bundle, outcome, status, claimsLeft, rejected, changed, verifier findings, unverified, snapshot dirs, fixed, unrestored, note and a `history` list. |

`record_stage(summary, kind)` appends the stage entry and adds its `cost_usd` to `spent`. That figure is the one the run cap reserves against.

## Dead-run recovery — `recover_dead_runs(llake_root, exclude_dir)`

Recovery runs first in every run. It looks at every other agent dir whose `run.json` is neither `finalized` nor `aborted` and still has `inFlight` pages. For each in-flight page under `wiki/` (paths containing `..` are skipped), it reads the snapshot's `manifest.json`:

- `true` and the copy exists: copy the snapshot back.
- `false`: delete the page. Only a page the dead run itself created is ever deleted.
- anything else, such as a missing manifest or a missing copy: leave the page alone, keep it in `inFlight` and list it under `unrecovered`.

A fully recovered run is marked `recovered: true`. The orchestrator logs `recovered the in-flight pages of dead run <id>`. For each run left unrecovered it logs `dead run <id> left unrecovered: <pages> (no snapshot to restore from; needs a human)`.

Recovery handles only pages that were in flight when the run died. Pages a dead run had already settled stay as written, but finalize never ran and the cursor held, so the next run re-ingests the same range over them.

## Key Points

- Run kinds: `empty`, `gap-only`, `range`, `split`, `skip`. With no watched change, the kind is never `split` or `skip`.
- Only `work`-class analysis failures and invalid briefs count. Two on one base halve the range; a one-commit range that fails twice is skipped and recorded.
- The counter is cleared only after a successful finalize.
- `run.json` is the journal both kill revert and dead-run recovery depend on. Do not delete the agent dir of a run that was killed without reverting.
- Recovery restores or deletes a page only on manifest evidence and never guesses.

## Code References

- `hooks/lib/ingest_v3/plan.py:28` — `record_failure`
- `hooks/lib/ingest_v3/plan.py:38` — `clear_failures`
- `hooks/lib/ingest_v3/plan.py:55` — `plan_run`
- `hooks/lib/ingest_v3/plan.py:73` — `recover_dead_runs`
- `hooks/lib/ingest_v3/state.py:7` — `new_journal`, the journal fields
- `hooks/lib/ingest_v3/state.py:12` — `RunState`
- `hooks/lib/ingest_v3/state.py:39` — `record_stage`
- `hooks/lib/ingest_v3/run.py:231` — `_log_unrecovered`
- `tests/lib/test_v3_plan.py` — run kinds, counter, split/skip and recovery

## See Also

- [[ingest-v3-pipeline]] — where planning sits in a run
- [[ingest-v3-snapshots]] — the snapshots recovery restores from
- [[ingest-v3-gap-record]] — where skipped ranges are recorded
- [[ingest-v3-finalize]] — `record_skip` and clearing the counter
- [[ingest-cursor]] — the cursor and clock pair
