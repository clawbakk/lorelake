---
name: llake-lint
description: Use when auditing wiki integrity or content quality — after bulk ingests, when the wiki feels stale, or before a critical decision. Invoked via /llake-lint [quick|comprehensive].
disable-model-invocation: true
---

# LoreLake Lint — Wiki Integrity & Content Quality

The user invoked `/llake-lint` (optionally with an argument). Your job is to audit the project's LoreLake wiki, repair mechanical issues in place, and surface content-level issues for the user to decide about.

Two modes exist, and the split is deliberate:

- **Quick** runs five mechanical, deterministic checks over the wiki's filesystem layout and frontmatter. No subagent dispatch. Cheap enough to run often — the recommended cadence is once a week or two, or anytime the wiki "feels stale."
- **Comprehensive** does everything Quick does, then dispatches read-only subagents in parallel to audit each shard of the wiki against its source code. Expensive enough to warrant a recommendation and a confirmation. The typical cadence is monthly, or before relying on the wiki for a critical decision (merging a big PR, onboarding a new engineer).

Quick is in-session work you do directly. Comprehensive makes you an **orchestrator** that dispatches diagnosis subagents, aggregates their findings, asks the user which findings to repair, then dispatches scoped repair subagents for the confirmed ones. The orchestrator owns every write; diagnosis subagents are read-only.

A lint run never edits `wiki/discussions/**`. Those pages are owned by session-capture and their Key Facts blocks are immutable by Standard 2 — lint may read them for link resolution but never writes. Similarly, lint never touches `schema/**`, `config.json`, or anything outside `<project>/llake/`.

---

## Resolve paths before doing anything

Two absolute paths drive every phase. Compute them once at the start; use them literally thereafter.

- **`$PLUGIN_ROOT`** — where this skill lives. Claude Code exposes the skill's base directory at invocation (the `skills/llake-lint/` directory). `$PLUGIN_ROOT` is two levels up from there. If you are unsure, resolve `realpath` against the SKILL.md path you were given and strip `/skills/llake-lint/SKILL.md`.

- **`$PROJECT_ROOT`** — resolve in this order, matching the contract documented in `$PLUGIN_ROOT/hooks/lib/detect-project-root.sh`:
  1. `$LLAKE_PROJECT_ROOT` environment variable, if set and non-empty.
  2. **Marker walk** — ascend from `pwd` looking for the first ancestor directory that contains `llake/config.json`.
  3. **Git toplevel** fallback — `git -C "$PWD" rev-parse --show-toplevel`. Only accept the result if `<toplevel>/llake/config.json` exists.
  4. If none yield a valid install: stop. Emit exactly: "No LoreLake install found near `<pwd>`. Run `/llake-lady` to install." Do not proceed.

Echo both resolved paths to the user in a single line before Phase 1 so a wrong CWD is caught before any file is written.

---

## Phase 1 — Parse the invocation

Lint has three invocation paths, and they behave differently. Detecting which one is active is the first step of the skill's flow.

Inspect the user's invocation message. Strip the leading `/llake-lint` and any surrounding whitespace; whatever remains is the mode argument.

| Argument | Mode | Behavior |
|---|---|---|
| *(empty)* | `no-arg` | Read `log.md`, compute a recommendation, and present the choice prompt. Phase 2 handles this. |
| `quick` | `quick` | Run Quick immediately. Skip the log-read and the choice prompt — Quick is cheap and needs no context beyond the current wiki state. |
| `comprehensive` | `comprehensive` | Skip the choice prompt but still show a one-time expense warning and require confirmation. No log read on this path either — the user has already chosen comprehensive; the recommendation context would be noise. |
| *(anything else)* | invalid | Stop. "Unrecognized argument: `<arg>`. Valid forms: `/llake-lint`, `/llake-lint quick`, `/llake-lint comprehensive`." |

Argument matching is case-insensitive and ignores leading/trailing whitespace.

### Validate the install

