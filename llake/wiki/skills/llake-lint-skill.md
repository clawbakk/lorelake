---
title: "/llake-lint — Wiki Quality Check"
description: "On-demand lint pass for broken links, one-sided related:, stale pages, and missing sections"
tags: [skills, lint, quality]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[llake-doctor-skill]]"
  - "[[llake-bootstrap-skill]]"
  - "[[config-schema]]"
---

# /llake-lint — Wiki Quality Check

## Overview

`/llake-lint` is an on-demand wiki integrity and content-quality auditor. It builds a full inventory of wiki pages, runs mechanical and content-level checks, auto-fixes purely structural issues, and surfaces findings that require human judgment. It never modifies `wiki/discussions/**`, `wiki/decisions/**`, `wiki/gotchas/**`, or `wiki/playbook/**` — those are historical directories owned by session-capture and their Key Facts blocks are immutable.

Two modes exist, with a deliberate cost split:

- **Quick** — five mechanical, deterministic checks over the wiki's filesystem and frontmatter. In-session, inherits the parent session's model and limits. Cheap enough to run regularly (recommended cadence: once every week or two, or whenever the wiki "feels stale").
- **Comprehensive** — everything Quick does, plus six content-level checks dispatched as read-only diagnosis subagents per wiki shard, followed by user-confirmed repair subagents for any findings the user wants to address. Expensive enough to warrant a confirmation prompt. Typical cadence: monthly, or before relying on the wiki for a critical decision (big PR merge, onboarding a new engineer).

---

## Invocation forms

```
/llake-lint                  # no-arg: reads log.md, computes a recommendation, prompts
/llake-lint quick            # run Quick immediately, no prompt
/llake-lint comprehensive    # run Comprehensive after a single confirmation
```

Argument matching is case-insensitive. Any other argument stops with an error listing the valid forms.

---

## Preconditions

Lint validates the install before running any checks:
- `llake/config.json` must exist and parse as JSON. If missing: "Run `/llake-lady` first." If invalid JSON: "Run `/llake-doctor` to repair."
- `llake/wiki/` must exist. If missing: "Run `/llake-doctor` first."
- `llake/log.md` must exist. If missing: "Run `/llake-doctor` first."

These are deliberately strict — running lint against a broken install produces misleading reports.

---

## No-arg mode: recommendation heuristic

When invoked without an argument, lint reads `log.md` to compute a recommendation before prompting:

1. **Finds the baseline** — the most recent `## [YYYY-MM-DD] lint | comprehensive | ...` entry. Only comprehensive lint runs count; bootstrap-consistency and other operations do not.
2. **Counts wiki-mutating activity** since that baseline — `ingest`, `session-capture`, `manual-update`, and `bootstrap-task` entries in `log.md`.
3. **Recommends Comprehensive** if any of:
   - No prior comprehensive run exists (baseline is "never").
   - Days since the baseline exceed `lint.comprehensive-recommended-after-days` (default `14`).
   - Activity count is at or above `lint.comprehensive-recommended-after-activity` (default `20`).
4. Otherwise recommends **Quick**.

The choice prompt shows concrete numbers (last comprehensive date, days elapsed, activity counts) so the user can sanity-check the recommendation.

---

## Quick checks (both modes run these — Phase 4)

### Check 1 — Orphan pages
A page is orphaned if no editorial page links to its slug. Category index files are not counted as editorial links. The four fixed-category indexes, `llake/index.md`, and monorepo group indexes are exempt — they are never expected to have inbound links.

**No auto-fix.** Wiring a link into another page requires understanding content — that is what Comprehensive is for. Orphans are flagged in the report.

### Check 2 — Broken wikilinks
Every `[[slug]]` in the wiki is checked against the page inventory. A missing target slug is flagged as broken, recording source page and target slug.

**No auto-fix.** The right target (if any) requires reading both pages.

