---
title: "Ingest v3 Finalize and Run Report"
description: "The code-owned end of a v3 run — updated dates, category index rows, gap record, log entry, cursor, skip record — and report.md run checks"
tags:
  - "lib"
  - "ingest"
  - "v3"
  - "finalize"
  - "cursor"
  - "logging"
  - "python"
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[ingest-v3-gap-record]]"
  - "[[ingest-v3-snapshots]]"
  - "[[ingest-cursor]]"
  - "[[ingest-v3-run-planning]]"
  - "[[revert-from-pre-run-copy]]"
  - "[[post-merge-hook]]"
  - "[[runtime-layout]]"
---
# Ingest v3 Finalize and Run Report

## Overview

In v3, agents edit page content and code makes every bookkeeping write. `hooks/lib/ingest_v3/finalize.py` does those writes at the end of a run. `hooks/lib/ingest_v3/report.py` then writes `report.md` for review. Finalize touches only four kinds of file, all under `llake/`:

- the run's own pages;
- the indexes of their categories;
- the gap record;
- `log.md` and the cursor/clock.

It never edits the root `index.md`.

## Write order

1. `updated:` on the pages the run changed or created.
2. Category indexes.
3. Gap record `llake/ingest-gaps.json`.
4. Log entry appended to `llake/log.md`.
5. Cursor `last-ingest-sha` and clock `.state/last-ingest-at`.
6. Journal `finalized = true`, `inFlight` cleared.
7. Failure counter `.state/ingest-failures.json` deleted.

Each file is added to the journal's `finalizeWrites` and snapshotted (see [[ingest-v3-snapshots]]) before it is written. The log text is saved as `logEntry`, so a kill can undo every step. `finalized` is set only after the cursor write. A crash anywhere earlier leaves the cursor on the old base, and the next run redoes the range.

## Updated vs created

Finalize walks the journal's `changed` pages. It does not diff every page against `pre/`. For each page it compares with that page's `changedFrom` snapshot, falling back to `pre/`:

- the page existed in the snapshot → **updated**;
- it did not → **created**;
- its text equals the snapshot again (for example, a check undid a broken write) → skipped.

`set_updated(text, today)` sets `updated:`, inserting it after `created:` when absent. It keeps CRLF endings and leaves a page without a closed frontmatter block unchanged. A page the run owned but did not change gets no `updated:` stamp, even if another writer edited it during the run.

## Category indexes — `rebuild_indexes`

For each category directory with an updated or created page, if `<cat>/<cat>.md` exists:

- Rows of the form `| [[slug]] | description |` for **updated** pages are rewritten, but only when the page's `description:` changed compared with its revert point.
- Rows for **created** pages are inserted after the last existing row, or appended when the index has no rows yet.
- When the category gained pages, the first `N page(s)` phrase is recounted.
- The index's own `updated:` is set.

Index prose is never edited. A false blurb becomes a `blurb` gap.

## The gap record

`outcomes(state, brief, checks)` reduces each run page to one outcome. Two run-level facts take priority over the writer's outcome:

- A page left `unrestored` after a failed write is owed as `reverted`.
- A page whose fixer did not succeed is owed as `flagged`, with every finding it received: the first checks pass, the verifier and anything still flagged.

`gaps.next_record` then turns the outcomes, the writers' `otherStale` reports and the brief's blurbs into the new record (see [[ingest-v3-gap-record]]).

## Log entry — `log_entry`

```
## [YYYY-MM-DD] ingest | <base7>..<head7>: v3 — N updated, M created, G gaps (K major)

Agent `<id>`, $X.XX list estimate. Themes: T1 <title>; T2 <title>.

Pages affected: [[a]] (updated), [[b]] (created), [[<cat>]]

Gaps (every claim is in `llake/ingest-gaps.json`):
- [[page]] — major, cause: flagged: "<quote>" → <head> (+1 more) — needs a human

Carried gaps resolved: [[c]]

Note: <dropped new page, out-of-surface write, or gap-record note>
```