Regardless of mode, confirm the install is healthy enough for lint to operate:

1. `$PROJECT_ROOT/llake/config.json` must exist and parse as JSON. If missing: stop with "No LoreLake install found at `$PROJECT_ROOT`. Run `/llake-lady` first." If unparseable: stop with "`<path>/llake/config.json` is not valid JSON. Run `/llake-doctor` to repair, then re-run `/llake-lint`."
2. `$PROJECT_ROOT/llake/wiki/` must exist. If missing: stop with "Wiki directory missing. Run `/llake-doctor` first."
3. `$PROJECT_ROOT/llake/log.md` must exist. If missing: stop with "`llake/log.md` missing. Run `/llake-doctor` first." (Log-read only happens in no-arg mode, but every mode will try to *append* one entry at the end, and the file must be there for that.)

These preconditions are deliberately strict. Lint is a diagnostic tool; running against a broken install would produce misleading reports.

---

## Phase 2 — Determine the mode

What this phase does depends on which invocation path Phase 1 selected.

### No-arg path: read `log.md`, recommend, prompt

1. Read `$PROJECT_ROOT/llake/log.md`.

2. **Find the baseline.** Scan for the most recent entry whose heading matches `^## \[YYYY-MM-DD\] lint \| comprehensive`. This is the baseline. **Do not count `bootstrap-consistency` or any other operation as a baseline** — the recommendation heuristic only trusts lint's own prior *comprehensive* runs. If no such entry exists, treat the baseline as "never."

3. **Count wiki-mutating activity since the baseline.** Walk the log from the baseline (exclusive) to the end; count entries whose operation token (the first word after `## [YYYY-MM-DD]`, up to `|`) is one of: `ingest`, `session-capture`, `manual-update`, `bootstrap-task`. Everything else — including `bootstrap`, `bootstrap-consistency`, and prior `lint` runs — does not count.

4. **Compute the recommendation.** Read `lint.comprehensive-recommended-after-days` (default `14`) and `lint.comprehensive-recommended-after-activity` (default `20`) via `$PLUGIN_ROOT/hooks/lib/read-config.py`. Recommend **Comprehensive** if ANY of:
   - Baseline is "never."
   - Days since the baseline entry's date are greater than `lint.comprehensive-recommended-after-days`.
   - Activity count is greater than or equal to `lint.comprehensive-recommended-after-activity`.

   Otherwise recommend **Quick**.

5. **Present the choice** via `AskUserQuestion`. Use this layout for the question body — keep the numbers concrete so the user can sanity-check the recommendation:

   ```
   LoreLake Lint

   Last comprehensive lint:  <date> (<N> days ago)   (or "never")
   Activity since then:      <X> ingests, <Y> session-captures, <Z> manual updates,
                             <W> bootstrap-tasks (<total> total)

   Recommendation: <mode> — <reason>

   Note: Comprehensive reads every wiki page and the source it describes,
   dispatches subagents in parallel, and typically runs for several minutes
   to tens of minutes depending on wiki size.
   ```

   Options, in order (recommended first):

   - **Quick** or **Comprehensive** *(recommended)* — run the recommended mode.
   - The other of Quick/Comprehensive.
   - **Cancel** — abort before any changes.

   Selecting `Cancel` stops the skill immediately with no log entry.

### `quick` path: go directly

Skip the log read and the prompt. Mode is `quick`.

### `comprehensive` path: one-time warning

Skip the log read and the recommendation computation, but still require confirmation. Ask the user via `AskUserQuestion`:

> "Comprehensive lint reads every wiki page and the source each describes, dispatches subagents in parallel, and typically runs several minutes to tens of minutes depending on wiki size. Proceed?"

Options, in order:
- **Proceed** — run Comprehensive.
- **Cancel** — abort.

`AskUserQuestion` auto-adds an "Other" option; treat anything other than explicit `Proceed` as abort.

---

## Phase 3 — Build the page inventory

