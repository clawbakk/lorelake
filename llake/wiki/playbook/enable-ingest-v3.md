---
title: "Switch a project to ingest pipeline v3"
description: "How to switch a project to ingest.pipeline v3, confirm it ran, read a run's report, handle held cursors and stuck gaps, and roll back"
tags: [playbook, ingest, v3, configuration, operations]
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[config-schema]]"
  - "[[ingest-v3-gap-record]]"
  - "[[ingest-gate]]"
  - "[[llake-doctor-skill]]"
  - "[[debug-hook-failures]]"
  - "[[enable-ingest-v2]]"
---
# Switch a project to ingest pipeline v3

## When to use this

Use this guide when a project should move from the legacy or v2 ingest to v3 ([[ingest-v3-pipeline]]), when you need to confirm v3 really ran, or when a v3 run held its cursor or left gaps.

## Before you switch

- Run `/llake-doctor`. It accepts `"v3"` as a pipeline value and validates any existing gap record (Check 8.6).
- Check that `ingest.include` lists the project's real source directories. v3 scopes everything to these paths.
- Check that `llake/last-ingest-sha` exists.
- Check the cost. By default one run may spend up to `maxRunBudgetUsd` ($40). Analysis alone can spend `analysisBudgetUsd` ($10), and the run cap does not limit analysis.

## Switch

1. In `llake/config.json`, set `"pipeline": "v3"` inside the existing `ingest` object. Only lowercase `v3` works. Any other spelling silently runs legacy.
2. Optional: override individual `ingest.v3.*` keys, for example a lower `maxRunBudgetUsd` or `"verifierMode": "accuracy"`. Override only what you change, and never copy the defaults into `config.json`. See [[config-schema]].
3. Commit `config.json`. After each run, also commit `llake/ingest-gaps.json` along with the wiki changes; it is a tracked file.

## Trigger a run

- Merge into `ingest.branch` as usual. The batching gate still applies (see [[ingest-gate]]).
- To run now, flush from the project root with `LLAKE_IGNORE_SCHEDULE=1 .git/hooks/post-merge`. Add `LLAKE_POST_MERGE_SYNC=1` to wait for the run in the foreground.

## Confirm it ran

- In `llake/.state/hooks.log`, look for `done: spawned v3 agent (...)` followed later by `agent-done | completed: agent <id> v3 <kind> (...)`. A plain `spawned agent` line means the legacy path ran, so the pipeline value is wrong.
- `llake/log.md` should have a new entry `## [date] ingest | <base>..<head>: v3 — ...`.
- Open `llake/.state/agents/<id>/report.md` and read the **Run checks** section first. Every FAIL line is worth investigating.

## Read a run

| Question | Look at |
|---|---|
| What did each stage cost and how did it end? | `report.md` Stages table; `ledger.json` |
| What happened to each page? | `report.md` Pages table; `pages.json` (`history`) |
| Which pages did analysis pick, and why? | `brief/pages/*.json`, `brief/considered.json`, `brief/files.json`, `brief.json` |
| Were brief entries dropped or added automatically? | `brief-report.json` (warnings, auto-added, unaccounted, dropped) |
| What did an agent see and do? | `stages/<stage>.prompt.md`, `stages/<stage>.jsonl`, `<stage>.summary.json` |
| What did checks revert or flag? | `checks.write.json`, `checks.fix.json` |
| Which settings applied? | `run-config.json` |
| What is still owed? | `llake/ingest-gaps.json`, or `python3 <plugin>/hooks/lib/ingest-v3.py validate-gaps --llake-root llake` |

## When the cursor holds

| `hooks.log` reason | Meaning | What to do |
|---|---|---|
| `held: ... (analysis work failure: ...)` | analysis failed for a work reason | Nothing at first. The next run retries. After two failures on one base it ingests the first half of the range, and a single failing commit is eventually skipped and recorded under `ranges`. |
| `held: ... (analysis infra failure: ...)` | rate limit, overload, auth or network | Not counted toward splitting. The next merge retries. |
| `held: ... (invalid brief: ...)` | analysis wrote JSON that failed validation | Read `brief-report.json`. This counts as a work failure. |
| `held: ... (internal error: ...)` | an uncaught exception | Read the traceback in `agent.log`. Common causes: a missing `ingest.v3` key, `ingest.include` not a list, an invalid `writeMode` or `verifierMode`. |

## Handle gaps

- **Open gaps** need nothing from you; the next run works on them first. An open major gap makes every v3 merge run ingest, even one the schedule would defer.
- **`STUCK:` gaps** (three failed attempts) are never retried. Fix the page by hand, delete that entry from `llake/ingest-gaps.json` (keep the JSON valid), and commit both.
- **`RANGE:` entries** are commits that were never ingested. Use the listed leads to update the affected pages by hand, then delete the entry.
- **`INVALID` with `page ... does not exist` or `quote not on the page`** after hand edits is expected and fixes itself on the next v3 run.

## After a kill or timeout

- The run reverts its own writes. `agent.log` shows `killed by SIG...: N agent(s) stopped, run reverted (...)`, and the cursor holds.
- If `agent.log` says `revert-run failed`, the next run's dead-run recovery retries the revert. Keep that agent dir.
- If a later `agent.log` says `dead run <id> left unrecovered: ... needs a human`, restore those pages from git.

## Roll back

Set `ingest.pipeline` back to `"v2"` or `"legacy"`, or remove the key. Legacy and v2 ignore `llake/ingest-gaps.json`. If you might return to v3 much later, delete the file, so the next v3 run does not rework stale debt.

## Key Points

- Only the literal `"v3"` selects v3. Confirm a run by the `spawned v3 agent` and `completed: agent ... v3` lines.
- `report.md` Run checks are the first thing to read after a run.
- Held cursors fix themselves for analysis failures (retry, split, skip). Internal errors usually mean config.
- Stuck gaps and skipped ranges are the only things that need manual cleanup.

## Code References

- `templates/config.default.json` — `ingest.v3` defaults
- `hooks/post-merge.sh:84-95` — pipeline switch
- `hooks/lib/ingest_v3/report.py:18` — the Run checks
- `hooks/lib/ingest_v3/cli.py:14` — `validate-gaps`

## See Also

- [[ingest-v3-pipeline]] — what v3 does
- [[ingest-v3-gap-record]] — the record of owed pages
- [[config-schema]] — every `ingest.v3.*` key
- [[debug-hook-failures]] — general hook debugging
- [[enable-ingest-v2]] — the equivalent guide for v2
