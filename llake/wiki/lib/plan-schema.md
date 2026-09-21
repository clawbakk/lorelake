---
title: plan_schema.py
description: "Structural validator for ingest v2 plan.json — slugs, op types, cross-bucket uniqueness, and pages_affected drift"
tags: [lib, ingest, validation, python]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[apply-ingest-plan]]"
  - "[[ingest-v2-pipeline]]"
  - "[[page-format]]"
---
# plan_schema.py

## Overview

`hooks/lib/plan_schema.py` validates the JSON plan emitted by the ingest v2 planner before [[apply-ingest-plan]] touches a single file. It is pure stdlib, importable (`plan_schema.validate(plan_dict) -> list[str]`) and runnable as a CLI (`python3 plan_schema.py <plan-path>`).

It exists because the applier's code paths assume shape. Without an up-front structural check, a plan missing an `ops` key or holding a non-dict where an object belongs would surface as a `KeyError` or `TypeError` *mid-run* — after some pages had already been rewritten. Validation is all-or-nothing and happens before any mutation, so a malformed plan costs nothing but the planner's tokens.

## What it checks

### Top level

All eight keys must be present: `version`, `skip_reason`, `summary`, `updates`, `creates`, `deletes`, `bidirectional_links`, `log_entry`. If any are missing, validation **returns immediately** without running the deeper checks — reporting fifty downstream errors caused by one missing key helps nobody.

### Slugs

`^[a-z0-9][a-z0-9-]*$`. Lowercase, hyphen-separated, no leading hyphen. Applied to every slug in `updates`, `creates`, `deletes`, and both sides of every bidirectional link.

### Updates

- `ops` must be present **and a list**. An earlier version treated a missing `ops` as an empty list, which let a malformed update pass validation and then crash the applier.
- Each op must be a dict with an `op` key, and that key must be one of `replace`, `append_section`, `frontmatter_set`, `frontmatter_add_related`, `body_replace`.
- `body_replace` and `replace` in the same update is an error — they express contradictory intents about the same bytes.

### Creates

`category` (string), `front_matter` (object), and `body` (string) are all required.

### Bidirectional links

`a` and `b` must both be present and be valid slugs, and `a != b`. A self-loop would make a page list itself as related, which is meaningless and would survive silently.

Note what is **not** checked here: whether those slugs correspond to real pages. The validator receives a dict and cannot see the filesystem. That existence check lives in the applier's CLI, which has `--wiki-root`, and it is treated as schema-level — a failure exits 1 and holds the cursor.

### Cross-bucket slug uniqueness

A slug may appear in at most one of `updates`, `creates`, `deletes`. Updating and deleting the same page in one plan is contradictory; creating and updating it is a plan that has confused itself about what already exists.

### `log_entry`

`operation`, `commit_range`, and `summary` must each be present **and be strings** — all three are interpolated directly into `log.md`, where a non-string would produce garbage or a crash.

Then the drift check: `log_entry.pages_affected` must equal, as a set, the union of all slugs in the three buckets. Mismatches report the missing and extra slugs by name.

This last check earns its keep. `pages_affected` is what a human reads in `log.md` to know what an ingest run touched; if it drifts from reality the log becomes actively misleading rather than merely incomplete. It is also a cheap proxy for planner attention — a plan whose roll-up does not match its own contents was probably assembled carelessly elsewhere too.

## Interface

`validate` returns a list of human-readable error strings, empty when valid. It never raises and never exits; the caller decides. As a CLI it prints each error to stderr and exits 1 (invalid), 2 (unreadable/unparseable), or 0.

```python
import plan_schema
errors = plan_schema.validate(plan)
if errors:
    for e in errors:
        print(e, file=sys.stderr)
    sys.exit(1)
```

Errors are addressed by path — `updates[3].ops[1]: unknown op 'rewrite'` — so the fixer agent can act on them without re-deriving where the problem is.

## Key Points

- Validation is structural only and runs before any mutation; a bad plan costs zero writes.
- Missing top-level keys short-circuit the deeper checks to keep the error list readable.
- `ops` must be a list; a missing `ops` is an error, not an empty list.
- `body_replace` and `replace` are mutually exclusive within one update.
- Slug existence against the real wiki is the applier CLI's job, not this module's.
- A slug may appear in at most one of `updates` / `creates` / `deletes`.
- `log_entry.pages_affected` must exactly equal the union of all plan slugs.
- `validate` returns errors rather than raising or exiting — the caller owns the policy.

## Code References

- `hooks/lib/plan_schema.py:19-24` — required keys, slug regex, allowed op set
- `hooks/lib/plan_schema.py:32-51` — `_check_update`, including the `ops`-is-a-list and mutual-exclusion rules
- `hooks/lib/plan_schema.py:54-65` — `_check_create`
- `hooks/lib/plan_schema.py:76-90` — `_check_bidir_link` and the self-loop rejection
- `hooks/lib/plan_schema.py:111-121` — cross-bucket slug uniqueness
- `hooks/lib/plan_schema.py:129-149` — `log_entry` field checks and the `pages_affected` drift check
- `hooks/lib/apply_ingest_plan.py:706-727` — caller: `validate`, then the wiki-existence check for bidirectional links
- `tests/lib/test_plan_schema.py` — unit tests for every rule above

## See Also

- [[apply-ingest-plan]] — the only caller, and the owner of the existence check
- [[ingest-v2-pipeline]] — where validation sits in the run
- [[page-format]] — the page conventions the plan is ultimately expressing
