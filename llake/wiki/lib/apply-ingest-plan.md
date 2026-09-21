---
title: apply_ingest_plan.py
description: "Deterministic applier that executes a validated ingest plan against the wiki, with per-op failure isolation and a path guard"
tags: [lib, ingest, applier, wiki, python]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[ingest-v2-pipeline]]"
  - "[[plan-schema]]"
  - "[[frontmatter-parser]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[adr-slug-collisions]]"
  - "[[planner-plan-json-fragility]]"
  - "[[three-writer-model]]"
  - "[[adr-plan-apply-split]]"
---
# apply_ingest_plan.py

## Overview

`hooks/lib/apply_ingest_plan.py` is Stage 3 of [[ingest-v2-pipeline]]: it reads a planner-emitted `plan.json` and performs every wiki mutation the plan describes. It is pure stdlib, importable as a module, and runnable as a CLI.

Its reason for existing is that an LLM should decide *what* changes and a deterministic program should decide *how*. Everything the applier does is reproducible, inspectable, and per-operation recoverable.

## CLI

```
python3 apply_ingest_plan.py --plan PATH --wiki-root PATH --llake-root PATH \
    --applied-out PATH --failed-out PATH --today YYYY-MM-DD [--no-log-entry]
```

`--today` is passed explicitly rather than read from the clock so tests are deterministic and so every page touched in one run gets the same `updated:` value. `--no-log-entry` is used by the orchestrator's fix pass so one ingest run yields one `log.md` entry.

| Exit | Meaning |
|---|---|
| 0 | Applied — possibly with per-op failures captured in `failed.json` |
| 1 | Schema-invalid plan, or a bidirectional link naming a page that does not exist |
| 2 | Plan file unreadable, or not JSON at all |

Exits 1 and 2 hold the ingest cursor; exit 0 advances it even with failures recorded.

## Reading the plan

Three distinct steps, deliberately kept separate so the caller can tell the failure modes apart (`hooks/lib/apply_ingest_plan.py:686-702`):

1. `_normalize_plan_text` strips markdown fences and, failing that, extracts the first balanced top-level `{...}` object using a string-aware scanner. Prose wrapped around the JSON is survivable. If nothing object-shaped is found, stderr says **`non-JSON planner output`** and the exit is 2.
2. `_parse_plan_text` calls `json.loads` **strictly first**, so a well-formed plan is never rewritten. Only on `JSONDecodeError` does it retry with `_strip_trailing_commas`. Still-broken JSON produces **`schema-invalid JSON in plan`** and exit 2.
3. `plan_schema.validate` checks structure; errors go to stderr and exit 1. See [[plan-schema]].

Those two quoted stderr strings are a contract: `hooks/lib/ingest-v2.sh` greps for them to write a precise `hooks.log` line.

`_strip_trailing_commas` is string-aware rather than a regex, and that is not fussiness. Plan bodies are markdown, and markdown prose routinely contains `, ]` or `, }`; a regex would silently corrupt page content. See [[planner-plan-json-fragility]].

## De-numbering new decision slugs

`_denumber_new_decision_slugs` rewrites `adr-007-foo` to `adr-foo` for slugs the plan **creates**, and rewrites every reference in step: `bidirectional_links`, `log_entry.pages_affected`, and `[[wikilinks]]` anywhere in the plan. `updates[]` and `deletes[]` are untouched — they name pages already on disk under their historical numbered names.

Two creates that would collapse onto the same de-numbered slug are left numbered, so `apply_create`'s `AlreadyExists` surfaces the real conflict instead of the tool hiding it. See [[adr-slug-collisions]].

## The operations

### `replace`

`apply_replace_ops` resolves every anchor against the **original** body, not the running result, then applies the spans in reverse position order so offsets never shift. Three guarantees are enforced before any byte moves:

- `AnchorNotFound` — the `find` text does not appear.
- `AnchorAmbiguous` — it appears more than once. Uniqueness is required; the planner must extend the anchor with context.
- `EditOverlap` — two anchors share bytes in the original.

Resolving against the original is what makes multi-op updates predictable: the planner reasons about one document, not about a sequence of intermediate states.

### `append_section`

`apply_section_ops` finds the exact heading line, computes the section end as the next heading of the same or higher level (or EOF), and inserts there. Unlike `replace`, these ops operate on the **running** text, so two appends to the same section accumulate the way a planner expects. A missing heading raises `HeadingNotFound`.

### `body_replace`

Wholesale body replacement, frontmatter preserved. Mutually exclusive with `replace` in the same update, and the schema rejects the combination.

### `frontmatter_set` / `frontmatter_add_related`

Applied to the parsed frontmatter dict. `frontmatter_add_related` skips items already present, so it is idempotent. `updated:` is then overwritten with `--today` regardless of what the plan said.

### Creates, deletes, bidirectional links

- `apply_create` writes `<wiki_root>/<category>/<slug>.md`, creating the category directory if needed, and raises `AlreadyExists` rather than overwriting.
- `apply_delete` unlinks the page, scrubs `[[slug]]` from every other page's `related:`, and **reports** — but does not rewrite — inline body mentions, which come back as `dangling_inline_links` warnings in `applied.json`. A slug that is already gone returns `target_already_absent` rather than failing.
- `apply_bidirectional_link` ensures each side's `related:` contains the other. Links where either side is in `deletes[]` are skipped with a note.

