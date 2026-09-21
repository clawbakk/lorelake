---
title: "Switch a project to ingest pipeline v2"
description: "How to enable ingest.pipeline v2, verify it actually ran, read its artifacts, and roll back"
tags: [playbook, ingest, configuration, operations]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[ingest-v2-pipeline]]"
  - "[[config-schema]]"
  - "[[debug-hook-failures]]"
  - "[[llake-doctor-skill]]"
---
# Switch a project to ingest pipeline v2

## When to do this

When you want ingest failures to be visible and per-operation rather than silent, when you want the wiki's write surface enforced in code, or when you are evaluating v2 against legacy. The default is still `legacy`. See [[ingest-v2-pipeline]].

## 1. Flip the switch

In the project's `llake/config.json`:

```json
{
  "ingest": {
    "pipeline": "v2"
  }
}
```

The value must be the **literal string `v2`**. `hooks/post-merge.sh` compares against it exactly and treats everything else as legacy — silently. `"V2"`, `"ingest-v2"`, and `true` all leave you on legacy with no error anywhere. This is the single most common way a switch appears not to work.

Everything else is optional: all `ingest.v2.*` keys fall back to `templates/config.default.json`. Do **not** copy the defaults into your config — they are the source of truth and duplicating them means missing future changes.

`ingest.branch`, `ingest.include`, and `ingest.enabled` are still read from the top level. There is no `ingest.v2.include`.

## 2. Verify the config resolves

```bash
python3 <plugin>/hooks/lib/read-config.py llake/config.json ingest.pipeline
python3 <plugin>/hooks/lib/read-config.py llake/config.json ingest.v2.plannerAllowedTools
```

The first must print exactly `v2`. The second must print a JSON array — if it prints nothing, `run_ingest_v2` will refuse to start rather than guess an allowlist. Running `/llake-doctor` also warns on an unrecognised `pipeline` value. See [[llake-doctor-skill]].

## 3. Trigger a run

Merge something into `ingest.branch`, or force a synchronous run for observation:

```bash
LLAKE_POST_MERGE_SYNC=1 <plugin>/hooks/post-merge.sh
```

Sync mode waits for the background subshell instead of disowning it, so you see the run finish. It changes nothing else.

## 4. Confirm it took the v2 path

`llake/.state/hooks.log` should show:

```
... | post-merge    | started → done: spawned v2 agent (range: a1b2c3d..e4f5a6b, timeout: 1200s)
```

**`spawned v2 agent`** is the tell. `spawned agent <id>` without the `v2` means you are still on legacy — go back to step 1 and check the string.

## 5. Read the artifacts

Everything lands in `llake/.state/agents/<agent-id>/`:

| File | What it tells you |
|---|---|
| `agent.log` | Stage banners and the full planner/fixer traces |
| `context/changes.json` | What Stage 1 decided was in scope |
| `context/wiki-index.json` | What the planner believed already existed |
| `plan.json` | What the planner intended |
| `applied.json` | What actually happened, plus `dangling_inline_links` warnings |
| `failed.json` | Per-op failures: `{slug, reason, detail}` |
| `fix-plan.json`, `final-failed.json` | The repair pass, if it ran |

Start with `failed.json`. If it is `[]`, the run was clean. `llake/log.md` also gets an `ingest-failures` block whenever anything remained unfixed.

## Reading common outcomes

| `hooks.log` line | What to do |
|---|---|
| `completed: ... (applied: N)` | Nothing. Clean run. |
| `partial: ... (applied: N, failed: M)` | Read `failed.json`. The cursor advanced; fix the pages by hand or let the next run re-plan. |
| `failed: stage1` | A git or IO error. Check `agent.log`; usually a bad `ingest.include` path or an unreachable SHA. |
| `failed: planner (exit N)` | Budget exhausted or the agent errored. Consider raising `ingest.v2.plannerBudgetUsd`. |
| `failed: applier (non-JSON-plan)` | The planner wrote prose instead of JSON. Cursor held; see [[planner-plan-json-fragility]]. |
| `failed: applier (malformed-JSON)` | Usually a truncated plan — the range is too large for the budget. |
| `failed: applier (schema-invalid)` | The plan parsed but broke the contract. Read the applier's stderr in `agent.log`. |
| `aborted: ... cursor held` | The applier was killed before writing outputs. Check whether the watchdog fired. |
| `skipped: post-merge lock held` | A concurrent run won. Normal; nothing was lost. |

## Tuning

- Planner running out of budget on large merges → raise `ingest.v2.plannerBudgetUsd`, or merge in smaller batches.
- Timeouts → raise `ingest.v2.timeoutSeconds`. Note this is **separate** from `ingest.timeoutSeconds`, which v2 does not read.
- Repeated `AnchorNotFound` / `EditOverlap` in `failed.json` → the planner is writing sloppy anchors; `ingest.v2.maxFixerRetries` of 1 should absorb most of it, and a stronger `plannerModel` helps more than more retries.
- Want failures left for a human instead of a repair agent → set `ingest.v2.maxFixerRetries` to `0`.

## Rolling back

Set `"pipeline": "legacy"`. There is no migration and no state to undo — the two pipelines share `last-ingest-sha` and write the same wiki. Pages created by v2 are ordinary pages.

## Key Points

- The value must be exactly `"v2"`; anything else silently means legacy.
- `spawned v2 agent` in `hooks.log` is the only reliable confirmation.
- Do not copy `ingest.v2.*` defaults into your config — the defaults file is the source of truth.
- `LLAKE_POST_MERGE_SYNC=1` runs the hook synchronously for observation.
- `failed.json` is the first artifact to read; `[]` means a clean run.
- `ingest.v2.timeoutSeconds` is separate from `ingest.timeoutSeconds`.
- Rollback is a one-line config change with no state to migrate.

## Code References

- `hooks/post-merge.sh:85-89` — the exact string comparison
- `hooks/post-merge.sh:170-213` — the v2 dispatch and its `hooks.log` line
- `hooks/lib/ingest-v2.sh:29-50` — which config keys are read
- `hooks/lib/ingest-v2.sh:188-214` — finalize, and which outcomes advance the cursor
- `templates/config.default.json:33-51` — all defaults

## See Also

- [[ingest-v2-pipeline]] — what each stage does
- [[config-schema]] — every `ingest.*` and `ingest.v2.*` key
- [[planner-plan-json-fragility]] — malformed and truncated plans
- [[debug-hook-failures]] — general hook debugging
- [[llake-doctor-skill]] — warns on an unknown `pipeline` value
