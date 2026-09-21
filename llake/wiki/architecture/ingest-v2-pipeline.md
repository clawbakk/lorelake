---
title: "Ingest Pipeline v2"
description: "Plan-then-apply ingest: a read-only planner agent emits JSON, a Python applier executes it, a fixer repairs rejected ops"
tags: [architecture, ingest, pipeline, post-merge, agents]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[post-merge-hook]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[build-ingest-context]]"
  - "[[apply-ingest-plan]]"
  - "[[plan-schema]]"
  - "[[adr-plan-apply-split]]"
  - "[[three-writer-model]]"
  - "[[config-schema]]"
  - "[[enable-ingest-v2]]"
  - "[[planner-plan-json-fragility]]"
  - "[[runtime-layout]]"
  - "[[ingest-gate]]"
---
# Ingest Pipeline v2

## Overview

Ingest v2 is a second implementation of LoreLake's ingest writer, selected by setting `ingest.pipeline` to `"v2"` in the project's `config.json`. It replaces "one agent reads diffs and edits wiki files" with **plan, then apply**: a read-only agent emits a single JSON document describing every intended change, and a deterministic Python program executes it.

Both pipelines ship, both are maintained, and the default is still `legacy`. `hooks/post-merge.sh` performs all shared setup — branch guard, commit range, include paths — and then branches. See [[post-merge-hook]].

## Why it exists

The legacy pipeline gives an LLM write access to the wiki and asks it, in prose, to follow the schema. That has three failure modes that no amount of prompt engineering fixes:

1. **Partial failure is invisible.** If the agent botches one edit out of twelve, the run still exits 0 and the cursor advances. Nothing records which edit was wrong.
2. **The write surface is a request.** "Never write to `wiki/discussions/`" is a sentence in a prompt. An agent that misreads it succeeds.
3. **Cost scales with editing, not with thinking.** Every `Edit` round-trip burns planner-grade tokens on mechanical text substitution.

Splitting the two halves fixes all three: the plan is inspectable before anything is written, failures are per-operation and routed to a repair pass, and the path guard is code. See [[adr-plan-apply-split]].

## The five stages

```
post-merge.sh (ingest.pipeline == "v2")
     |
     v
Stage 1  build_ingest_context.py      -> context/{changes.json, diffs/, wiki-index.json}
     |
     v
Stage 2  planner agent (claude -p)    -> plan.json           [Read, Glob, Grep only]
     |
     v
Stage 3  apply_ingest_plan.py         -> applied.json, failed.json
     |
     v
Stage 4  fixer agent (if failures)    -> fix-plan.json       [optional, maxFixerRetries]
         apply_ingest_plan.py --no-log-entry -> final-{applied,failed}.json
     |
     v
Stage 5  finalize: log line, advance last-ingest-sha
```

### Stage 1 — structured context

`build_ingest_context.py` turns the commit range into files on disk so the planner never runs `git` itself: per-commit metadata, per-file diffs chunked on hunk boundaries, and a flat catalog of every existing wiki page. See [[build-ingest-context]].

This is what makes the planner's tool allowlist viable. It needs no `Bash`, because everything it would have shelled out for is already a file.

### Stage 2 — the planner

A `claude -p` agent with `--tools Read,Glob,Grep`. Its prompt is `hooks/prompts/ingest.v2.md.tmpl`, rendered with three large slots — the schema rules, the plan format, and worked examples — which default to `templates/schema-rules.md`, `templates/plan-format.md`, and `templates/plan-examples.md` and can be overridden per project through `config.prompts`. Its entire output is one JSON document, extracted from the stream by `format-agent-log.py --extract-result`.

The planner **cannot write**. That is the point.

### Stage 3 — the applier

`apply_ingest_plan.py` validates the plan against [[plan-schema]], then executes it: surgical `replace` ops resolved against the original text, `append_section`, `body_replace`, frontmatter ops, creates, deletes with `related:` cascade, and bidirectional links. Each page is written atomically and each operation fails independently into `failed.json`. See [[apply-ingest-plan]].

### Stage 4 — the fixer

If `failed.json` is non-empty and `ingest.v2.maxFixerRetries > 0`, a second agent receives the original plan, the failure list, and the *current* bodies of the affected pages, and emits a corrected plan for those operations only. That plan is applied with `--no-log-entry`, so one ingest run still produces exactly one entry in `log.md`. A fixer failure is non-fatal — the first-pass results stand.

