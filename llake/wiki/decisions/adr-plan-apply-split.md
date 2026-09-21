---
title: "ADR: Split ingest into a read-only planner and a deterministic applier"
description: "Why ingest v2 has an agent emit a JSON plan that Python executes, instead of letting the agent edit the wiki directly"
tags: [decisions, architecture, ingest, agents]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[ingest-v2-pipeline]]"
  - "[[apply-ingest-plan]]"
  - "[[adr-001-post-merge-trigger]]"
  - "[[three-writer-model]]"
---
# ADR: Split ingest into a read-only planner and a deterministic applier

## Status

Accepted. Shipped behind `ingest.pipeline: "v2"`; `legacy` remains the default and both implementations are maintained.

## Context

The original ingest writer was one `claude -p` agent with `Read, Write, Edit, Glob, Grep, Bash`. It read diffs, decided what the wiki needed, and edited files itself. Three problems emerged in practice:

**Partial failure was invisible.** An agent that botched one edit out of twelve still exited 0. The cursor advanced, the log said `completed`, and nothing recorded which edit had gone wrong. The only way to find out was to read the diff afterwards.

**The write surface was a request, not a boundary.** "Never write to `wiki/discussions/`" and "never touch `schema/`" were sentences in a prompt. An agent that misread them succeeded. For a boundary that separates two writers' territory — ingest versus session-capture — prose is not enough.

**Cost scaled with editing, not with thinking.** Each `Edit` round-trip spent planner-grade tokens on mechanical text substitution, and a page rewritten twice in one run cost twice.

## Decision

Separate deciding from doing.

- A **planner** agent runs with `Read, Glob, Grep` and no write tools at all. Its entire output is one JSON document describing every intended change.
- A **Python applier** validates that document and performs every mutation.
- A **fixer** agent gets one pass at repairing operations the applier rejected.

A Python pre-processor builds a context directory first — commit metadata, per-file diffs, a wiki catalog — so the planner needs no `Bash` either. Removing a tool from an agent is only safe once you have removed its reason to want it.

## Consequences

**Good:**

- The write surface is enforced by `check_write_path`, which resolves `realpath` and refuses forbidden targets regardless of what the plan says. `wiki/discussions/**` is not merely forbidden to ingest — the applier's wiki walker skips it, so it is invisible.
- Failures are per-operation. `failed.json` names the slug, the reason, and the detail, which is a usable input for both a repair agent and a human.
- The plan is inspectable before anything is written, and it persists in the agent directory afterwards. "What did this run intend?" is answerable.
- Edits are cheap. The expensive model thinks once; substitution is free.
- Anchor semantics are guaranteed rather than hoped for — `replace` anchors resolve against the original text, uniqueness and non-overlap are checked before any byte moves, and an update is all-or-nothing.

**Bad:**

- Everything now depends on one document parsing. A truncated or malformed plan discards the whole run, which is a failure mode the legacy pipeline did not have. See [[planner-plan-json-fragility]].
- Five stages, two agents, and several JSON artifacts are more moving parts to debug than one agent.
- The planner must express changes in an op vocabulary rather than just editing. A change it cannot express surgically falls back to `body_replace`, losing the benefit.
- Two pipelines now exist and both must be maintained.

## Alternatives considered

**Keep one agent, tighten the prompt.** Rejected: the write-surface problem is unfixable this way. A prompt cannot make a boundary non-negotiable, and every prior tightening had been followed by a new way around it.

**One agent, but post-hoc validation.** Let the agent write, then check the result and revert violations. Rejected: reverting is unreliable once an agent has made interleaved edits across files, and a violation that has already been written has already happened — for a secrets or discussions violation, detection after the fact is not much better than none.

**Structured tool calls instead of a JSON plan.** Have the agent call narrow `update_page` / `create_page` tools. Attractive, and closer to how the problem is usually solved, but it requires tool-definition plumbing LoreLake does not have in `claude -p`, and it loses the property that the *whole* plan is inspectable and validatable before the first write.

**Make v2 the default immediately.** Rejected for now: the legacy pipeline works, the failure modes of v2 are still being learned, and `ingest.pipeline` makes switching back a one-line change.

## Key Points

- The planner is read-only; every wiki byte is written by `apply_ingest_plan.py`.
- The motivation is invisible partial failure, an unenforceable write surface, and cost scaling with editing.
- Enforcement moved from prompt prose to a `realpath`-resolving path guard.
- The cost is a single-point-of-failure plan document and more moving parts.
- Both pipelines ship; `ingest.pipeline` selects, and `legacy` is still the default.

## Code References

- `hooks/post-merge.sh:85-89` — the pipeline switch
- `hooks/lib/ingest-v2.sh:18-216` — stage sequencing
- `hooks/lib/apply_ingest_plan.py:386-422` — `check_write_path`, the enforced boundary
- `hooks/lib/apply_ingest_plan.py:103-119` — the discussions exclusion
- `templates/config.default.json:38-51` — `ingest.v2` defaults

## See Also

- [[ingest-v2-pipeline]] — the resulting architecture
- [[apply-ingest-plan]] — the applier
- [[planner-plan-json-fragility]] — the main cost of this decision
- [[adr-001-post-merge-trigger]] — the trigger decision this builds on
- [[three-writer-model]] — how this changes the ingest writer's safety story
