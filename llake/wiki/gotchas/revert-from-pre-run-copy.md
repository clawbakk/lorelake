---
title: "Reverting from the pre-run copy destroys concurrent edits"
description: "Restoring a killed run's pages from the pre-run copy wipes edits another writer made during the run — restore each page from its own snapshot"
tags: [gotchas, ingest, v3, snapshots, concurrency]
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-snapshots]]"
  - "[[ingest-v3-finalize]]"
  - "[[session-capture-worker]]"
---
# Reverting from the pre-run copy destroys concurrent edits

## What goes wrong

An ingest v3 run copies `llake/` to `pre/` when it starts, and a run can last up to an hour. During that time other writers keep working. Session capture ([[session-capture-worker]]) does not take the post-merge lock, so it can edit a wiki page or create one while the run is going.

Originally the kill revert restored every in-flight page, every changed page and every file finalize wrote from `pre/`. That broke two things:

- A capture edit that landed between the start of the run and the writer's snapshot of that page was **silently lost** when the run was killed.
- A page that did not exist at `pre/` time, but was created and then touched, was **deleted**.

Finalize had the same flaw. It found updated and created pages by diffing every owned page against `pre/`. An owned page the writer left alone but capture edited was stamped `updated:`, counted as updated in the log, and could have its index row rewritten.

## The fix

Each path is restored from **its own** revert point, the latest copy taken before this run wrote it:

1. `changedFrom`: the snapshot taken just before the run first changed the page. `settle` records it, and a later fixer settle never moves it.
2. The page's `inFlight` bundle snapshot, for a writer killed mid-edit.
3. `finalize-snapshot/`: each file finalize writes, copied just before its first write. This keeps index rows another writer added.
4. `pre/`: only for a path with none of the above.

The usual rules still apply: three-state restore, and no deletion without an explicit manifest `false`.

Finalize now walks only the journal's `changed` pages and compares each with its `changedFrom` snapshot, falling back to `pre/`. It also skips a page that was restored to that snapshot since.

## How to apply

When undoing an agent's work while other writers may be active, restore from the latest snapshot taken before *that agent* wrote, never from a whole-tree copy taken earlier. Decide what a run changed from what the run itself recorded, not by diffing the live tree.

## Key Points

- `pre/` is a fallback, not the revert point.
- `changedFrom` is set once per page and never moved by a fixer.
- Finalize counts only the run's own recorded changes.

## Code References

- `hooks/lib/ingest_v3/snapshots.py:117` — `settle` and `changedFrom`
- `hooks/lib/ingest_v3/snapshots.py:132` — `snapshot_finalize_write`
- `hooks/lib/ingest_v3/snapshots.py:215` — `revert_run`, revert-point order
- `hooks/lib/ingest_v3/finalize.py:201` — `finalize`, changed/created from the journal
- `tests/lib/test_v3_snapshots.py`, `tests/lib/test_v3_finalize.py`

## See Also

- [[ingest-v3-snapshots]] — the snapshot model
- [[ingest-v3-finalize]] — updated/created classification
- [[session-capture-worker]] — the concurrent writer
