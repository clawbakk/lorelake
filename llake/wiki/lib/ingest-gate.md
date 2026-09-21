---
title: "ingest_gate.py — Ingest Batching Gate"
description: "Decides on every post-merge whether ingest runs now, waits for more churn/time, or skips an empty pile — shared by the legacy and v2 pipelines"
tags:
  - "lib"
  - "ingest"
  - "post-merge"
  - "cost"
  - "batching"
  - "python"
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[post-merge-hook]]"
  - "[[ingest-cursor]]"
  - "[[config-schema]]"
  - "[[post-merge-lock]]"
  - "[[hook-log]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[ingest-v2-pipeline]]"
  - "[[runtime-layout]]"
  - "[[adr-001-post-merge-trigger]]"
---
# ingest_gate.py — Ingest Batching Gate

## Overview

`hooks/lib/ingest_gate.py` runs on every git `post-merge` on the monitored branch. It decides whether ingest should **run now**, **wait** for more changes to pile up, or **skip** because nothing relevant changed. `hooks/post-merge.sh` calls it after the SHA cursor checks and **before either pipeline (legacy or v2) spawns**, so both pipelines share one decision.

The gate exists to bound ingest cost. Before it, every qualifying merge spawned a full ingest agent (by default an `opus` run with a multi-dollar budget cap). A busy branch with many small merges paid for one full agent run per merge. The v2 pipeline was worse: it had no relevance pre-flight at all and spawned even when nothing under `ingest.include` changed. The gate batches small merges into one larger run by holding the cursor back until enough churn or enough time has accumulated.

It is pure-stdlib Python. It keeps no state of its own and only reads `.state/last-ingest-at`.

## How the pile is measured

```
git -C <project> diff --numstat <last-sha>..<current-sha> -- <include paths...>
```

The pile is the **net range diff**, not a sum of per-commit diffs. A function rewritten five times across the range counts once, and code added then reverted counts as zero. `summarize()` turns the numstat output into `(files, lines)`, where `lines` = added + deleted. Binary files print `-` in both numeric columns. They add to `files` but not to `lines`, so a binary-only change is never mistaken for an empty pile.

When `ingest.include` is empty, no pathspec is passed and changes anywhere in the repo count.

## The age arm

`age_hours()` reads `<project>/llake/.state/last-ingest-at` (epoch seconds) and returns the hours elapsed since then. It returns `None` when the file is missing or unparseable. Only the [[ingest-cursor]] helpers write that file: `advance_ingest_cursor` stamps it whenever the SHA cursor moves, and `ensure_ingest_clock` seeds it when it is absent.

## Decision table

`decide()` evaluates in this order, and the first match wins:

| # | Condition | Verdict | Detail suffix |
|---|---|---|---|
| 1 | `files == 0` | `EMPTY` | none |
| 2 | `LLAKE_IGNORE_SCHEDULE=1` in the environment | `RUN` | `reason=forced` |
| 3 | `ingest.schedule.enabled` is `false` | `RUN` | `reason=schedule-disabled` |
| 4 | clock unreadable (`age_h` is `None`) | `RUN` | `reason=no-timestamp` |
| 5 | `lines >= minChangedLines` | `RUN` | `reason=lines` |
| 6 | `age_h >= maxAgeHours` | `RUN` | `reason=age` |
| 7 | otherwise | `WAIT` | `need_lines=<min> need_age_h=<max>` |

The ordering has two consequences:

- The empty-pile check beats everything. **Neither `enabled: false` nor the force variable ever spawns an agent for a range with no relevant changes.**
- The two batching arms are OR'd. Ingest runs when *either* one trips.

## Output and exit codes

stdout is exactly one line: `<VERDICT> lines=<n> files=<n> age_h=<h.h|none>[ <suffix>]`. For example:

```
WAIT lines=42 files=3 age_h=2.5 need_lines=1500 need_age_h=24
RUN lines=1830 files=12 age_h=3.1 reason=lines
EMPTY lines=0 files=0 age_h=5.0
```

