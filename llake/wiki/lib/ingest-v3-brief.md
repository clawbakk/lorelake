---
title: "Ingest v3 Inputs, Brief and Bundles"
description: "The $0 inputs (removed names, hit/anchor/literal indexes, patches), the analysis and recall agents, brief assembly and validation, and deterministic bundling"
tags:
  - "lib"
  - "ingest"
  - "v3"
  - "analysis"
  - "brief"
  - "python"
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[ingest-v3-writers]]"
  - "[[ingest-v3-gap-record]]"
  - "[[ingest-v3-templates]]"
  - "[[ingest-v3-run-planning]]"
---
# Ingest v3 Inputs, Brief and Bundles

## Overview

Before any writer runs, a v3 run builds a **brief**: the set of wiki pages the range made stale, each with the exact stale statements quoted from the page and the new facts that belong there. Four modules produce it:

- `names.py` derives $0 leads from the diff.
- `stage.py` stages the range for the agents and renders the analysis and recall prompts.
- `brief.py` validates and assembles what the agents wrote.
- `bundle.py` groups pages into writer bundles.

The brief is the contract between finding and writing in [[ingest-v3-pipeline]].

## Stage inputs ($0)

All inputs land in `<agent dir>/inputs/`:

| File | From | Content |
|---|---|---|
| `names.json` | `names.derive` | `removed`, `removed_files`, `added`, `literals`, `literal_lines` |
| `hits.json`, `hit-index.md` | `names.wiki_hits` | pages naming a removed name, by line (index capped at 20 lines per page) |
| `anchor-hits.json`, `anchor-index.md` | `names.anchor_hits` | page anchors `path:line` that point into base lines the range removed or rewrote |
| `literal-hits.json`, `literal-index.md` | `names.literal_hits` | pages quoting a message-like string literal from a removed line |
| `commits.md`, `numstat.txt` | `stage.stage_inputs` | commit messages oldest first; numstat of the range |
| `patches/` | `stage.stage_patches` | one patch per changed file |
| `catalog.md` | `wiki.catalog_md` | every non-discussion page with its `description:` |

Every git call is limited to `ingest.include` and uses `core.quotePath=false`, so non-ASCII paths stay readable.

### Removed and added names

The tokens on removed (`-`) lines of `git diff -U0 -M base head` are of two kinds:

- **Simple names**: identifiers of at least 4 characters that contain an internal capital, an underscore or a digit, or are ALLCAPS.
- **Compound names**: identifier parts joined by `.` or `-`.

A token is **removed** when it has zero word-boundary matches in the watched source at head. The basename of a file deleted or renamed away is also removed, unless that basename still exists at head. **Added** names are the mirror image: names that appear on added lines and nowhere at base. A name that is still used at head is never a lead.

Compound-name matching treats a trailing `.` that is not followed by a word character as a sentence end. `config.v2.` at the end of a sentence still matches `config.v2`.

**Literals** are string literals on removed lines with at least 10 characters. After `${...}` holes are split out and the fragment is trimmed, it must be at least 12 characters long, contain a space, hold at least two words, be at least 90% prose characters, and not start with `http`, `/` or `@`. Each literal is `removed` (gone at head) or `changed` (still at head, but a line carrying it changed).

### Anchor hits

`changed_ranges` reads the old-side line ranges of each hunk; pure insertions have zero length and are ignored. An anchor on a wiki page counts as a hit when it resolves to exactly one changed file by suffix or basename and its lines overlap a changed range. An anchor whose line number merely shifted is not a hit.

### Patches

There is one patch per changed file, named after the path with `/` replaced by `__` (`src/a/b.ts` → `src__a__b.ts.patch`). A rename or copy is diffed with both its old and new path, so the pair stays together. Paths are passed as `:(literal)` pathspecs, so a file name containing glob characters matches only itself. A patch over 60000 bytes is split at hunk boundaries into `.patch.part1`, `.patch.part2`, and so on.

## The analysis agent

`stage.analysis_prompt` renders `ingest.v3.analysis.md.tmpl` (see [[ingest-v3-templates]]) with the range, include paths, commits, numstat, removed and added names, the three indexes and the pages already owed. The agent runs with `analysisModel`/`analysisEffort`/`analysisBudgetUsd`. It has `Read`, `Glob`, `Grep`, `Edit` and `Write`, with write permission only under `<agent dir>/brief/`. Its working directory is the working tree at head. It writes:

- `themes.json`: change themes, one idea each, with commit SHAs.
- `pages/batch-<n>.json`: arrays of page entries, written in batches of about 5 to 10. Each entry has `path`, `kind` (`direct|thematic|index|new`), `severity`, `themes`, `reason`, `stale` claims (`quote`, `head`, `severity`), `newFacts` and `evidence`.
- `considered.json`: pages examined and ruled out.
- `files.json`: every changed file accounted for.
- `notes.json`: anything the agent could not verify.

The prompt sets the rules for claims:

- A `quote` is a verbatim 5-to-25-word fragment from one page line.
- Every major claim gets its own entry, including each restatement elsewhere on the page.
- At most 2 minor claims per page.
- Each new fact has exactly one home page.
- A new page needs an existing category, `title` and `description`.
- A decision record is listed when the range implements, reverses or supersedes it, or makes one of its current-tense facts false.
- A category index is listed only if its blurb became false.
- `llake/index.md` and `discussions/` are out of scope.

