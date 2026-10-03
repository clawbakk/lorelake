---
title: "Ingest v3 Gap Record"
description: "llake/ingest-gaps.json — the pages a v3 run could not bring current, with causes and quoted stale claims, plus skipped ranges; carried into the next run"
tags: [lib, ingest, v3, gaps, python]
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[ingest-v3-finalize]]"
  - "[[ingest-v3-brief]]"
  - "[[ingest-gate]]"
  - "[[llake-doctor-skill]]"
  - "[[llake-bootstrap-skill]]"
  - "[[runtime-layout]]"
  - "[[ingest-v3-run-planning]]"
  - "[[ingest-v3-orchestrator]]"
  - "[[post-merge-hook]]"
  - "[[ingest-v3-writers]]"
  - "[[enable-ingest-v3]]"
---
# Ingest v3 Gap Record

## Overview

`llake/ingest-gaps.json` records the debt ingest v3 leaves behind. It lists every page a run could not bring current, with the reason and the stale statements still on the page, plus every commit range ingest skipped. The cursor advances over recorded gaps, and the next run takes them on first. The record is tracked in git, so every clone sees the same debt. `hooks/lib/ingest_v3/gaps.py` loads, validates, carries and recomputes it. Its schema is `hooks/lib/ingest_v3/schemas/gap-record.json`.

Only v3 reads or writes it. Legacy and v2 ignore it. `/llake-bootstrap` resets it to an empty record. `/llake-doctor` validates it and never repairs it.

## Format

```json
{
  "version": 1,
  "asOf": "<head sha of the run that wrote it>",
  "agent": "<agent id>",
  "date": "YYYY-MM-DD",
  "gaps": [
    {
      "page": "wiki/lib/example.md",
      "severity": "major",
      "cause": "writer-failed",
      "since": "<sha at which the gap was first recorded>",
      "attempts": 1,
      "stuck": false,
      "claims": [
        {"quote": "verbatim text still on the page", "head": "what is true at head, with path:line", "severity": "major", "source": "brief"}
      ]
    }
  ],
  "ranges": [
    {"base": "<sha>", "head": "<sha>", "cause": "analysis-failed", "leads": ["wiki/lib/example.md: `oldName` L12"]}
  ]
}
```

`page` is relative to `llake/`. Each gap has at least one claim, and each claim's `quote` must be text that is on the page now. Gaps are sorted major first, then by page.

## Causes

| Cause | Meaning | Counts as an attempt |
|---|---|---|
| `declared` | the writer declared a gap on minor claims it left (`claimsLeft`) | yes |
| `flagged` | still flagged after checks/fix, a fixer that did not succeed, or another writer's `otherStale` report | yes |
| `writer-failed` | the bundle and the single-page retry both failed | yes |
| `reverted` | the write was reverted (broken frontmatter) or the page could not be restored | yes |
| `run-cap` | never dispatched; would have exceeded `maxRunBudgetUsd` | no |
| `timeout` | the run deadline passed before or during the write | no |
| `infra` | an infrastructure failure stopped dispatch | no |
| `unverified` | the verifier was on but did not check this page's rewrite | no |
| `blurb` | a category index's prose is false at head (indexes are never dispatched) | no |

Claim sources: `brief`, `verifier:residual`, `verifier:accuracy`, `check:removed-name`, `check:quote-residue`, `check:anchor`, `writer:other`.

## Attempts and stuck

`attempts` grows by one each time a page fails with a cause that counts. At 3 (`STUCK_ATTEMPTS`) the gap is `stuck`. The validator requires `stuck` to equal `attempts >= 3`. A stuck gap is never dispatched again and never forces an ingest run, but **it stays in the record** with its claims re-checked against the page, until a human edits the page and removes the entry, or deletes the page. `ranges` entries likewise accumulate until a human removes them.

## How a run uses it

- **Gate.** `owed_major` is true when any open (non-stuck) gap is `major`. Under v3 that turns a `WAIT` into `RUN reason=gaps` and an `EMPTY` into `RUN reason=gaps-only` (see [[ingest-gate]]).
- **Analysis.** The analysis prompt lists the owed pages and tells the agent not to re-derive them (see [[ingest-v3-brief]]).
- **Carry.** `gaps.carried` turns open gaps into brief entries for a range run (all open gaps) or a gap-only run (major only). Stuck gaps are skipped. Gaps whose page no longer exists are dropped. Each carried claim **keeps its `source`**: a `check:removed-name` claim stays exempt from the quote-residue check, and `writer:other` provenance survives.
- **Writers** see carried pages with their `since` and failed-attempt count and re-verify the claims at head.