`_trim()` renders `24.0` as `24` in `need_age_h` so log lines read naturally. A `git diff` failure prints `ingest_gate: git diff --numstat: <stderr>` to stderr and exits 2. Argparse errors, such as a non-numeric threshold, also exit 2.

## CLI

```
python3 hooks/lib/ingest_gate.py \
  --project-root <dir> --last-sha <sha> --current-sha <sha> \
  --state-dir <project>/llake/.state \
  --schedule-enabled <true|false> --min-changed-lines <int> --max-age-hours <float> \
  [--include <path> ...]
```

The three schedule flags are **required and have no defaults**, by design. Their defaults live only in `templates/config.default.json` (`ingest.schedule.*`) and reach the script via `read-config.py` in the caller. That follows the config fallback contract: duplicating defaults in the script would create a second source of truth. `--schedule-enabled` counts as enabled unless its value is literally `false` (case-insensitive).

## Integration in post-merge.sh

The gate block sits between the commit-range validation and the v2 hand-off (`hooks/post-merge.sh:169-238`):

1. **Seed the clock.** `ensure_ingest_clock` seeds `.state/last-ingest-at` if it is missing. `.state/` is gitignored, so every fresh clone lacks it. Without seeding, the missing clock would read as overdue (`reason=no-timestamp`) and force a full ingest on the first merge in every clone on every machine. Seeding starts that clone's own window instead.
2. **Read the config.** It reads `ingest.schedule.enabled`, `minChangedLines`, and `maxAgeHours` through `read-config.py`.
3. **Run the gate.** It builds `--include` args from `INCLUDE_PATHS` and runs the gate with stderr captured to `.state/gate-stderr.tmp`.
4. **Fail open.** If the gate exits nonzero, its stderr is sanitized through `render_err_summary` (see [[hook-log]]) and the verdict becomes `RUN reason=gate-error`. A broken gate must never silently stop ingest forever. The sanitized summary is appended only to the log copy (`GATE_LOG_DETAIL`), so `GATE_DETAIL` stays exactly `reason=gate-error` as a stable contract. Raw tracebacks would otherwise glue onto the unterminated `started` line in `hooks.log`.
5. **Dispatch on the verdict:**
   - **EMPTY**: take the [[post-merge-lock]] synchronously, call `advance_ingest_cursor` (SHA + clock), release the lock, and log `skipped: no relevant file changes (<range>)`. If the lock is busy, an agent from an earlier merge may still be working and will write the cursor back later. In that case the hook does **not** advance. It logs `skipped: no relevant file changes, lock held — cursor not advanced (<range>)`, and the next merge retries the wider range. Advancing without the lock could clobber the in-flight agent's cursor write and lose a range.
   - **WAIT**: log `deferred: <detail> (<range>)` and exit 0. The cursor is **held** and no lock is taken.
   - **RUN**: fall through to the v2 or legacy spawn. The spawn line in `hooks.log` gains `gate: <detail>`.

EMPTY also resets the clock. `last-ingest-at` means "the wiki is known-current as of T", and after an empty-pile skip that is true.

## The cursor is the queue

A `WAIT` records nothing, and there is no pending-work file. Because `last-ingest-sha` is not advanced, the next merge computes `last..HEAD` over a wider range and re-measures the whole accumulated pile. The run that eventually happens ingests everything since the last successful ingest in one agent.

## Forcing a run and tuning

- **Manual flush:** set `LLAKE_IGNORE_SCHEDULE=1` in the hook's environment. The hook still needs `HEAD` on `ingest.branch` and ahead of the cursor. The usual approach is to run the installed post-merge hook by hand from the project root, e.g. `LLAKE_IGNORE_SCHEDULE=1 .git/hooks/post-merge`. Any value other than `1` is ignored.
- **Disable batching:** set `ingest.schedule.enabled: false`. Every non-empty merge then runs, as it did before the gate. The empty-pile skip stays on.
- **Tune:** lower `minChangedLines` / `maxAgeHours` for a fresher wiki, or raise them to batch harder. See [[config-schema]].