### Check 3 — Index sync
Two sub-checks:
- Every non-index page in `wiki/<cat>/` must be referenced (as `[[slug]]`) in `wiki/<cat>/<cat>.md`. **Auto-fix:** appends a `- [[slug]] — <description>` line to the category index if the reference is missing.
- Every `[[slug]]` in a category index must have a corresponding file. If missing, it is **flagged for the report** — lint never silently removes an index line.

### Check 4 — Frontmatter shape
Validates required fields (`title`, `description`, `tags`, `created`, `updated`, `status`), ISO date formats for `created`/`updated`, that `tags` is a YAML list, that `related` (if present) is a list of `[[slug]]`-shaped strings, and that `status` is one of `current`, `draft`, `stale`, `deprecated`, `immutable`.

**Auto-fix only:** normalizes `related:` when written as a scalar string (`related: "[[foo]]"`) to the correct list form. All other frontmatter issues are flagged without modification — auto-filling missing dates or inventing titles would degrade content quality.

### Check 5 — Credential regex scan
Scans every page body for credential-shaped patterns:
- High-entropy strings (≥32 chars from `[A-Za-z0-9+/=_-]`, Shannon entropy ≥ 4.5 bits/char)
- JWT shape (`eyJ...eyJ...`)
- AWS access keys (`AKIA` / `ASIA` prefixes)
- GCP service-account key snippets
- GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` prefixes)
- Connection strings with embedded credentials (`:...@` pattern for postgres, mysql, mongodb, redis, amqp, etc.)

**No auto-redact.** A false positive silently mangling content is worse than a true positive surfaced for the user. Findings tell the user which page, which line, and which pattern to abstract per Standard 3.

---

## Comprehensive checks (Comprehensive mode only — Phase 5)

Comprehensive dispatches **read-only diagnosis subagents** per wiki shard (typically one per category, split when a category exceeds ~12 pages). Subagents return JSON findings manifests; the orchestrator aggregates them and runs two cross-shard checks itself.

### Check A — Stale pages (per shard)
Pages with an `updated:` date older than `lint.stale-threshold-days` (default `30`) are flagged as stale if the source files they describe have git commits since that date. Pages in the four fixed-category directories are always exempt from the stale check.

### Check B — Content gaps (per shard, reconciled cross-wiki)
Concepts referenced in page body text that lack a wiki page. Local findings from each shard are merged and deduplicated; the orchestrator drops false positives by checking the full wiki inventory.

### Check C — Standard 1 coherence (per shard)
Applies the new-employee test from `schema/code-content-standard.md`: a new contributor reading the page plus one or two wikilinks away must be able to start work without flagging a senior engineer. Pages that are pure structure dumps or that propagate unstated assumptions are flagged.

### Check D — Sensitive content beyond regex (per shard)
Contextual scan for content that passes the mechanical regex but still leaks sensitive data: internal hostnames, customer names, personal data, specific authentication-bypass URLs, internal IP addresses. Subagents describe the kind of leak without quoting the value.

### Check E — Contradictions (orchestrator-owned, cross-shard)
Pages making conflicting claims about the same concept: different function signatures, contradictory invariants, two pages both claiming to be the canonical home. The orchestrator records `(page-1, page-2, conflict description)` — resolution is not automatic because the right answer requires reading the source.

### Check F — Terminology drift (orchestrator-owned, cross-shard)
Identifies the same concept named differently across pages (`tick loop` vs `main loop`, `worker pool` vs `worker queue`). Records the canonical candidate, variants, and affected pages. No auto-rewrite — the canonical form is a judgment call.

### Review and repair
Findings are presented to the user category by category via `AskUserQuestion`. For each category:
- **Repair all** — dispatch repair subagents for all findings in that category.
- **Selective** — pick per-finding.
- **Defer** — record in the log entry, make no changes.

Repair subagents have a narrowly scoped write surface (only the specific slugs being repaired, never the four fixed-category directories). They can update stale pages, write new pages for content gaps, resolve contradictions, normalize terminology, expand Standard 1 failures, and abstract sensitive values.

---

## Config keys (all optional)

Read from `llake/config.json`, falling back to `templates/config.default.json`. See [[config-schema]] for the full key reference.

| Key | Default | Effect |
|---|---|---|
| `lint.model` | `"sonnet"` | Model for Comprehensive diagnosis and repair subagents |
| `lint.effort` | `"high"` | Reasoning effort for Comprehensive subagents |
| `lint.comprehensive-recommended-after-days` | `14` | Days since last comprehensive run that push the no-arg recommendation to Comprehensive |
| `lint.comprehensive-recommended-after-activity` | `20` | Activity count (ingests + captures + etc.) since last comprehensive run that push the recommendation |
| `lint.stale-threshold-days` | `30` | Age threshold for Comprehensive stale check; pages in fixed-category dirs always exempt |

`lint.model` and `lint.effort` apply **only to Comprehensive subagents**. Quick runs in-session under the parent session's model — no config knobs needed there.

---

## Log entry and report

Both modes write exactly one `lint` entry to `log.md` in Phase 6. The format is:
```markdown
## [YYYY-MM-DD] lint | quick | 3 issues found, 2 fixed, 1 deferred
```
or
```markdown
## [YYYY-MM-DD] lint | comprehensive | 11 issues found, 7 fixed, 4 deferred
```

The mode token (`quick` or `comprehensive`) must immediately follow `lint |` — the no-arg recommendation heuristic greps for this exact shape when finding the most recent comprehensive baseline.

The final report follows a consistent `[CHECK]` structure mirroring the one `/llake-doctor` uses:
```
LoreLake Lint — quick — Complete