This phase runs for both modes. Comprehensive builds on top of it; don't duplicate the work.

Glob `$PROJECT_ROOT/llake/wiki/**/*.md`. For each page file, build an inventory record:

- **Slug** — filename without `.md`. Slugs are globally unique across the wiki (see `$PLUGIN_ROOT/schema/core.md`).
- **Path** — absolute path.
- **Category** — path segment immediately under `wiki/` (or the sub-category under a monorepo grouping dir, e.g., `packages/apposum` yields category `packages/apposum`).
- **Frontmatter** — parsed YAML. Record presence of required fields: `title`, `description`, `tags`, `created`, `updated`, `status`, `related`.
- **Outbound `[[wikilinks]]`** — every `[[slug]]` match in the body. Record target slugs.
- **Inbound count** — computed after all pages are parsed; the number of other pages whose outbound links include this slug.

Category index files (`wiki/<cat>/<cat>.md`) are inventory records too — they have frontmatter and participate in the orphan-check as links-target-and-links-source. But they're also the registry that page-to-category-index-sync (Quick check 3) verifies against; treat them as a distinct artifact when performing that check.

The inventory is the foundation every check below runs against. Build it once and keep it in session memory.

---

## Phase 4 — Quick checks (all modes run these)

Walk these five checks in order against the Phase 3 inventory. Record findings in a structured result set; apply **only** the purely additive mechanical fixes inline (fixes 3 and 4 below). Everything else — including borderline cases — goes in the report for the user to see, without writes.

### Check 1 — Orphan pages

A page is orphaned if no other page's outbound wikilinks point at its slug. Category index files (`wiki/<cat>/<cat>.md`) are not counted as "other pages" — their links are bookkeeping, not editorial — so an index link alone does not save a page from orphan status.

Exceptions: the four fixed-category index files themselves (`discussions.md`, `decisions.md`, `gotchas.md`, `playbook.md`), the root `index.md`, and any monorepo group index (`packages/packages.md`, etc.) are exempt. They are never expected to have inbound links.

Flag every orphaned page in the report. No auto-fix — wiring a link into some other page would require understanding the content, which is exactly what Comprehensive is for.

### Check 2 — Broken wikilinks

For every outbound `[[slug]]` recorded in the inventory, verify there is a page whose slug matches. If no match: flag as broken, recording `(source page, target slug)` for the report. No auto-fix — the right target (if any) requires reading both pages.

### Check 3 — Index sync

Two sub-checks reconcile pages against their category indexes:

- For every page `wiki/<cat>/<slug>.md` that is NOT itself a category index, confirm `wiki/<cat>/<cat>.md` contains a line referencing `[[<slug>]]` (with any surrounding bullet/description formatting). If missing: **auto-fix by appending a line to the category index** of the form:

  ```
  - [[<slug>]] — <one-line description from the page's `description:` frontmatter>
  ```

  If the page lacks a `description:` field, append `- [[<slug>]]` with no description and also flag the missing frontmatter under Check 4.

- For every `[[<slug>]]` reference in a category index, confirm a file `wiki/<cat>/<slug>.md` exists. If missing: flag for the report. **Do not auto-remove the index line** — the user may have renamed or relocated the page, and the right answer is for the user to fix the link, not for lint to silently drop evidence.

Record "N index-sync entries appended" in the report. Appending is safe because it's additive — the file still works if the newly-added line is redundant.

### Check 4 — Frontmatter shape

For every page, validate:

- Required fields present: `title`, `description`, `tags`, `created`, `updated`, `status`. (`related` is optional — pages without relations are fine.)
- `created` and `updated` parse as ISO dates (`YYYY-MM-DD`).
- `tags` is a YAML sequence (list), not a string.
- `related`, if present, is a YAML sequence of `[[slug]]`-shaped strings.
- `status` is one of `current`, `draft`, `stale`, `deprecated`, `immutable`.

