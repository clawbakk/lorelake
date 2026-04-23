---
title: "ADR-001: Ingest Runs on post-merge, Not post-commit"
description: "Why ingest triggers on git merge rather than every commit — cost, noise, and trustworthiness"
tags: [decisions, architecture]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[post-merge-hook]]"
  - "[[three-writer-model]]"
---

# ADR-001: Ingest Runs on post-merge, Not post-commit

## Decision

The ingest writer — the background agent that updates the wiki from source code changes — is triggered by the git `post-merge` hook, not `post-commit`. It activates only when new commits land on the configured branch (via `git pull` or a merge commit), and only when those commits touch files covered by `ingest.include`.

## Context

LoreLake's ingest writer exists to keep the wiki in sync with the codebase. Whenever code changes arrive, an ingest agent reads the diff, examines the affected files, and updates or creates wiki pages to reflect those changes.

There are two natural trigger points in a git workflow:

- **post-commit** — fires after every local `git commit`, including WIP commits, amends, fixups, and rebased fragments.
- **post-merge** — fires after `git pull` completes (or after a merge commit lands), meaning it captures a batch of commits that made it into the branch from elsewhere.

The `ingest.branch` config key (defaulting to `main`) specifies which branch is monitored. The hook reads `last-ingest-sha` to know the previous position and compares it against the current `HEAD` to detect new commits. A pre-flight diff check further skips runs where no `ingest.include` paths changed.

See [[post-merge-hook]] for the full implementation.

## Rationale

**post-commit is too noisy for a per-run LLM cost.** A developer committing a feature might create five or ten individual commits during a normal working session — stash pops, fixups, checkpoint saves, and the final "done" commit. Running a full ingest agent on every one of those commits would:

1. Multiply cost by the number of commits rather than the number of merged features.
2. Frequently analyze intermediate states that will be amended or rebased away before they ever reach the canonical branch.
3. Produce wiki updates that contradict each other as a feature is developed and revised.

**post-merge captures reviewed, stable code.** When code lands in the monitored branch via a pull, it represents work that is complete enough to be shared. This is the right semantic unit for wiki documentation: "a batch of coherent changes arrived."

**The SHA cursor design reinforces this.** The `last-ingest-sha` file records the last commit that was successfully ingested. On each post-merge trigger, the hook diffs from that SHA to the new `HEAD`, which naturally covers the full set of commits that just arrived — even if several landed together.

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| **post-commit** | Fires on every local commit; too noisy, high cost, analyzes unstable intermediate states |
| **post-push** | Not a standard git hook; not guaranteed to fire, e.g. on direct-to-branch pushes by others |
| **Polling via cron** | Requires persistent process or scheduler setup; out of scope for a hooks-only plugin |
| **Manual invocation only** | Wiki would fall out of sync silently; defeats the purpose of automated capture |

## Consequences

**What this commits you to:**

- The wiki will lag behind local commits until they are merged into the monitored branch. A developer who commits to a feature branch will not see wiki updates until the branch merges.
- If a developer works entirely in local commits without ever merging (e.g., solo project on main with only local commits), the trigger will not fire unless they do a `git pull` that brings in at least one new commit.
- The monitored branch must be configured correctly via `ingest.branch`. If this points at the wrong branch, ingest will silently not run.

**Acceptable trade-offs:**

- The wiki documents the project's canonical, merged state — not transient work-in-progress. This is consistent with what documentation should capture.
- Cost is bounded to roughly one ingest run per pull/merge event rather than per commit.
- The pre-flight diff check (`git diff --name-only $LAST_SHA..$CURRENT_SHA -- "${INCLUDE_PATHS[@]}"`) ensures the agent is not spawned when only non-included files changed, further reducing unnecessary runs.

## See Also

- [[post-merge-hook]] — implementation details of the hook, SHA cursor, and pre-flight check
- [[three-writer-model]] — the full picture of all three writers and their triggers
