---
title: "Ingest v3 Snapshots and Revert"
description: "Pre-run copy, per-bundle page snapshots, three-state restore, write attribution that reverts out-of-surface writes, and the kill revert"
tags:
  - "lib"
  - "ingest"
  - "v3"
  - "snapshots"
  - "revert"
  - "safety"
  - "python"
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[ingest-v3-writers]]"
  - "[[ingest-v3-finalize]]"
  - "[[ingest-v3-run-planning]]"
  - "[[ingest-v3-orchestrator]]"
  - "[[revert-from-pre-run-copy]]"
---
# Ingest v3 Snapshots and Revert

## Overview

`hooks/lib/ingest_v3/snapshots.py` makes every v3 write undoable. It keeps a pre-run copy of `llake/` and a snapshot of each page before an agent may touch it. It also attributes every agent write to a stage, and its kill revert undoes a run that did not finish. A rule runs through the whole module: **only paths under `llake/` are ever restored or deleted, and a file is restored or deleted only on evidence.** A v3 write outside `llake/` is reported and never touched.

## The pre-run copy — `take_pre`

The orchestrator calls `take_pre` before the run's first write under `llake/`, including for `skip` runs. It copies `llake/`, without `.state/`, to `<agent dir>/pre/`. It then writes `pre-manifest.json`, mapping each file to its SHA-1. The hashes come from the copy, not the live tree, so a file created after the copy cannot pass for pre-run state.

`pre/` serves as a fallback, not as the main revert point. Write attribution uses it, as does any path that has no closer snapshot.

## Page snapshots — `snapshot(state, pages, dest)`

Before a writer, retry or fixer starts, its pages are copied into its snapshot dir: `bundles/bNN/snapshot`, `retryK` or `fix-snapshot`. `manifest.json` records `true` for each page that existed and `false` for each that did not. Two snapshots into one dir merge their manifests. Each page enters the journal's `inFlight` (page → snapshot dir) and `owned` lists.

## Three-state restore — `restore_page`

| Manifest value | Result |
|---|---|
| `true` and the copy exists | page restored → `restored` |
| `false` (exactly) | page deleted → `deleted` |
| missing manifest, missing entry, or missing copy | page left alone → `unrestored` |

`_restore_llake` refuses any target outside `llake/` with `ValueError`. A source that is named but missing does not count as evidence that the page was absent, so the page stays as it is.

## Settle and revert

- `settle(state, pages)` runs after a successful writer or fixer and keeps its edits. Each page leaves `inFlight`. If it changed since its snapshot, it is added to `changed`, and `changedFrom` records that snapshot as the page's revert point. Only the **first** such snapshot counts: a later fixer settle never moves it.
- `revert(state, pages, dest)` runs after a failed writer or fixer. It restores each page from the job's snapshot. Pages that cannot be restored stay in `inFlight` for the kill and recovery paths. The dispatcher marks them `unrestored`, leaves them as written and never retries them.
- `changed_since` and `page_diff` compare a page with its snapshot. The verifier uses `page_diff`.

## Write attribution — `out_of_surface` and `revert_out_of_surface`

After each agent, its `Edit`/`Write` tool calls from the stream are compared with what it was allowed to write. A call is ignored when it targets:

- the job's own pages, or any page the run owns;
- the allowed directory (`brief/` for analysis, `brief/pages/` for recall);
- a path the CLI denied. A denied attempt wrote nothing.

Everything else is out of surface:

- **Under `llake/`.** A file inside `.state/`, or any file when there is no pre-manifest, is only `reported`. A file whose hash matches the pre-manifest is skipped. Otherwise the file is restored from `pre/`, or deleted if `pre/` did not have it, and recorded as `reverted` (or `reported` if that fails).
- **Outside `llake/`.** Recorded as `reported-outside-llake` and never touched.

Actions go to the ledger's `surface` list. They appear as notes in the log entry and as `report.md` lines; a stream-attributed write outside `llake/` is a FAIL.

## Finalize snapshot — `snapshot_finalize_write`

Before finalize first writes any file, such as an index, the gap record or the cursor, it copies that file as it stands into `finalize-snapshot/` (manifest `false` when absent). Only the first copy of a file counts. If another writer added a row to an index during the run, a kill revert restores the index with that row instead of the older pre-run version.

## Kill revert — `revert_run(project_root, agent_dir)`

The kill trap's `ingest-v3.py revert-run` and Python's own kill handler both call this:

1. Skip when the journal is `finalized` (the run stands) or already `aborted`.
2. Collect the paths to revert: `inFlight` + `changed` + `finalizeWrites`, minus `log.md`.
3. Restore each path from its own earliest revert point: `changedFrom`, then its `inFlight` snapshot, then `finalize-snapshot`. `pre/` is used only for a path with none of these. Without a pre-manifest such a path is left unrestored.
4. Remove the run's log entry from `log.md` by exact text match on the journal's `logEntry`. The rest of `log.md` is never rewritten.
5. Set `aborted` only when nothing stayed unrestored. Otherwise the run stays open, so the next run's dead-run recovery can retry it (see [[ingest-v3-run-planning]]).

The function returns `{skipped, restored, unrestored}` (or `{skipped, reason}`). Calling it twice is harmless.

Why each path has its own revert point instead of `pre/`: see [[revert-from-pre-run-copy]].

## Key Points

- Nothing outside `llake/` is ever restored or deleted. Out-of-surface writes outside it are reported.
- Restore is three-state, and deletion needs an explicit `false` in a manifest.
- A changed page reverts to the snapshot taken just before the run first changed it, so concurrent edits from capture survive a kill.
- Denied tool calls do not count as writes.
- `revert_run` does nothing on a finalized run and can safely run twice.

## Code References

- `hooks/lib/ingest_v3/snapshots.py:32` — `take_pre`
- `hooks/lib/ingest_v3/snapshots.py:73` — `snapshot`
- `hooks/lib/ingest_v3/snapshots.py:92` — `restore_page`
- `hooks/lib/ingest_v3/snapshots.py:117` — `settle`
- `hooks/lib/ingest_v3/snapshots.py:132` — `snapshot_finalize_write`
- `hooks/lib/ingest_v3/snapshots.py:163` — `out_of_surface`
- `hooks/lib/ingest_v3/snapshots.py:190` — `revert_out_of_surface`
- `hooks/lib/ingest_v3/snapshots.py:215` — `revert_run`
- `tests/lib/test_v3_snapshots.py` — restore states, attribution, fail-closed reverts, per-page revert points

## See Also

- [[ingest-v3-writers]] — when snapshots are taken and settled
- [[ingest-v3-finalize]] — finalize writes and their snapshot
- [[ingest-v3-orchestrator]] — the kill path that calls `revert_run`
- [[revert-from-pre-run-copy]] — the bug the per-page revert points fix