**Auto-fix only** the structural-drift case where `related:` is written as a string (`related: "[[foo]]"`) instead of a list. Normalize to:

```yaml
related:
  - "[[foo]]"
```

Everything else (missing fields, unparseable dates, unknown `status`) goes in the report without modification. Auto-filling a missing date or inventing a missing title would degrade content quality.

### Check 5 — Credential regex scan

Grep every page body (not frontmatter — frontmatter is bookkeeping) for these patterns, per Standard 3:

- **High-entropy strings** — contiguous runs of ≥32 characters from `[A-Za-z0-9+/=_-]` with Shannon entropy ≥ 4.5 bits/char. A practical proxy: the string contains at least 12 distinct characters and has no vowels in natural positions.
- **JWT shape** — `eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`.
- **AWS access key** — `AKIA[0-9A-Z]{16}` or `ASIA[0-9A-Z]{16}`.
- **GCP service-account key snippets** — `"type":\s*"service_account"` near a `"private_key"` field.
- **GitHub tokens** — `ghp_[A-Za-z0-9]{36,}`, `gho_[A-Za-z0-9]{36,}`, `ghu_[A-Za-z0-9]{36,}`, `ghs_[A-Za-z0-9]{36,}`, `ghr_[A-Za-z0-9]{36,}`.
- **Connection strings** — `\b(?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^\s]*:[^\s]*@`. The `:...@` portion with any content is the tell — a hostname without embedded credentials is not flagged.

Flag matches with `(page, line, match pattern)`. **Do not auto-redact** — a false positive silently mangling content is worse than a true positive surfaced for the user. The report tells the user what to do: edit the page to abstract the value per Standard 3 ("the API key" instead of the key itself).

---

## Phase 5 — Comprehensive checks (Comprehensive mode only)

In Quick mode, skip this phase entirely and go straight to Phase 6.

Comprehensive adds six content-level checks. Four are best done per-shard inside diagnosis subagents that can read both the wiki pages and the source code they describe; two are orchestrator-owned because they require cross-shard visibility. The orchestrator dispatches, aggregates, and then reconciles.

Time discipline matters in this phase. Record the start epoch (`date +%s`). Use shard count and page volume to pace your dispatch — if the run is taking unreasonably long, stop dispatching new subagents and finalize with the findings you already have. Note "partial results — run stopped early" prominently in the report if you stop early.

### Step 1 — Build the shard plan

Group pages by category. Each category becomes one shard. If a single category has more than ~12 pages or a high cross-link density, split it into multiple shards grouped by sub-topic so each subagent's context stays manageable. Large monorepo packages (`wiki/packages/<pkg>/`) typically warrant one shard each.

For each shard, capture:
- **Shard name** — e.g., `architecture`, `playbook`, `packages/apposum`.
- **Assigned slugs** — every page slug the subagent is expected to audit.
- **Source-code scope** — the subset of `ingest.include` paths that each page in the shard describes. Derive this from the page's `## Code References` section when present, or fall back to the entire `ingest.include` array when the page has no explicit references. Be explicit; subagents will honor the scope.

### Step 2 — Dispatch diagnosis subagents

Run independent shards in parallel — a single message with multiple `Task` calls. Each subagent is read-only and returns a JSON findings manifest.

Use this prompt template, pasting in the shard-specific fields:

```
You are a LoreLake lint diagnosis subagent. You are read-only. Write nothing.

Shard: <shard name>

Assigned wiki pages (you may Read only these, under <project>/llake/wiki/):
- wiki/<cat>/<slug-1>.md
- wiki/<cat>/<slug-2>.md
...

Source-code scope (you may Read only these paths, under <project>/):
<list of paths from ingest.include this shard covers>

For each assigned page, check the following and record findings. Return one
JSON object conforming to the manifest shape at the end of this prompt.

Check A — Stale pages.
Skip this check entirely for pages under wiki/discussions/, wiki/decisions/,
wiki/gotchas/, and wiki/playbook/ — these are historical directories and must
never be flagged for staleness or repaired by lint.
For all other pages, read the `updated:` frontmatter date. Compute the threshold
date = (today - <lint.stale-threshold-days> days). If `updated` is older than
the threshold AND the source files the page describes (read via its
"Code References" section, or — if absent — via the shard's source-code scope)
have commits in `git log --since=<updated-date> -- <paths>`, flag the page as
stale and include the changed-file list.

Check B — Content gaps (local).
Record any concept referenced inside this shard's pages (recurring phrases
from body text, explicit "see also" hints that don't resolve, or concepts
the source code clearly describes that no page covers) that lacks a page.
The orchestrator will reconcile local gap lists against cross-wiki references
to produce a final list.

Check C — Standard 1 coherence.
For each page, apply the new-employee test from schema/code-content-standard.md
Standard 1: a new contributor reading the page plus one or two [[wikilinks]]
away must be able to start work on the subject without flagging a senior
engineer. Flag pages that fail — pure structure dumps without semantic value,
pages that propagate unstated assumptions, pages whose examples are invented
rather than drawn from the source.

Check D — Sensitive content beyond regex.
Beyond the mechanical regex in Quick check 5, look for contextual leaks:
descriptions that mention internal hostnames, customer names, personal data,
specific URLs that bypass authentication, or internal IP addresses. Flag per
Standard 3. Do NOT quote the value itself in your finding; describe its
shape ("mentions an internal hostname `<redacted>` in line 23").

Content standards (non-negotiable, paste-included so you don't have to
chase schema/ files yourself):

- Standard 1 (code-content completeness): <paste schema/code-content-standard.md
  "Standard 1" section verbatim>
- Standard 3 (security): <paste schema/code-content-standard.md
  "Standard 3" section verbatim>

Return exactly this JSON manifest, no prose around it:

{
  "shard": "<shard name>",
  "stale": [
    {"slug": "<slug>", "updated": "<date>", "source_paths": ["..."], "changed_commits": ["<short-sha>: <msg>"], "rationale": "<short>"}
  ],
  "gaps_local": [
    {"concept": "<name>", "referenced_in": ["<slug>", "<slug>"], "suggested_category": "<cat-or-unknown>"}
  ],
  "standard1": [
    {"slug": "<slug>", "severity": "fail|warn", "rationale": "<short>"}
  ],
  "sensitive": [
    {"slug": "<slug>", "line": <int>, "kind": "internal-hostname|customer-name|personal-data|private-url|internal-ip", "rationale": "<short>"}
  ]
}
```

Substitute the shard's specifics into every placeholder. Paste the Standard 1 and Standard 3 sections in full — subagents cannot chase references mid-task without losing context, and the standards must not drift between shards.

As manifests return, aggregate them in session memory. Track any shard that fails or returns malformed JSON; don't silently retry the same prompt. Note failed shards in the report and continue with what you have.

### Step 3 — Orchestrator-owned cross-shard checks

Two checks need cross-wiki visibility; run them yourself, using the aggregated manifests and targeted page re-reads.

**Check E — Contradictions.** Scan for pages making conflicting claims about the same concept: different function signatures, conflicting invariants, opposite dependency directions, two pages both claiming to be the canonical home. The strongest signal is slug-level overlap in subject matter across categories. Record `(page-1, page-2, conflict description)`. Reading the source code the pages describe is often necessary to decide which side is correct; delegate a narrow follow-up subagent if needed, but do not auto-resolve — the user decides.

**Check F — Terminology drift.** For each significant concept (proper nouns, API names, subsystem labels), confirm the wiki uses one canonical name. Flag where two or more variants coexist (`tick loop` vs `main loop`, `worker pool` vs `worker queue`). Record `(canonical candidate, variants seen, pages affected)`. Do not auto-rewrite — the "right" canonical form is a judgment call the user owns.