A gap-only run uses `gap-only at <head7>` as its range and the sentence `No new range: carried gaps only.`. The text after the arrow is the claim's `head` string. `— needs a human` marks stuck gaps.

## Cursor — `write_cursor`

`write_cursor` writes `last-ingest-sha` and `.state/last-ingest-at` together, the same way `advance_ingest_cursor` does in shell (see [[ingest-cursor]]). An `empty` run calls it directly without finalize.

## Skip record — `record_skip`

For a `skip` run (a one-commit range that failed analysis twice), `record_skip` appends `{base, head, cause: "analysis-failed", leads}` to `ranges` and keeps the existing gaps. The leads are the wiki pages that name a removed name, with their lines, or a placeholder line when there are none. It appends a log entry `v3 — skipped: analysis failed twice` listing up to 20 leads, then advances the cursor past the commit.

## `report.md` — `write_report`

The report opens with a header line giving run kind, range, list-price cost and wall time. Three sections follow.

**Run checks**:

| Level | Check |
|---|---|
| PASS/FAIL | gap record valid, every claim's quote on its page |
| PASS/FAIL | a page that ended corrected or no-change carries only a `flagged` or `unverified` gap |
| PASS/FAIL | every major brief page corrected or a gap; WARN for major pages that ended no-change |
| PASS / WARN | no unrestored pages / one WARN per unrestored page |
| PASS/FAIL | spend within `maxRunBudgetUsd`; WARN when `analysisBudgetUsd` alone exceeds it |
| PASS/FAIL | wall time within `timeoutSeconds` |
| PASS/WARN | every stage within its own budget |
| INFO | failed stages, each classified infra or work |
| INFO | cursor move and run kind |
| PASS/FAIL | no permission denials on owned pages |
| PASS/FAIL | no stream-attributed writes outside `llake/` |
| WARN | `git status` changes outside `llake/` during the run, not attributed to v3 (for example, the user's own edits) |
| WARN | out-of-surface writes reverted under `llake/` |
| INFO | files changed during the run by another writer (not reverted) |

**Stages**: one table row per agent, with class, cost, turns, input/output/cache tokens, first-turn cache read/write, peak context, wall time and denials.

**Pages**: one table row per page, with severity, bundle, outcome and history.

If writing the report fails after finalize, the failure is logged and the run still exits 0.

## Key Points

- Write order: pages → indexes → gap record → log → cursor → `finalized` → clear the failure counter. Each write is journaled and snapshotted first.
- Updated and created pages come from the run's own `changed` journal, compared with each page's first snapshot. Concurrent edits by capture are neither counted nor stamped.
- Index rows are rebuilt from page descriptions. Index prose and the root index are never touched.
- A crash before the cursor write means the next run redoes the range.
- `report.md` is the shakedown checklist. A FAIL line there is worth reading before trusting the run.

## Code References

- `hooks/lib/ingest_v3/finalize.py:23` — `set_updated`
- `hooks/lib/ingest_v3/finalize.py:42` — `write_cursor`
- `hooks/lib/ingest_v3/finalize.py:53` — `rebuild_indexes`
- `hooks/lib/ingest_v3/finalize.py:109` — `outcomes`
- `hooks/lib/ingest_v3/finalize.py:134` — `log_entry`
- `hooks/lib/ingest_v3/finalize.py:180` — `_advance`
- `hooks/lib/ingest_v3/finalize.py:201` — `finalize`
- `hooks/lib/ingest_v3/finalize.py:250` — `record_skip`
- `hooks/lib/ingest_v3/report.py:18` — `run_check_lines`
- `hooks/lib/ingest_v3/report.py:92` — `write_report`
- `tests/lib/test_v3_finalize.py`, `tests/lib/test_v3_report.py`

## See Also

- [[ingest-v3-gap-record]] — `next_record` and the record format
- [[ingest-v3-snapshots]] — `finalize-snapshot` and the kill revert
- [[ingest-cursor]] — the shell cursor helper `write_cursor` mirrors
- [[revert-from-pre-run-copy]] — why finalize no longer diffs against `pre/`