### Stage 5 — finalize

The cursor advances on a **best-effort** policy: partial success still advances `last-ingest-sha`, with the remaining failures written into `log.md` under an `ingest-failures` heading so a human can see them. The cursor is held only when the pipeline could not produce a trustworthy result at all — Stage 1 failed, the planner failed, the plan would not parse or validate, or the applier was killed before writing its outputs.

## What is idempotent and what is not

`frontmatter_add_related` and `apply_bidirectional_link` are idempotent. `replace` is not — its anchor usually no longer exists after a successful apply, so a re-run of the same plan produces `AnchorNotFound` rather than a double edit. This is the safe direction to fail in, and it is why held cursors are recoverable: re-processing a range produces a *new* plan against the current wiki state, not a replay of the old one.

## Failure modes and where they surface

| Failure | `hooks.log` | Cursor |
|---|---|---|
| Stage 1 (git/IO error) | `failed: stage1` | held |
| Planner render failure | `render-failed: planner` | held |
| Planner nonzero exit or empty plan | `failed: planner (exit N)` | held |
| Planner output is not JSON at all | `failed: applier (non-JSON-plan)` | held |
| Plan is JSON but unparseable or schema-invalid | `failed: applier (malformed-JSON` / `schema-invalid)` | held |
| Applier killed before writing outputs | `aborted: ... no applied/failed` | held |
| Some ops failed, rest applied | `partial: ... (applied: N, failed: M)` | **advanced** |
| Clean run | `completed: ... (applied: N)` | advanced |

A truncated plan — the planner hitting its budget mid-document — is **not** recoverable. Truncation loses content rather than corrupting syntax, so there is nothing to repair. See [[planner-plan-json-fragility]].

## Housekeeping

Before each run the orchestrator deletes any `wiki/**/.*.md.tmp` left behind by an applier killed mid-write, so a previous timeout does not show up as noise in `git status`.

## Key Points

- Selected by `ingest.pipeline: "v2"`; the literal string `v2` is the only value that activates it.
- The planner agent is read-only (`Read`, `Glob`, `Grep`); every wiki byte is written by `apply_ingest_plan.py`.
- Five stages: context build → plan → apply → optional fix → finalize.
- Failures are per-operation and land in `failed.json`; the fixer gets one pass by default.
- Cursor policy is best-effort: partial success advances the SHA and records the remainder in `log.md`.
- The cursor is held only when no trustworthy result exists — stage-1, planner, parse, schema, or applier-kill failures.
- A truncated plan is unrecoverable by design; a plan with a stray trailing comma is recovered.

## Code References

- `hooks/post-merge.sh:85-89` — the `ingest.pipeline` switch
- `hooks/post-merge.sh:170-213` — v2 dispatch: agent dir, lock, watchdog, `run_ingest_v2`
- `hooks/lib/ingest-v2.sh:18-216` — the orchestrator
- `hooks/lib/build_ingest_context.py:171-201` — Stage 1 entry point
- `hooks/prompts/ingest.v2.md.tmpl` — planner prompt
- `hooks/prompts/ingest.v2.fix.md.tmpl` — fixer prompt
- `templates/schema-rules.md`, `templates/plan-format.md`, `templates/plan-examples.md` — planner prompt slot defaults
- `hooks/lib/apply_ingest_plan.py:670-792` — applier CLI
- `templates/config.default.json:38-51` — the `ingest.v2` defaults
- `tests/hooks/test_post_merge_v2.sh` — end-to-end coverage including fixer and cursor-held paths
- `tests/manual/benchmark_ingest.sh` — manual legacy-vs-v2 comparison helper

## See Also

- [[adr-plan-apply-split]] — the decision to separate planning from application
- [[ingest-v2-orchestrator]] — `run_ingest_v2`, the shell that sequences the stages
- [[build-ingest-context]] — Stage 1
- [[apply-ingest-plan]] — Stage 3
- [[plan-schema]] — the plan contract
- [[planner-plan-json-fragility]] — what happens when the plan is malformed or truncated
- [[enable-ingest-v2]] — how to switch a project over and verify it
- [[post-merge-hook]] — the hook that dispatches this pipeline
- [[three-writer-model]] — where ingest sits among the writers