**Final gap reconciliation.** Merge every shard's `gaps_local` into a single deduplicated list. Then re-check each candidate against the full wiki inventory — a concept one shard flagged as a gap may already have a page in another category. Drop false positives. What remains is the content-gap list for the report.

### Step 4 — Review and repair

You now have findings in six categories (stale, gaps, contradictions, terminology, Standard 1, sensitive). Present them to the user category-by-category via `AskUserQuestion`. For each category that has findings:

- **Question:** "Findings: <category>. <N> items. How should I handle these?" Include a compact summary — slugs and short rationales — before the question so the user doesn't need to scroll back.
- **Options:**
  - **Repair all** — dispatch repair subagents.
  - **Selective** — lets the user pick per-finding. Follow up with a multiSelect `AskUserQuestion` listing each finding.
  - **Defer** — record findings in the log entry but don't repair.

Categories with zero findings: skip the prompt entirely. Do not ask a rhetorical question.

For each repair the user confirms, dispatch a **repair subagent** with a narrowly scoped write surface — only the specific page slugs being repaired, never `discussions/**`. Use this prompt template:

```
You are a LoreLake lint repair subagent.

Scope — you may edit ONLY these files:
- <project>/llake/wiki/<cat>/<slug>.md
(and others in the list)

Repair directive:
<one of the following, keyed to the finding category>

- "Stale" — re-read the source files listed below and update the page to
  reflect current behavior. Update `updated:` frontmatter to today. Do not
  change the `created:` date. Keep the page's existing wikilinks unless
  the referenced page was renamed (in which case update the links).

- "Content gap" — write a new page at <path> that fully describes <concept>
  per Standard 1. Add a line to the corresponding category index. Cross-link
  to the pages that referenced the concept.

- "Contradiction" — after reading the source at <paths>, edit whichever page
  is wrong so both pages agree. If both are wrong, edit both. Do not create
  a new page.

- "Terminology drift" — globally rename the non-canonical variants to
  <canonical> across the listed pages. Preserve the surrounding sentence
  structure; do not rewrite paragraphs.

- "Standard 1 coherence" — expand the page to meet the new-employee test.
  Read the source code at <paths> for concrete examples. Prefer examples
  already present in tests/fixtures/docstrings over invented ones.

- "Sensitive content" — abstract the sensitive value per Standard 3: replace
  the literal with "the API key", "the production database", "an internal
  endpoint", etc. Leave the surrounding context intact.

Source-code context (you may Read only these paths, under <project>/):
<source paths relevant to this repair>

Write surface — strict:
- You MAY write only the pages in the scope above.
- You MUST NOT write to wiki/discussions/**, wiki/decisions/**,
  wiki/gotchas/**, or wiki/playbook/** under any circumstance.
  These are historical directories; their content is owned by session-capture
  and is never modified by lint.
- You MUST NOT write to wiki/<cat>/<cat>.md unless the repair directive
  explicitly says "add a line to the corresponding category index".
- You MUST NOT edit config.json, schema/**, log.md, last-ingest-sha,
  .state/**, or any file outside <project>/llake/.

Content standards (paste-included):
- Standard 1: <paste verbatim>
- Standard 3: <paste verbatim>

Return a JSON manifest:
{
  "pages_edited": [{"slug": "<slug>", "change_summary": "<short>"}],
  "pages_created": [{"slug": "<slug>", "path": "<path>", "category": "<cat>"}]
}
```

As repair subagents return, merge their manifests into the run's overall change log.

---

## Phase 6 — Write the log entry and print the report

Both modes end here.

### Append exactly one `lint` entry to `log.md`

The entry must start with `## [YYYY-MM-DD] lint | <mode> | ...` — mode (`quick` or `comprehensive`) as the first token of the description. This specific shape is what the Phase 2 no-arg path greps for when computing future recommendations. If you put anything else between `lint |` and the mode, the baseline detector will miss this run.

Quick-mode example:

