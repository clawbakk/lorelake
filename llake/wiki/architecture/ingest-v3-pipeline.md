---
title: "Ingest Pipeline v3"
description: "Staged ingest with $0 leads, an analysis agent that writes a brief, a recall pass, parallel per-page writers, $0 checks, a fix round, and code-owned finalize with a gap record"
tags:
  - "architecture"
  - "ingest"
  - "pipeline"
  - "post-merge"
  - "agents"
  - "v3"
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[three-writer-model]]"
  - "[[ingest-v2-pipeline]]"
  - "[[post-merge-hook]]"
  - "[[ingest-v3-orchestrator]]"
  - "[[ingest-v3-run-planning]]"
  - "[[ingest-v3-brief]]"
  - "[[ingest-v3-writers]]"
  - "[[ingest-v3-snapshots]]"
  - "[[ingest-v3-finalize]]"
  - "[[ingest-v3-gap-record]]"
  - "[[ingest-v3-templates]]"
  - "[[enable-ingest-v3]]"
  - "[[config-schema]]"
  - "[[runtime-layout]]"
  - "[[claude-p-tools-flag]]"
---
# Ingest Pipeline v3

## Overview

Ingest v3 is the third implementation of the ingest writer (see [[three-writer-model]]), selected with `ingest.pipeline: "v3"` in `llake/config.json`. It replaces the legacy single agent and the v2 planner/applier ([[ingest-v2-pipeline]]) with a staged pipeline. A Python orchestrator in `hooks/lib/ingest_v3/` drives it. `hooks/post-merge.sh` starts it through `hooks/lib/ingest-v3.sh`, which runs `python3 hooks/lib/ingest-v3.py run` (see [[ingest-v3-orchestrator]]). Legacy and v2 still exist unchanged; v3 runs only when selected.

The design splits the job in two:

- **Finding** what is stale is the job of one read-only analysis agent, plus an optional recall agent. They write a JSON *brief*: which pages the range made stale, the exact stale sentences quoted from each page, and where each new fact belongs.
- **Writing** is done by several writer agents in parallel. Each owns a bundle of at most `bundleMaxPages` pages and can edit only those pages.

Every other write a wiki update needs is done by code, never by an agent: `updated:` dates, category index rows, the `log.md` entry, the gap record and the cursor. Pages a run cannot bring current are not silently lost. They go into the **gap record**, `llake/ingest-gaps.json`, and the next run works on them first (see [[ingest-v3-gap-record]]).

## Stages of one run

`run._run` in `hooks/lib/ingest_v3/run.py` drives these stages in order:

| # | Stage | Done by | Output |
|---|---|---|---|
| 1 | Recover dead runs | code | restores pages an earlier run left mid-write ([[ingest-v3-run-planning]]) |
| 2 | Run planning | code | run kind and target head |
| 3 | Pre-run copy | code | `pre/` copy of `llake/` (without `.state/`) and `pre-manifest.json` ([[ingest-v3-snapshots]]) |
| 4 | Inputs | code, $0 | removed/added names, hit index, anchor index, literal index, patches, commits, catalog ([[ingest-v3-brief]]) |
| 5 | Analysis | 1 agent | `brief/themes.json`, `brief/pages/batch-N.json`, `considered.json`, `files.json`, `notes.json` |
| 6 | Recall | 1 agent, optional | extra `brief/pages/recall-*.json` entries for pages analysis missed |
| 7 | Brief assembly | code | validated `brief.json`, with sweep pages and carried gaps merged in |
| 8 | Bundling | code | `bundles.json`: groups of at most `bundleMaxPages` pages, major first |
| 9 | Writers | N agents in parallel, plus optional verifiers | page edits and a structured status per page ([[ingest-v3-writers]]) |
| 10 | Checks | code, $0 | `checks.json`: reverts, unlinks and flags |
| 11 | Fix round | one fixer per flagged bundle | second pass on flagged pages, then checks again |
| 12 | Finalize | code | `updated:`, category indexes, gap record, log entry, cursor ([[ingest-v3-finalize]]) |
| 13 | Report | code | `report.md` with run checks, per-stage cost and page outcomes |

## Run kinds

`plan.plan_run` picks one kind per run:

| Kind | When | What runs | Cursor |
|---|---|---|---|
| `empty` | no watched change in `base..HEAD` and no open major gap | nothing | advances to HEAD |
| `gap-only` | no watched change, but an open, non-stuck major gap is owed | writers on carried major gaps only; analysis and recall skipped | advances to HEAD |
| `range` | normal case | every stage | advances to HEAD at finalize |
| `split` | analysis failed twice (class `work`) on this base | every stage, on the first half of the failed range | advances to the split midpoint |
| `skip` | a one-commit range failed analysis twice | no agents; the range is recorded under `ranges` in the gap record | advances past the commit |

A split midpoint can come before every watched change. The run then skips analysis and recall, writes a placeholder theme `T0`, and still dispatches writers for carried gaps.