## Constraints and gotchas

- **The age arm is not a timer.** It is only evaluated when a merge fires the hook. A small pile deferred on Monday stays pending until the *next* merge after `maxAgeHours` elapses. If nobody merges, nothing runs. This matches [[adr-001-post-merge-trigger]], which rejected cron polling.
- A fresh clone seeds its clock and then **defers** a small first merge instead of running it. `test_missing_clock_seeds_and_defers` pins this.
- The hook seeds a missing clock before running the gate. So `reason=no-timestamp` is only reachable when the clock file exists but is unreadable or corrupt.
- A misconfigured threshold (e.g. `"maxAgeHours": "soon"`) makes argparse exit 2. **Every** merge then fails open to `RUN reason=gate-error`, which effectively turns batching off. The symptom is `gate: reason=gate-error err=...` on every spawn line in `hooks.log`.
- Failed or killed ingest runs hold both the cursor and the clock. The next merge's age arm is measured from the last *successful* ingest, so it usually trips immediately.

## Key Points

- Runs on every post-merge, before either pipeline. The verdicts are `EMPTY`, `WAIT`, and `RUN`.
- The pile is the net `git diff --numstat last..HEAD -- <include>`. Binary files count as files, not lines.
- Ingest runs when either the lines arm (`minChangedLines`, default 1500) or the age arm (`maxAgeHours`, default 24) trips.
- `WAIT` holds the cursor (the cursor is the queue). `EMPTY` advances the cursor and clock under the post-merge lock.
- `enabled: false` and `LLAKE_IGNORE_SCHEDULE=1` bypass batching but never the empty-pile skip.
- The gate fails open: any gate error becomes `RUN reason=gate-error`.
- The script has no defaults. Thresholds come from `config.default.json` via `read-config.py`.
- The age arm only fires on a merge. It is not a scheduler.

## Code References

- `hooks/lib/ingest_gate.py:26` — `TIMESTAMP_FILENAME = "last-ingest-at"`
- `hooks/lib/ingest_gate.py:29-39` — `git_numstat`, which raises `RuntimeError` on git failure
- `hooks/lib/ingest_gate.py:42-62` — `summarize` and its binary-file handling
- `hooks/lib/ingest_gate.py:65-81` — `age_hours`
- `hooks/lib/ingest_gate.py:89-105` — `decide`, the verdict table
- `hooks/lib/ingest_gate.py:115-144` — CLI, `LLAKE_IGNORE_SCHEDULE`, exit 2 on git failure
- `hooks/post-merge.sh:169-217` — clock seeding, config reads, gate invocation, fail-open
- `hooks/post-merge.sh:219-233` — `EMPTY` branch under the post-merge lock
- `hooks/post-merge.sh:235-238` — `WAIT` branch
- `templates/config.default.json` — `ingest.schedule` defaults
- `tests/lib/test_ingest_gate.py` — unit tests for the numstat summary, age, and verdict logic
- `tests/hooks/test_post_merge_gate.sh` — 12 integration scenarios: defer, lines arm, age arm, empty pile, forced, disabled, disabled-but-empty, fresh clone, v2 empty/defer/success, fail-open

## See Also

- [[post-merge-hook]] — the caller and the pipeline dispatch that follows a `RUN`
- [[ingest-cursor]] — `advance_ingest_cursor` / `ensure_ingest_clock`
- [[config-schema]] — the `ingest.schedule.*` keys
- [[post-merge-lock]] — the lock the `EMPTY` branch takes
- [[hook-log]] — `render_err_summary` used for fail-open reporting
- [[ingest-v2-orchestrator]] — only reached after a `RUN`
- [[runtime-layout]] — where `last-ingest-at` lives
- [[adr-001-post-merge-trigger]] — why ingest is merge-triggered, not scheduled