```markdown
## [YYYY-MM-DD] lint | quick | 3 issues found, 2 fixed, 1 deferred

Appended missing entries for [[worker-pool]] and [[fixtures-layout]] to
playbook and gotchas indexes. Flagged one orphan page ([[legacy-sync]]).

Pages affected: [[worker-pool]], [[fixtures-layout]], [[legacy-sync]]
```

Comprehensive-mode example:

```markdown
## [YYYY-MM-DD] lint | comprehensive | 11 issues found, 7 fixed, 4 deferred

Full audit across 42 pages. Fixed 2 stale pages, 3 terminology
inconsistencies, and 2 index desyncs. Flagged 3 content gaps and 1
potential contradiction for user review (deferred).

Pages affected: [[queue-runner]], [[session-lifecycle]], ...
```

Counts are the run's totals — `found = fixed + deferred + unfixable`. Include every affected slug in the `Pages affected:` line; this is what `/llake-doctor` and future lint runs use to trace recent wiki churn.

### Print the final report

Format the report exactly like this. Column widths are approximate — keep labels legible and aligned:

```
LoreLake Lint — <mode> — Complete

Project:          /path/to/project
Scope:            <N> pages across <M> categories
Runtime:          <wall clock>

[CHECK] Orphan pages              : <result>
[CHECK] Broken wikilinks          : <result>
[CHECK] Index sync                : <result>
[CHECK] Frontmatter shape         : <result>
[CHECK] Credential regex          : <result>
[CHECK] Stale pages               : <result>          (comprehensive only)
[CHECK] Content gaps              : <result>          (comprehensive only)
[CHECK] Contradictions            : <result>          (comprehensive only)
[CHECK] Terminology drift         : <result>          (comprehensive only)
[CHECK] Standard 1 coherence      : <result>          (comprehensive only)
[CHECK] Sensitive content review  : <result>          (comprehensive only)

Summary: <total> issues, <fixed> fixed, <deferred> deferred.
Deferred issues recorded in log.md.
```

Rules for the report:

- In Quick mode, omit the six `(comprehensive only)` lines entirely — don't print them as "not run." The user already knows Quick doesn't run them.
- For each `[CHECK]` line, pick a concise result phrase:
  - `OK` — zero findings.
  - `<N> flagged` — findings surfaced, no auto-fix applied.
  - `<N> fixes applied` — auto-fix ran (Quick index-sync, Quick frontmatter normalization).
  - `<N> fixed` — repair subagents ran and made changes (Comprehensive repairs).
  - `<N> flagged, <M> fixed` — mixed.
  - `FAILED — <reason>` — the check could not run (e.g., a shard subagent errored).
- The summary line always appears last. If Comprehensive aborted early due to timeout/budget, append: `Note: <reason> — results are partial.`

Re-running `/llake-lint quick` immediately after a successful run should report the same Quick-check results, minus any issues the run fixed. This idempotency property is what makes the report trustworthy — nothing is "fixed" silently.

---

## Write surface

Both Quick and Comprehensive share the same restrictions. Include them verbatim in every repair-subagent prompt; they back up the tool-level allowances.

| Allowed | Forbidden |
|---|---|
| `wiki/**` except the four fixed-category dirs — page edits during repair | `wiki/discussions/**`, `wiki/decisions/**`, `wiki/gotchas/**`, `wiki/playbook/**` — historical directories, never written by lint. Lint reads them for link resolution only. |
| Category indexes (`wiki/<cat>/<cat>.md`) — appended to by Quick check 3's auto-fix and by Comprehensive repair subagents creating new pages | `schema/**` — immutable to agents |
| `log.md` — appended exactly once per run (Phase 6) | `config.json`, `last-ingest-sha`, `.state/**` |
| | Anything outside `<project>/llake/` |

Comprehensive-mode **diagnosis** subagents are read-only — they write nothing at all. **Repair** subagents inherit the table above, narrowed further to the specific slugs they were dispatched for.

---

## Allowed tools