## The discussions exclusion

`_walk_wiki_pages` filters out everything under `wiki/discussions/` and backs every walk in the module — `_scrub_related`, `_scan_inline_links`, `_resolve_slug_path`, and `apply_delete`. Discussions are owned by the session-capture writer, and the guarantee ingest offers is stronger than "will not write there": **discussions are invisible to ingest**. A delete targeting a discussion page returns `target_already_absent`, because the walker never reaches it.

## The path guard

`check_write_path` runs before every write and resolves through `os.path.realpath`, so a symlink cannot be used to escape:

- Anything outside `<llake_root>/` → `ForbiddenPath`
- `config.json`, `last-ingest-sha` → `ForbiddenPath`
- `.state/**`, `schema/**` → `ForbiddenPath`
- Anything outside `<wiki_root>/` (except `log.md` when `allow_log_md=True`) → `ForbiddenPath`
- `wiki/discussions/**` → `ForbiddenPath`

This duplicates rules also stated in the planner's prompt, on purpose. The prompt is advice; this is enforcement.

## Atomicity and failure isolation

`_atomic_write` writes a sibling `.<name>.md.tmp` then `os.replace`s it, so a page is never observed half-written. Combined with the fact that all ops for one update are computed in memory before the single write, an update is **all-or-nothing**: if op 3 of 4 raises, the file on disk is byte-identical to the original.

The three per-entry loops in `main` catch `ApplyError`, `OSError`, `UnicodeDecodeError`, and `frontmatter.FrontmatterParseError`, mapping each to a `reason` string via `_classify_error` and appending to `failed.json`. One unreadable page therefore costs one entry, not the whole run.

`reason` values: `AnchorNotFound`, `AnchorAmbiguous`, `EditOverlap`, `HeadingNotFound`, `AlreadyExists`, `SlugNotFound`, `ForbiddenPath`, `FrontmatterParseError`, `IOError`.

## Outputs

- `applied.json` — `{updates, creates, deletes, bidirectional_links}`, each entry naming the slug and any notes or warnings.
- `failed.json` — a list of `{slug, reason, detail}`. This is the fixer's input.
- `log.md` — one `## [date] ingest | v2 | <range> | <summary>` heading plus a `Pages affected:` line, and, when there are failures, a second `ingest-failures` block listing each one with its reason and a 120-char detail. A plan with `skip_reason` writes a single skip line so the operator can see the planner ran and chose not to act.

## Key Points

- Strict JSON parse first; trailing-comma recovery only as a fallback, using a string-aware scanner so markdown bodies are never corrupted.
- Two exact stderr strings (`non-JSON planner output`, `schema-invalid JSON`) are a contract with the orchestrator's log line.
- `replace` anchors resolve against the original body and are applied in reverse order; anchors must be unique and non-overlapping.
- `append_section` operates on the running text; `replace` does not. The asymmetry is intentional.
- An update is all-or-nothing — every op is computed before the single atomic write.
- `wiki/discussions/**` is skipped by the wiki walker, so it is invisible rather than merely forbidden.
- `check_write_path` resolves `realpath`, defeating symlink escapes.
- Failures are isolated per entry into `failed.json`; the run still exits 0 and the cursor still advances.
- `adr-NNN-` prefixes are stripped from newly created decision slugs, with all references rewritten in step.

## Code References

- `hooks/lib/apply_ingest_plan.py:53-84` — exception hierarchy and `reason` strings
- `hooks/lib/apply_ingest_plan.py:94-100` — `_atomic_write`
- `hooks/lib/apply_ingest_plan.py:103-119` — `_walk_wiki_pages` and the discussions exclusion
- `hooks/lib/apply_ingest_plan.py:221-251` — `apply_replace_ops`
- `hooks/lib/apply_ingest_plan.py:254-277` — `apply_section_ops`
- `hooks/lib/apply_ingest_plan.py:288-301` — frontmatter ops
- `hooks/lib/apply_ingest_plan.py:306-334` — `apply_update`
- `hooks/lib/apply_ingest_plan.py:337-372` — `apply_create`, `apply_delete` and the cascade
- `hooks/lib/apply_ingest_plan.py:386-422` — `check_write_path`
- `hooks/lib/apply_ingest_plan.py:437-497` — `_normalize_plan_text`
- `hooks/lib/apply_ingest_plan.py:500-560` — `_strip_trailing_commas` and `_parse_plan_text`
- `hooks/lib/apply_ingest_plan.py:563-636` — `_denumber_new_decision_slugs`
- `hooks/lib/apply_ingest_plan.py:639-667` — `_append_log_entry`
- `hooks/lib/apply_ingest_plan.py:670-792` — CLI `main`
- `tests/lib/test_apply_ingest_plan.py` — op semantics, atomicity, forbidden paths, parse recovery

## See Also

- [[plan-schema]] — the validator run before any op executes
- [[frontmatter-parser]] — the parser/serializer used for every page round-trip
- [[ingest-v2-pipeline]] — where this sits in the pipeline
- [[ingest-v2-orchestrator]] — the shell that invokes it twice per run
- [[planner-plan-json-fragility]] — the malformed-plan failure modes
- [[adr-slug-collisions]] — why decision slugs are de-numbered