## When the cursor moves

- **Advances** whenever finalize completes, even if pages became gaps. Writer failures, the run budget cap and the run deadline do not hold the cursor. The pages they leave become gaps with cause `writer-failed`, `run-cap` or `timeout`.
- **Held** on an analysis failure, an invalid brief, any internal error (an uncaught exception), a kill before finalize, or a benchmark `LLAKE_V3_STOP_AFTER` stop. Analysis failures and invalid briefs of class `work` are counted in `.state/ingest-failures.json`. Infra failures are not counted, so a rate limit never pushes a range toward splitting.

## Failure classes

`agent.summarize` classifies every finished agent:

- `none`: success.
- `infra`: an error result that mentions a rate limit, overload, a 5xx, a credit or usage limit, auth or a network error; a rejected rate-limit event in the stream; or no first turn at all.
- `work`: every other failure.

An infra failure during writing stops dispatch. In-flight bundles are killed and reverted, undispatched pages become `infra` gaps, and the fix round is skipped.

## Write surface

- No agent gets `Bash`. `build_argv` refuses any tool outside `Read, Glob, Grep, Edit, Write`.
- Every spawn uses `--permission-mode dontAsk` with both `--tools` and `--allowedTools` (see [[claude-p-tools-flag]]). A writer's `--allowedTools` holds the read tools plus one `Edit(/<absolute page path>)` rule per owned page, so the CLI denies a write anywhere else.
- Analysis gets one directory rule, `Edit(/<run dir>/brief/**)`; recall gets `brief/pages/**`. `_dir_rule` builds such a rule only for a directory inside the run's own agent dir.
- After every agent, write attribution re-reads the agent's `Edit`/`Write` tool calls. An out-of-surface write under `llake/` is reverted from `pre/`. A write outside `llake/` is reported and never touched.
- Record pages (`wiki/decisions/`) are never rewritten. A writer may only set `status:` to `deprecated` or `stale` and add one dated `Superseded`/`Resolved` note under the title.
- `wiki/discussions/` is excluded everywhere, and `llake/index.md` is never edited. New pages can only go into an existing category, meaning one that has its `<cat>/<cat>.md` index. Any other new page is dropped and noted in the log entry.

## Page classes

`wiki.page_class` sorts every page path into one of five classes:

| Class | Pages | Treatment |
|---|---|---|
| `excluded` | `wiki/discussions/**`, non-page paths | never scanned for leads, never dispatched |
| `index` | `<cat>/<cat>.md` | rows rebuilt by code; false blurb prose becomes a `blurb` gap |
| `record` | `wiki/decisions/**` | status plus dated note only |
| `gotcha` | `wiki/gotchas/**` | normal edits; analysis must account for any gotcha naming a removed name |
| `state` | everything else | made true at head; auto-added when it names a removed name |

## Cost and time controls

- **Per-agent budgets:** `analysisBudgetUsd` (10), `recallBudgetUsd` (1), `writerBudgetUsd` (3, also used by fixers), `verifierBudgetUsd` (1).
- **Run cap** `maxRunBudgetUsd` (40). Before each pool spawn, the spent total plus the budgets of agents in flight plus the new agent's budget must fit under the cap. If not, the pool waits for a running agent, or marks the job `run-cap` when nothing is in flight. Recall is skipped when its budget does not fit. **The analysis spawn is never checked against the cap.** `report.md` warns when `analysisBudgetUsd` alone exceeds it.
- **Prompt-cache warm-up:** the first writer runs alone until its first turn, so the shared prefix is cached once. That prefix is `stages/writer.shared.md`, passed with `--append-system-prompt-file`. `cacheTtl` reaches every agent as `CLAUDE_CODE_PROMPT_CACHE_TTL`.
- **Deadline:** `timeoutSeconds` (3600) is a soft deadline handled in Python. Each agent's timeout is clipped to the time left, with a 5 s minimum. Past the deadline the pool kills in-flight agents, reverts their bundles and marks the rest `timeout`; the run still finalizes. A hard bash watchdog fires at deadline + `LLAKE_V3_WATCHDOG_GRACE_SECONDS` (300), only to catch a hung process.

## Fixed constants

`hooks/lib/ingest_v3/common.py` fixes these values; they were settled by benchmark and are not config:

| Constant | Value | Meaning |
|---|---|---|
| `HIT_INDEX_CAP` | 20 | hit lines shown per page |
| `PATCH_MAX_BYTES` | 60000 | size above which a per-file patch is split |
| `SPLIT_AFTER_FAILURES` | 2 | analysis failures on one base before the range is halved |
| `STUCK_ATTEMPTS` | 3 | dispatched failures before a gap is stuck |
| `BROKEN_ANCHOR_CAP` | 25 | broken-anchor leads per page |

## Benchmark-only environment hooks

These are environment variables, never config:

- `LLAKE_V3_FROZEN_BRIEF=<earlier agent dir>` copies that run's `brief/` and skips the analysis spawn.
- `LLAKE_V3_STOP_AFTER=<analysis|recall|brief|bundle|write|fix>` stops after that stage with the cursor held. An unknown stage is logged and ignored.

## Module map

| Module | Role | Page |
|---|---|---|
| `run.py` | orchestrator, kill handling, the `hooks.log` line | [[ingest-v3-orchestrator]] |
| `cli.py`, `ingest-v3.py` | `run`, `revert-run`, `owed-major`, `validate-gaps` | [[ingest-v3-orchestrator]] |
| `config.py` | `V3Config`: `ingest.v3.*` over plugin defaults, no defaults in code | [[config-schema]] |
| `plan.py`, `state.py` | run kinds, failure counter, run journal, dead-run recovery | [[ingest-v3-run-planning]] |
| `names.py`, `stage.py`, `brief.py`, `bundle.py` | inputs, analysis/recall prompts, brief assembly, bundling | [[ingest-v3-brief]] |
| `agent.py`, `dispatch.py`, `prompts.py`, `anchors.py`, `checks.py`, `fix.py` | spawning, the pool, writer/verifier prompts, anchor rule, checks, fix round | [[ingest-v3-writers]] |
| `snapshots.py` | pre-run copy, page snapshots, write attribution, kill revert | [[ingest-v3-snapshots]] |
| `finalize.py`, `report.py` | code-owned writes, log entry, cursor, `report.md` | [[ingest-v3-finalize]] |
| `gaps.py` | the gap record | [[ingest-v3-gap-record]] |
| `render.py`, `schema.py`, `schemas/*.json` | in-process prompt rendering, JSON contracts | [[ingest-v3-templates]] |
| `wiki.py`, `common.py` | page model (paths, classes, slugs, catalog), atomic I/O, git helper, constants | this page |

## What a run leaves behind

Everything stays in `.state/agents/<id>/` for review:

- `agent.log`, the run's trace.
- `run.json` (journal), `ledger.json` (costs), `pages.json` (per-page outcomes and history) and `run-config.json` (effective `ingest.v3` settings).
- `pre/` and `pre-manifest.json`, the pre-run copy.
- `inputs/`, `brief/`, `brief.json`, `brief-report.json` and `bundles.json`.
- `bundles/<bNN>/` snapshots.
- `stages/`, holding each agent's `.jsonl` stream, `.prompt.md` and `.summary.json`.
- `checks*.json`, `finalize.json`, `finalize-snapshot/` and `report.md`.

[[enable-ingest-v3]] explains how to read them.

## Key Points

- v3 separates finding stale content (analysis, recall) from writing it (parallel writers, each confined to its own pages).
- Code, not agents, writes `updated:`, category index rows, the log entry, `llake/ingest-gaps.json` and the cursor.
- Pages left unfinished become gaps, and the cursor still advances. Analysis failures hold the cursor. Two `work` failures on one base split the range, and a failing single commit is skipped and recorded.
- No agent has a shell. Writers are restricted per file at the permission level, and each write is checked again afterwards.
- A kill or timeout reverts the run's writes from snapshots, restoring a page only on evidence.
- Spend is bounded per agent and per run (`maxRunBudgetUsd`). The exception is the analysis spawn, which only its own budget bounds.

## Code References

- `hooks/lib/ingest_v3/run.py:254` — `_run`, the stage sequence
- `hooks/lib/ingest_v3/run.py:127` — `_dir_rule`, the analysis/recall directory permission
- `hooks/lib/ingest_v3/plan.py:55` — `plan_run`, the run kinds
- `hooks/lib/ingest_v3/agent.py:17` — `INFRA_RE`, infra failure detection
- `hooks/lib/ingest_v3/agent.py:54` — `build_argv`, spawn flags and tool allowlist
- `hooks/lib/ingest_v3/dispatch.py:46` — `pool`: run cap, warm-up, deadline
- `hooks/lib/ingest_v3/wiki.py:56` — `page_class`
- `hooks/lib/ingest_v3/common.py:9-13` — fixed constants
- `templates/config.default.json` — the `ingest.v3` block
- `schema/operations.md` — the v3 operating summary (gap record, gate, cursor, lock and kill, settings, review)
- `tests/lib/test_v3_run_e2e.py` — end-to-end runs against a fake `claude` (`tests/hooks/fixtures/claude-v3-stub.py`)

## See Also

- [[ingest-v3-orchestrator]] — how post-merge starts a run and how a run is killed
- [[ingest-v3-gap-record]] — the record of owed pages
- [[ingest-v3-writers]] — writers, verifier, checks and the fix round
- [[ingest-v3-brief]] — inputs, analysis, recall, brief and bundles
- [[enable-ingest-v3]] — switching a project over and reading a run
- [[ingest-v2-pipeline]] — the plan-then-apply predecessor
- [[three-writer-model]] — where ingest sits among the writers
- [[config-schema]] — every `ingest.v3.*` key