- `Read`, `Glob`, `Grep` — page and source survey, inventory building, regex scans.
- `Bash` — `git log --since=<date> -- <paths>` for the stale check, config reads via `$PLUGIN_ROOT/hooks/lib/read-config.py`, wall-clock timing, path detection via `$PLUGIN_ROOT/hooks/lib/detect-project-root.sh`.
- `Write`, `Edit` — mechanical fixes (Quick auto-fixes) and confirmed repairs (Comprehensive repair subagents); the single `log.md` append.
- `Task` — Comprehensive-mode diagnosis and repair subagents.
- `AskUserQuestion` — the no-arg choice prompt, the `comprehensive` confirmation warning, and the per-category repair prompts in Phase 5.

---

## Config keys

Read from `<project>/llake/config.json`, falling back to `$PLUGIN_ROOT/templates/config.default.json` via `$PLUGIN_ROOT/hooks/lib/read-config.py`. All keys are optional in the user's config:

| Key | Default | Applies to | Purpose |
|---|---|---|---|
| `lint.model` | `"sonnet"` | Comprehensive subagents | Model used by diagnosis and repair subagents. |
| `lint.effort` | `"high"` | Comprehensive subagents | Reasoning effort for the same. |
| `lint.comprehensive-recommended-after-days` | `14` | No-arg recommendation heuristic | Days since the last comprehensive run that push the recommendation to Comprehensive. |
| `lint.comprehensive-recommended-after-activity` | `20` | No-arg recommendation heuristic | Wiki-mutating entries since the last comprehensive run that push the recommendation to Comprehensive. |
| `lint.stale-threshold-days` | `30` | Comprehensive stale check | Threshold for flagging a page as stale. Pages in the four fixed-category directories are always exempt. |

`model` and `effort` apply **only to Comprehensive subagents** — Quick runs in-session under the parent session's model and limits, so no knobs are needed there. The three hyphenated keys are heuristic thresholds — they change the recommendation logic and the stale check, not per-subagent settings.

---

## Behaviors out of scope

- **Editing `wiki/discussions/**`.** Capture-owned; immutable Key Facts per Standard 2. If a discussion page is orphaned or contradicts a derived page, flag and defer — the resolution path is manual or through capture, not through lint.
- **Editing `schema/**`, `CLAUDE.md`, or anything outside `<project>/llake/`.** Lint's entire write surface is inside `llake/`.
- **Bootstrapping missing categories.** That is `/llake-bootstrap`. Comprehensive will flag a content gap whose slug has no natural home, but it will not invent a new top-level category.
- **Repairing hook or install drift.** That is `/llake-doctor`. If `$PROJECT_ROOT/llake/config.json` or `log.md` is missing, lint refuses with a pointer to doctor.
- **Running without a prior install.** No `$PROJECT_ROOT/llake/config.json` → refuse with a pointer to `/llake-lady`.
- **Second `lint` log entry in the same run.** Exactly one entry is appended in Phase 6 regardless of how many fixes, repair subagents, or deferred findings the run produced. Multiple entries would break the baseline grep the no-arg recommendation uses.

---

## References

- Page format, write surface, monorepo rule, naming, indexing: `$PLUGIN_ROOT/schema/core.md`.
- Standard 1 (new-employee test) and Standard 3 (security, regex + context-aware): `$PLUGIN_ROOT/schema/code-content-standard.md`.
- Standard 2 (why discussions are never edited by lint): `$PLUGIN_ROOT/schema/conversation-content-standard.md`.
- Project-root detection contract: `$PLUGIN_ROOT/hooks/lib/detect-project-root.sh`.
- Config layering and dot-key lookup: `$PLUGIN_ROOT/hooks/lib/read-config.py`.
- Config defaults (source of truth for thresholds and knobs): `$PLUGIN_ROOT/templates/config.default.json`.
- Sibling skills: `/llake-lady` (initial install), `/llake-doctor` (diagnose and repair install), `/llake-bootstrap` (populate wiki).