A failure of any class except `none` holds the run (`HoldRun`). Only `work` failures count toward splitting the range.

## The recall agent

Recall is skipped when `recallPass` is `"off"`, when no watched change was found (`gap-only` and empty split midpoints), or when `spent + recallBudgetUsd` exceeds `maxRunBudgetUsd`. Otherwise `stage.recall_prompt` renders `ingest.v3.recall.md.tmpl` with the themes, the pages already listed (including state pages that the hit index adds automatically) and the considered pages. The agent looks for pages the brief missed, and may write only under `brief/pages/`.

Code then enforces a narrower rule: **recall can only add `recall-*.json` files**.

- Every file analysis wrote is restored byte for byte.
- Any new file that is not named `recall-*`, that came from a failed recall, or that fails the `brief-page` schema is moved to `brief/rejected-recall/`.

A recall failure of any kind never fails the run.

## Brief assembly — `brief.assemble`

1. **Validate.** `themes.json` is checked against `brief-themes.json`, and every `pages/*.json` entry against `brief-page.json`. Problems in analysis files are errors: `InvalidBrief` holds the cursor and counts as a work failure. Problems in `recall-*` files are only warnings.
2. **Normalize.** The root `index.md`/`log.md`, in any spelling (absolute, `./`, `llake/`, `wiki/`), is dropped with a warning. Paths are normalized to `wiki/...md`. A `new` entry without `title` and `description` is an error. A page is `major` if any of its claims is.
3. **Filter and merge.** Excluded pages are dropped, as are non-existent pages not marked `new` and new pages whose category has no index (these are noted in the log entry). Entries for the same page merge: claims are deduplicated by normalized quote, and themes, evidence and facts are unioned.
4. **Check quotes.** Each claim gets `quoteFound`, which says whether its quote is really on the page.
5. **Sweep.** Every page in `hits.json` that the brief omits is added. A `state` page is auto-added as a minor `sweep` entry, with claims taken from its hit lines (source `check:removed-name`). A `record` or `gotcha` page that is also missing from `considered.json` is added as *unaccounted*.
6. **Carry gaps.** Owed gaps are merged in from the gap record: all open gaps for a range run, major only for `gap-only` (see [[ingest-v3-gap-record]]).
7. **Separate blurbs.** Index pages move out to `blurbs`. They are never dispatched and become `blurb` gaps at finalize.
8. **Annotate.** Each page gets `create` (the file does not exist yet) and `weight_kb`.

Assembly writes `brief.json` and `brief-report.json`, which holds counts, warnings, auto-added and unaccounted pages, carried and dropped gaps, and skipped stuck gaps.

## Bundling — `bundle.make_bundles`

Bundling is deterministic. Each page weighs its size in KB plus its number of claims.

- **Major tier first**, then minor. If the run cap runs out, minor work is what gets dropped.
- **Order within a tier:** carried before fresh, carried pages by oldest `since` (ranked with `git rev-list --count`), new pages last, then more claims first.
- Each bundle grows from its seed by **affinity**: 2 points per shared theme plus 1 per shared evidence file. A `record` page that links, at base, to a page already in the bundle scores +10 and may join from the minor tier. That way one writer both marks the record and corrects the page it links to.
- `bundleMaxPages` and `bundleMaxWeight` always hold. A seed at or above the weight cap goes alone.
- Bundles are numbered `b01`, `b02`, …; the order is the dispatch order.

## Key Points

- Removed names, anchor hits and literal hits are mechanical leads costing $0. Analysis confirms them; it does not trust them.
- Each claim carries a verbatim quote, which code checks against the page and later uses to verify that the writer removed it.
- State pages that name a removed name join the brief automatically, and decisions or gotchas that do must be accounted for.
- Recall can only add `recall-*` files and can never invalidate the brief.
- Invalid analysis output holds the cursor and counts toward splitting the range.
- Bundles put major work first and keep related pages, including a record and the page it links to, with one writer.

## Code References

- `hooks/lib/ingest_v3/names.py:106` — `derive`, removed/added names and literals
- `hooks/lib/ingest_v3/names.py:267` — `write_inputs`
- `hooks/lib/ingest_v3/stage.py:34` — `stage_patches`
- `hooks/lib/ingest_v3/stage.py:61` — `stage_inputs`
- `hooks/lib/ingest_v3/stage.py:95` — `analysis_prompt`
- `hooks/lib/ingest_v3/stage.py:114` — `recall_prompt`
- `hooks/lib/ingest_v3/run.py:172` — `_recall`, the recall-file rule
- `hooks/lib/ingest_v3/brief.py:101` — `assemble`
- `hooks/lib/ingest_v3/bundle.py:32` — `make_bundles`
- `tests/lib/test_v3_names.py`, `tests/lib/test_v3_stage.py`, `tests/lib/test_v3_brief.py`, `tests/lib/test_v3_bundle.py`

## See Also

- [[ingest-v3-pipeline]] — the whole run
- [[ingest-v3-writers]] — what consumes the brief and bundles
- [[ingest-v3-gap-record]] — carried gaps
- [[ingest-v3-templates]] — the analysis and recall prompts