Project:          /path/to/project
Scope:            42 pages across 6 categories
Runtime:          1m 12s

[CHECK] Orphan pages              : 1 flagged
[CHECK] Broken wikilinks          : OK
[CHECK] Index sync                : 2 fixes applied
[CHECK] Frontmatter shape         : OK
[CHECK] Credential regex          : OK

Summary: 3 issues, 2 fixed, 1 deferred.
Deferred issues recorded in log.md.
```

---

## Key Points

- Quick is cheap and in-session; Comprehensive is expensive and orchestrated — choose based on how much wiki activity has happened since the last comprehensive run.
- Lint never edits `wiki/discussions/**`, `wiki/decisions/**`, `wiki/gotchas/**`, or `wiki/playbook/**`. It reads them for link resolution only.
- Only two Quick checks auto-fix: index-sync appends (Check 3) and `related:` string normalization (Check 4). Everything else is flagged for the user.
- Comprehensive diagnosis subagents are read-only — only confirmed repair subagents write.
- Exactly one `log.md` entry is written per run, regardless of how many repairs were made.
- The no-arg recommendation is driven by elapsed days and activity count. Both thresholds are config-tunable.

---

## Code References

- `skills/llake-lint/SKILL.md` — full lint spec (all phases, check logic, subagent prompt templates, write surface)
- `schema/core.md` — page format, slug naming, frontmatter requirements
- `schema/code-content-standard.md` — Standard 1 (new-employee test) and Standard 3 (security, regex + contextual)
- `schema/conversation-content-standard.md` — Standard 2 (why discussions are never edited by lint)
- `hooks/lib/detect-project-root.sh` — project root detection contract
- `hooks/lib/read-config.py` — config key lookup with fallback to defaults
- `templates/config.default.json` — default values for all `lint.*` config keys

---

## See Also

- [[llake-doctor-skill]] — repairs install drift; run doctor if lint refuses due to missing `config.json` or `log.md`
- [[llake-bootstrap-skill]] — initial wiki population; lint assumes bootstrap has already run
- [[config-schema]] — `lint.*` config keys and all other project config knobs