## Computing the next record — `next_record`

At finalize, each page the run handled gets a result based on its outcome (see [[ingest-v3-finalize]]):

- `corrected` / `no-change`: a gap only if flags are still on the page (`flagged`, severity from the flags), or the verifier was on and did not check it (`unverified`, minor). Otherwise a previously owed page counts as **resolved**.
- `flagged` (fixer did not succeed): a `flagged` gap.
- `declared`: a `declared` gap with the `claimsLeft` still on the page, falling back to the brief claims.
- `writer-failed`, `reverted`, `run-cap`, `timeout`, `infra`: a gap with that cause and the brief claims still on the page.

After that, three more sources feed the record:

- **Other writers' reports.** `otherStale` reports on pages outside the run (excluded and index pages ignored) become `flagged` gaps, merged with any existing gap for the page. The attempt count is kept, not incremented.
- **Blurbs.** Index pages whose prose is false become minor `blurb` gaps.
- **Untouched gaps.** Previous gaps on pages the run did not handle are kept, with claims filtered to quotes still on the page.

Two rules apply throughout:

- A claim is kept only if its quote, with whitespace normalized, is on the page. When none is, a **placeholder claim** quotes the page's `description:` (or its first non-empty line) and explains why the page is owed. A page that is empty gets no gap, and a note says so.
- `since` comes from the earlier gap, else the brief entry, else this run's head.

`ranges` = previous ranges + new ones.

## Validation

`gaps.validate(doc, llake_root)` checks:

- the schema;
- that every `page` is a normalized wiki path;
- that no page appears twice;
- that `stuck` agrees with `attempts`;
- when `llake_root` is given, that each page exists and each quote is on its page.

`ingest-v3.py validate-gaps` prints one of:

- `ABSENT`;
- `OK: N gaps (K major, S stuck), R skipped ranges`, followed by one `STUCK:` line per stuck gap and one `RANGE:` line per skipped range;
- `INVALID: <first error>`, followed by indented further errors, with exit 1.

A human edit, such as deleting a page or rewording a quoted sentence, makes the file `INVALID` with `page ... does not exist` or `quote not on the page`. That is not corruption. The next v3 run drops the deleted page and uses a placeholder for the missing quote. `/llake-doctor` Check 8.6 reports it as self-healing. `report.md` also re-validates the record after every run.

## Key Points

- The record is committed, rewritten whole by each v3 run, and read only by v3.
- Every claim quotes text that is on the page now. Code re-checks that on every run.
- Failures that count as attempts are `declared`, `flagged`, `writer-failed` and `reverted`. Three of them make a gap stuck. Stuck gaps and skipped ranges need a human, who removes them from the file by hand.
- Open major gaps force v3 runs, even on merges the schedule would defer or that changed nothing watched.
- Carried claims keep their source, so exemptions and provenance survive across runs.

## Code References

- `hooks/lib/ingest_v3/gaps.py:9` — `CAUSES`, `ATTEMPT_CAUSES`, `SOURCES`
- `hooks/lib/ingest_v3/gaps.py:39` — `validate`
- `hooks/lib/ingest_v3/gaps.py:69` — `owed_major`
- `hooks/lib/ingest_v3/gaps.py:77` — `carried`
- `hooks/lib/ingest_v3/gaps.py:133` — `next_record`
- `hooks/lib/ingest_v3/schemas/gap-record.json` — the schema
- `hooks/lib/ingest_v3/cli.py:14` — `validate-gaps` output
- `skills/llake-doctor/SKILL.md` — Check 8.6
- `skills/llake-bootstrap/SKILL.md` — Phase 6 reset
- `tests/lib/test_v3_gaps.py` — carry, next record, validation, schema enum pinning

## See Also

- [[ingest-v3-finalize]] — where the record is written
- [[ingest-gate]] — the owed-gap override
- [[llake-doctor-skill]] — Check 8.6
- [[llake-bootstrap-skill]] — the reset in Phase 6
- [[enable-ingest-v3]] — handling stuck gaps and skipped ranges
