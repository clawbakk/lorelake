---
title: "Numbered ADR slugs collide across branches"
description: "adr-NNN sequence numbers are allocated from the local wiki, so parallel branches claim the same number and git merges both without conflict"
tags: [gotchas, decisions, slugs, git, ingest]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[apply-ingest-plan]]"
  - "[[page-format]]"
  - "[[ingest-v2-pipeline]]"
---
# Numbered ADR slugs collide across branches

## What goes wrong

Decision records were named `adr-NNN-short-title`, with `NNN` allocated as "highest existing number plus one", read from the local wiki.

That allocation is only correct if there is one wiki. There is not — there is one per branch and per worktree. Two branches created the same day each read the same highest number and each claimed `NNN+1`. Because the resulting files have **different names** (`adr-011-foo.md` and `adr-011-bar.md`), git merges them cleanly with no conflict and nothing to review.

One real install ended up with `adr-011` twice and `adr-012` three times, each created on a different branch on the same day.

## Why max+1 cannot be fixed

A branch is a distributed system. Allocating a globally unique counter across it requires coordination that does not exist — there is no shared authority to ask, and the merge point is too late, since by then both files exist and neither is wrong.

Reading the wiki harder does not help. `max+1` can *detect* a collision after the fact; it can never prevent one. The only fix that works is to stop having a counter.

## The fix: content-derived slugs

Decision slugs are now `adr-<short-title>` with no number: `adr-plan-apply-split`, `adr-post-merge-trigger`. A content-derived slug has nothing to contend on, so the collision class disappears rather than being mitigated. Two branches proposing genuinely the same decision now produce genuinely the same filename — which git *will* conflict on, which is correct, because that is a real conflict.

Ordering information was never carried by the number anyway: `created:` in the frontmatter is more accurate and does not need to be allocated.

## Automatic de-numbering in the applier

Planner agents trained on the old convention still emit numbered slugs, so `apply_ingest_plan.py` strips the prefix as a safety net (`_denumber_new_decision_slugs`, `hooks/lib/apply_ingest_plan.py:563-636`). Three properties matter:

- **Only `creates[]` is rewritten.** `updates[]` and `deletes[]` name pages that already exist on disk under their historical numbered names — rewriting those would target files that do not exist.
- **References are rewritten in step**: `bidirectional_links`, `log_entry.pages_affected`, and `[[wikilinks]]` anywhere in the plan, including inside page bodies.
- **Genuine conflicts are preserved.** If two creates would collapse onto the same de-numbered slug, both are left numbered so `apply_create` raises `AlreadyExists` and the conflict surfaces. Silently merging two different decisions into one page would be worse than the problem being solved.

Each rename is reported on stderr, which the orchestrator folds into `agent.log`.

## Existing numbered pages stay

`adr-001-post-merge-trigger`, `adr-002-two-pass-triage`, and `adr-003-bash-3-2-portability` keep their names. Renaming them would break every `[[wikilink]]` pointing at them for no benefit. The convention applies to new decisions; old ones are grandfathered.

## Key Points

- `adr-NNN` numbers were allocated from the local wiki, so parallel branches claimed the same number.
- Git merges the duplicates cleanly because the filenames differ — there is no conflict to review.
- `max+1` can detect a collision but can never prevent one; the counter had to go, not be improved.
- New decision slugs are `adr-<short-title>`, derived from content, with nothing to contend on.
- `_denumber_new_decision_slugs` strips prefixes from `creates[]` only, rewriting all references in step.
- Two creates collapsing onto one slug are left numbered so `AlreadyExists` surfaces the real conflict.
- Pre-existing numbered ADRs are grandfathered; do not rename them.

## Code References

- `hooks/lib/apply_ingest_plan.py:563-636` — `_denumber_new_decision_slugs`
- `hooks/lib/apply_ingest_plan.py:563` — `ADR_SEQ_RE`, the `^adr-\d+-` prefix pattern
- `hooks/lib/apply_ingest_plan.py:594-597` — the collapsed-slug guard
- `hooks/lib/apply_ingest_plan.py:704` — where it runs, between parse and validation
- `templates/plan-examples.md`, `templates/schema-rules.md`, `templates/plan-format.md` — planner-facing anchors, corrected
- `schema/core.md` — the naming convention itself
- `tests/lib/test_apply_ingest_plan.py` — de-numbering and collision tests

## See Also

- [[apply-ingest-plan]] — where de-numbering runs in the apply sequence
- [[page-format]] — naming conventions, including slug uniqueness
- [[ingest-v2-pipeline]] — the pipeline that creates most new decision pages
