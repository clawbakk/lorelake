# Ingest v3 pipeline

- **Status:** accepted design, not implemented.
- **Date:** 2026-10-02.
- **Map:** [Ingest v3 wayfinder map (LOR-1)](https://linear.app/clawbakk/issue/LOR-1). This spec resolves [LOR-14](https://linear.app/clawbakk/issue/LOR-14).
- **Decision record:** [ADR 0001: Ingest v3 pipeline](../adr/0001-ingest-v3-pipeline.md).
- **Quality definition:** [Ingest quality bar](../benchmark/quality-bar.md).

Ingest v3 is a third ingest pipeline, next to legacy and v2. One read-only analysis agent works out what a commit range means for the wiki and writes a brief. A second read-only agent (the recall pass) looks for pages the brief missed. Code groups the affected pages into small bundles and spawns one writer per bundle, in parallel. Each writer edits its own pages directly, and may write only those pages: the write surface is enforced at the permission level, then checked again after the write. Code runs $0 checks on what was written, gives flagged pages one fix round, then owns all bookkeeping: `updated:` dates, indexes, the gap record, the log entry and the ingest cursor. Pages a run could not bring current are recorded in a committed gap record, and the cursor advances over them; the next run takes them on first.

Terms in **bold** at first use are defined in [`CONTEXT.md`](../../CONTEXT.md).

## 1. Goal and scope

**Goal.** Pick the design at the **knee** of the cost–quality curve, measured on both benchmark fixtures, where quality is the **quality score (Q)** defined by the quality bar. The bar is the curve's reference line, not a gate.

**Scope.**

- v3 is selected by `ingest.pipeline: "v3"`. Legacy stays the default; legacy and v2 keep their behaviour and stay selectable.
- v3 shares with the other pipelines: `ingest.include`, `ingest.branch`, `ingest.schedule` (the **batching gate**), the post-merge lock and the cursor file `llake/last-ingest-sha`. Everything else lives under `ingest.v3.*` and v3's own code.

**Constraints** (from the map's Notes):

- Every agent is a `claude -p` process spawned from the post-merge hook, orchestrated by shell and Python. No Agent SDK and no API key. Several agents per run, concurrent ones included, are allowed.
- Runs are unattended. No step waits for a human; anything that needs one is recorded and reported. Reviewability comes from the artifacts persisted in the agent dir and the log entry.
- Opus for judgment; cheaper models only where the benchmark shows no loss, and always through a config knob. The writer default is a deliberate exception (§14, ADR).
- Behaviour that a sweep could tune is a config knob, not a hardcoded choice. Choices the benchmark settled are fixed in code (see §13).
- Code informs the agents; it does not bound them. Mechanical signals (hit index, change leads) are leads handed to judgment, not the limit of what judgment may find.
- Diffs alone are not enough input: every agent can read the surrounding source at the range head.
- No length bars. Conciseness is "to the point", never a word count.
- No dependence on commit messages. Commit subjects may be shown; nothing relies on their detail.
- Pages describe the code at the range head. A run does not summarize diffs or narrate change on state pages.

**Non-goals** (the map's Out of scope):

- Retiring the legacy or v2 pipelines. v3 ships separately; both stay selectable.
- Applying the prompt-cache TTL fix to legacy or v2.
- Changes to the bootstrap or session-capture writers (§17 lists what may transfer, as notes only).
- Any runtime other than `claude -p` from the hook.
- A user-facing benchmark. The benchmark is a developer tool for choosing v3's design.

## 2. Pipeline at a glance

```
post-merge (lock held) ── batching gate ── WAIT, nothing owed ─► exit
        │ RUN / RUN reason=gaps / EMPTY with major gaps owed
        ▼
recover dead runs ($0, run journal)
        ▼
plan run ($0) ── empty ─► advance cursor, exit
        │       ── skip  ─► advance cursor with a ranges[] entry, exit
        │ range | gap-only
        ▼
derive names, stage inputs ($0)                      ┐
        ▼                                            │ range runs only
analysis (1 agent, read-only + its brief dir) ───────┤ fail ─► hold cursor
        ▼                                            │
recall pass (1 agent, read-only) ── fail ─► ignored  ┘
        ▼
assemble + validate brief ($0) ── invalid ─► hold cursor
        ▼
bundle ($0)
        ▼
writers × N in parallel (each: snapshot ─► edit own pages ─► diff, surface check)
        ▼
[verifier, off by default]
        ▼
$0 checks ─► fix round (fixers on flagged pages) ─► $0 checks
        ▼
finalize ($0): updated:, indexes, gap record, log entry, cursor + clock
```

| Stage | Kind | Input | Output (in the run's agent dir unless noted) | On failure |
|---|---|---|---|---|
| Recover | code | run journals of earlier runs | in-flight pages of a dead run restored | — |
| Plan run | code | cursor, merged head, failure counter, gap record | run kind and head | — |
| Derive names | code | `git diff -M` of the range over `ingest.include` | removed/added names, hit index, full hit list | hold cursor |
| Stage inputs | code | range, wiki | commit list, per-file patches, page catalog, change leads, analysis prompt | hold cursor |
| Analysis | LLM | staged inputs; source and wiki on demand | brief files | hold cursor; work failures count toward a split |
| Recall pass | LLM | themes, catalog, listed and ruled-out pages | extra pages for the brief | ignored; brief unchanged |
| Assemble brief | code | brief files, hit index, gap record | validated brief | invalid brief: hold cursor, work failure |
| Bundle | code | brief | bundles in dispatch order | — |
| Writers | LLM | shared prefix + bundle part | edited pages, writer status, snapshot, diff | revert bundle, retry as singletons, then `writer-failed` gap |
| Verifier (off) | LLM | bundle pages, diff | findings | `unverified` gap |
| Checks | code | diff, brief, names | flags per page | — |
| Fix round | LLM | flagged pages + flags | edited pages, status | revert to post-write state, `flagged` gap |
| Finalize | code | everything above | `updated:`, indexes, `llake/ingest-gaps.json`, `log.md` entry, cursor, clock | hold cursor |

**Run kinds.**

| Kind | When | What runs |
|---|---|---|
| Range run | the **commit range** has watched changes | every stage; carried gaps join the brief |
| Split run | analysis failed twice with work failures on this base | a range run over base..midpoint (§3) |
| Gap-only run | no watched changes, open non-stuck major gaps | no analysis; writers on the carried major gaps only |
| Empty | no watched changes, nothing major owed | cursor and clock advance; no agent |
| Skip | a single-commit range failed analysis twice | cursor advances with a `ranges[]` entry; no agent |

**Write order** at finalize: pages → indexes → gap record → log entry → cursor and clock. A crash at any point leaves the cursor on the old base, so the next run redoes the range; pages already corrected stay correct.

## 3. Run planning

**Gate.** v3 uses the shared batching gate unchanged and adds one rule. When the gate says WAIT and the gap record holds an open, non-stuck major gap, the verdict becomes `RUN reason=gaps`. When the gate says EMPTY (no watched changes) and such a gap exists, v3 runs a **gap-only run**. Minor gaps never trigger a run; they ride along with the next range run.

**Run journal.** Each run keeps `run.json` in its agent dir: the run kind, base and head, the pages in flight with their snapshot location, and a `finalized` flag. At start, v3 scans the journals of earlier runs. A run that is not finalized and has pages in flight died mid-write: its in-flight pages are restored from their snapshots (pages it created are deleted). Its cursor was never advanced, so its range is ingested again.

**Failure counter.** `.state/ingest-failures.json` holds `{base, count}`. Analysis work failures (including an invalid brief) on the same base increment it; infra failures do not (§12). A finalized run clears it.

**Range split.** When the counter reaches 2 for the current base, the next run ingests base..midpoint, where the midpoint is the middle commit of the first-parent chain. On success the cursor advances to the midpoint and the rest of the range follows on the next merge. A range of one commit cannot shrink: after two work failures it is skipped. The cursor advances past it, and the gap record gets a `ranges[]` entry with cause `analysis-failed` and the range's $0 hit lines as leads, so a human can see what was not ingested.

## 4. Removed names, hit index and change leads

All of this is code, $0, with no language parsers.

**Names.** From the removed and added lines of `git diff -M base..head -- <include>`:

- simple identifiers of 4 or more characters with an internal capital, an underscore or a digit, or all capitals;
- compound dotted or kebab-case names;
- a **removed name** has zero word-boundary `git grep` hits at the head; deleted and renamed-away file names are removed names too;
- added names are derived the same way, mirrored;
- names whose use only shrank (still present at head) are dropped: as leads they gave no gain.

**Hit index.** Every wiki page that mentions a removed name, grouped page → name → count, with line numbers capped at 20 per page (`hitIndexCap`, fixed). The full hit list stays on disk for writers. Analysis gets the hit index instead of searching the wiki for names itself.

**Sweep accounting.** The **staleness sweep** finds only staleness that names removed code (on the big fixture 46% of stale claims and 23% of major ones; on the small fixture none), so judgment must cover the rest. Code still holds analysis to the sweep:

- a state page with hits joins the **affected set** automatically, as a minor page with its hit lines as claims (gotchas and category indexes excepted);
- a decision or gotcha page with hits must appear in the brief's pages or in `considered` with a reason; an unaccounted one is added as a minor page and reported.

On the big fixture this auto-add produced no false positives.

**Change leads.** Two $0 indexes handed to analysis and, per page, to writers:

- **Anchor index:** wiki `path:line` anchors whose base line the range removed or rewrote (not merely shifted). 81% precision on the big fixture, against 86% for the hit index.
- **Literal index:** log and error strings from removed lines, marked removed or changed.

**Patches.** Per-file patches are written to disk, split above 60 KB, so agents read only the files they need.

## 5. Analysis

**Model and effort.** Opus, medium effort (`analysisModel`, `analysisEffort`). Sonnet failed the brief: it listed 27 pages and missed 3 of the 14 pages legacy corrected. Low effort was erratic (small-fixture **brief Q** 83–98.9 across runs; big-fixture **claim Q** 19). High effort gave no gain and cost 35% more on the small fixture.

**Inputs** (all pre-staged): the commit list, the per-file patch index, the page catalog (path and `description:` of every page), the hit index, the change leads and the carried gaps' pages. Source at the head and wiki pages are read on demand. Analysis does not grep the wiki for names; given the hit index instead, it costs less ($4.9 against $5.47) and scores a higher brief Q.

**Record rule (framed).** A record page goes in the brief when the range moved past it: it is implemented, reversed or superseded, or it states as current a consequence, a "still" or "is live" claim, or a code pointer the range made false. Text framed in time ("at the time", "was decided") is history and does not qualify. A wider rule pulled in 8 extra record pages on the big fixture; the framed rule 0–1.

**Claims (`majorQuotes`).** Analysis rates each page's severity first. Every major claim, and every restatement of it on the same page, keeps its own verbatim quote. Minor claims are capped at 2 per page, each with a terse `head` pointer. The quotes are what writers and checks act on.

**Incremental, batched writes.** Analysis writes the brief as files in its brief dir as it goes, several page files per write, instead of one final response. This removes the single-response output cap and cuts turns. The prompt asks for parallel tool calls.

**Isolation and surface.** Same spawn flags as every v3 agent (§9). Tools `Read, Glob, Grep, Edit, Write`; its only write permission is its own brief dir. No Bash.

**Brief schema.**

| Part | Fields |
|---|---|
| Themes | one entry per **change theme**: id, title, summary |
| Page entry | `page`, `severity` (major\|minor), `reason`, `themes`, `claims[]` {`quote`, `head` (`path:line`), `severity`}, `evidence` (files), `newFacts[]` each with its home page |
| New pages | named up front with their category and purpose |
| Considered | pages looked at and ruled out, each with a reason |
| Blurbs | category blurbs the range made false |
| Notes | anything else the run should log |

**Validation by code.** A page file that does not parse or lacks a required field makes the brief invalid (a work failure). Each claim's quote is checked against the page at base; a quote not found is kept but marked, and is not residue-checked later. Then code applies the sweep accounting (§4), merges carried gaps (§7) and turns blurb flags into minor `blurb` gaps, because no writer owns indexes.

**Known residual misses** (accepted): on the big fixture one record page is ruled "still holds" in every run; on the small fixture one unhinted minor playbook line is found in about half the runs.

## 6. Recall pass

A second read-only agent, Opus medium (`recallPass`, `recallEffort`). It gets the themes, the catalog, the pages the brief lists and the pages it ruled out, and answers one question: which other pages restate a behavior a theme changed? Its pages join the brief as thematically affected pages.

On the big fixture it added 1–3 affected pages for $0.34–0.53; on the small fixture none, for $0.10–0.11. Sonnet found nothing, so the default is Opus. A recall failure of any kind never fails the run: the brief goes on unchanged.

## 7. Brief assembly and bundling

**Assembly** (code). The validated brief, plus:

- carried gaps from the gap record, marked `carried`, their claims as leads (range runs take all open non-stuck gaps; gap-only runs take the major ones);
- auto-added pages from the sweep (§4);
- recall pages (§6).

A writer's `otherStale` reports are handled at finalize (§12), not here.

**Bundles.** One writer per bundle; one bundle per page set. Pages bundle together when they share themes or evidence files (`bundleStrategy=shared`, fixed), up to:

- `bundleMaxPages` = 4 pages;
- `bundleMaxWeight` = 80, where a page weighs its size in KB plus 1 per brief claim.

A page heavier than the weight cap goes alone. A record page joins the bundle of the state page it points to, so one writer marks the record and corrects the page it links. On the big fixture 58 pages made 28 bundles, and weight was the binding cap (the largest pages are 50–90 KB).

**Dispatch order.** Bundles holding a major page first. Among carried gaps: major first, then oldest `since`, then new pages.

## 8. Writers

**Topology.** Flat fan-out, no coordinator. Writers never talk to each other. `writerConcurrency` = 4 run at once (8 ran fine; 4 concurrent Opus writers saw no rate-limit rejections).

**Context.** Two parts, in this order:

1. **Shared prefix** (identical for every writer, passed as an appended system prompt with the dynamic system-prompt sections excluded so it caches across writers): the **writer contract**, the theme summaries and the full page catalog.
2. **Bundle part** (the user prompt): for each page, its brief slice (reason, claims, evidence files), its hit lines and the added names, the anchor leads on it, and the new facts homed on it.

Source at the head is read on demand; per-file patches are on disk. A writer does not get the full brief, other pages' slices or the whole diff.

**Prefix warm-up.** The first writer starts alone; the others wait for its first turn, so the shared prefix is written to the cache once. This saves about $0.12 per writer.

**Writer contract.**

1. Edit only the pages you own. Other pages are read-only to you.
2. Brief claims, hit lines, anchor leads and new facts are leads, not facts. Verify each against the code at the head before acting. A lead that is wrong at the head is reported as rejected, not written.
3. On a state page, make every stale claim true of the head and add the new facts homed on it. No change narration ("previously", "this range removed").
4. On a record page, never rewrite the body. Set `status:` and add a dated superseded-or-resolved note that links the state page now true.
5. A page with a major claim must be corrected. A page whose claims are all minor may be declared a gap instead, naming the claims it leaves.
6. Fix or remove every `path:line` anchor on a line you touch. A new anchor must point at a line that holds the named symbol at the head.
7. Do not add worked examples, counts or caller attributions you have not verified at the head.
8. Keep `description:` true and `related:` current on your pages. Do not add reciprocal links to pages you do not own. Never touch `updated:` or category indexes.
9. Report stale text you notice on pages you do not own in `otherStale`, with a verbatim quote, a `head` pointer and a severity.
10. End with the structured status.

Rule 2 exists because a brief can be wrong: one brief stated a false new fact, writers repeated it in two of three runs, and a fixer caught it once.

**Structured status** (validated against a schema):

| Field | Meaning |
|---|---|
| `pages[].page`, `pages[].status` | `corrected`, `declared-gap` or `no-change` |
| `pages[].claimsLeft[]` | for a declared gap: {`quote`, `head`, `severity`} |
| `pages[].rejected[]` | brief claims the writer verified as not stale |
| `pages[].note` | free text for the log |
| `otherStale[]` | {`page`, `quote`, `head`, `severity`} on pages the writer does not own |

**Write mode.** `writeMode=edit`: targeted `Edit` calls. Whole-page `Write` cost 1.7× on the small fixture for no gain; it stays a knob.

## 9. Writing mechanism and v2 invariants

**Spawn.** Every v3 agent is spawned the same way:

- `claude -p` with `--model`, `--effort`, `--max-budget-usd` from the stage's knobs, stream-JSON output, and a JSON schema where the stage returns structured output;
- `--tools` limits the tool set (no Bash for any agent); `--allowedTools` is the stage's surface;
- `--permission-mode dontAsk`, so anything not allowed is denied, never prompted;
- `--setting-sources ""` and `--strict-mcp-config`, so no user or project settings, hooks or MCP servers load;
- `--no-session-persistence`;
- env `IS_LLAKE_AGENT=true` (recursion guard) and `CLAUDE_CODE_PROMPT_CACHE_TTL=5m`;
- working dir: the project root.

**Writer surface.** Tools `Read, Glob, Grep, Edit, Write`. The allow list is `Read, Glob, Grep` plus one `Edit(//<absolute page path>)` rule per owned page and per new page the brief named for it. One `Edit(...)` rule also covers `Write` to that path. Across every prototype stage: 0 permission denials on owned pages, 0 writes outside the surface.

**Snapshot, diff, revert.** Before a bundle starts, code copies its pages into the agent dir (never `git stash`) and records them in flight in the run journal. After the writer exits, code diffs each page against its snapshot. Because each page has exactly one writer and its own snapshot, reverting a page is reliable.

**v2 invariants and how v3 keeps them.**

| v2 invariant (plan-apply ADR) | In v3 |
|---|---|
| Write surface enforced outside the agent | **Kept, stronger.** Per-writer allow list of exact page paths at the permission layer prevents out-of-surface writes; the post-write surface check reverts any changed file no writer owned. |
| A failed operation fails alone | **Page-level.** Every page ends `corrected`, `declared-gap` or `no-change`, or as a gap with a cause. A failing bundle never touches another bundle's pages. |
| The change is inspectable before it is applied | **Inspectable after.** Snapshots, the per-run diff and the writer status are kept in the agent dir. |
| Bookkeeping by code | **Kept.** |
| Analysis read-only | **Kept.** Its only write is its brief dir. |

**Who owns what.**

| Code | Writer |
|---|---|
| `updated:` (one line edit per changed page) | page body |
| frontmatter validation | `description:` |
| dangling-link repair | `related:` (no reciprocal links) |
| category indexes (rows from `description:`, counts) | record `status:` and note |
| log entry, gap record, cursor | its structured status |

**Failure and repair.**

| What happens | Repair | Outcome |
|---|---|---|
| `Edit` target text not found | the writer retries in its own loop | — |
| A file outside every surface changed | reverted from the pre-run copy | reported in the log |
| Frontmatter does not parse after the write | page reverted to its snapshot | `reverted` gap |
| A `[[link]]` added this run points at no page | code unlinks it | — |
| Writer crash, timeout or budget trip | bundle reverted; its pages retried once, each alone | still failing: `writer-failed` gap |
| Writer reports `corrected` but the page is unchanged | brief claims stay owed | `flagged` gap |

## 10. Verification

**$0 checks**, on every page a writer changed:

1. **Removed-name grep:** a non-gotcha state page must have zero hits for removed names.
2. **Quote residue:** a claim's quote still on a page reported corrected (record pages and claims the writer rejected are exempt).
3. **Anchors on added lines:** the path resolves, the line is in range, and a named symbol on the anchored line appears within ±2 lines of the cited line (the quality bar's rule).
4. **Status against diff:** reported corrected but unchanged.

Plus the surface, frontmatter and link checks of §9. Known checker false positives (follow-ups, §16): a quote that matches an unchanged table-row prefix; anchor-to-symbol pairing on a line with several anchors; a word match on a config key; a whole-word match that misses a name embedded in a longer one. Known checker gaps: an anchor range that ends past the end of the file; only added lines are checked.

**Fix round** (`fixRound=on`). A fresh writer (a fixer, with the writer knobs) gets each flagged page with its flags (and verifier findings, if the verifier is on). One round only. The $0 checks run again; a page still flagged becomes a `flagged` gap. A fixer budget trip reverts the page to its post-write state and records a `flagged` gap. On the big fixture the fix round cost about $1.1, raised the **harness screen** from 14 to 17 of 21 checks and left Q unchanged.

**Verifier** (`verifierMode=off` by default; `accuracy` or `accuracy+residual`). A read-only **verification pass** per bundle. Not adopted because one pass misses too much and costs too much for what it finds: Sonnet raised Q by 0.35–0.65 for $2.8–4.1 more; an Opus verifier lowered Q (92.6); a single pass found 25–63% of eight known errors (Sonnet high 5 of 8 at $6.12, Opus medium 3–5, Sonnet medium 2). Verifier findings are recorded as gaps; they never revert a page and never hold the cursor. A page the verifier did not check (budget trip, run cap) gets a minor `unverified` gap when the verifier is on.

**Indexes and blurbs.** Code rebuilds every touched category index: rows from each page's `description:`, created and deleted pages added or removed, counts. A category blurb that became false gets no writer; it becomes a minor `blurb` gap. No v3 run introduced an index problem.

## 11. Per-stage context, model, effort and budget

| Stage | Default model / effort | Budget | Timeout | Measured max (big fixture) |
|---|---|---|---|---|
| Analysis | Opus / medium | $10.00 | 1800 s | $4.98, ~120 turns, 897 s |
| Recall pass | Opus / medium | $1.00 | 900 s | $0.53 |
| Writer | Sonnet / medium | $3.00 | 900 s | $1.68 (Sonnet), $2.16 (Opus); peak context 159k; 421 s |
| Fixer | writer knobs | writer budget | writer timeout | $0.61 |
| Verifier (off) | Sonnet / medium | $1.00 | 600 s | $0.44 |
| Whole run | — | `maxRunBudgetUsd` $40.00 | `timeoutSeconds` 3600 s | $17.9 e2e; ~1800 s |

**Calibration rule.** Budgets and timeouts are about twice the highest value measured at the knee, rounded. The writer budget is 1.8× the Sonnet maximum and 1.4× the Opus maximum, so the high-accuracy setting fits it too. Stage timeouts are clipped to the time left before the run deadline.

**Context.** Peak-context targets per stage were measured, not enforced, and writers reached 159k; they are not rules. Bundle weight governs writer context. The run report gives peak context per stage.

**Cache.** Every spawn uses a 5-minute prompt-cache TTL (set per process; needs a CLI version that honours it). Verify once per install that the stream shows 5-minute cache writes. Fitted list prices per million tokens: Opus output 20, cache read 0.20, 5-minute cache write 5, input 4; Sonnet output 9.94, cache read 0.20, cache write 2.53.

**Prompt order.** Shared content first, stage-specific content last, in every prompt, so prefixes cache. The analysis prompt asks for parallel tool calls.

**Run cap.** Before each spawn, code checks spent + the budgets of agents in flight + this agent's budget ≤ `maxRunBudgetUsd`. A bundle that does not fit is not dispatched; its pages become `run-cap` gaps. Major-first dispatch means minor pages are capped first.

**Budget and timeout trips.**

| Where | Outcome | Cursor |
|---|---|---|
| Analysis budget, timeout or error | work failure, counted toward a split | holds |
| Recall pass | ignored | — |
| Writer | revert bundle, retry pages as singletons, then `writer-failed` | advances |
| Fixer | revert to post-write state, `flagged` | advances |
| Verifier | `unverified` | advances |
| Run cap reached | undispatched pages `run-cap` | advances |
| Run deadline during writing | in-flight bundles killed and reverted, the rest `timeout` | advances |

## 12. Gap record and cursor policy

**The file.** `llake/ingest-gaps.json`, committed, one JSON document rewritten whole by each run's finalize. It is the **gap record** together with the run's log entry. It has no union merge rule: a union of two JSON documents is not JSON, and runs on the monitored branch are serialized by the post-merge lock. Bootstrap resets it; doctor validates it.

```json
{
  "version": 1,
  "asOf": "<head sha>",
  "agent": "<agent id>",
  "date": "YYYY-MM-DD",
  "gaps": [
    {
      "page": "wiki/<category>/<page>.md",
      "severity": "major | minor",
      "cause": "declared",
      "since": "<head sha when first recorded>",
      "attempts": 1,
      "stuck": false,
      "claims": [
        { "quote": "<verbatim text on the page>", "head": "<path:line>", "severity": "minor", "source": "brief" }
      ]
    }
  ],
  "ranges": [
    { "base": "<sha>", "head": "<sha>", "cause": "analysis-failed", "leads": ["<page: hit lines>"] }
  ]
}
```

**Causes.**

| Cause | Meaning | Counts an attempt |
|---|---|---|
| `declared` | **declared gap**: the writer left minor claims | yes |
| `flagged` | checks, verifier or another writer still find stale text after the run | yes (not for `writer:other`) |
| `writer-failed` | the writer failed twice (bundle, then alone) | yes |
| `reverted` | the write was reverted (invalid frontmatter) | yes |
| `run-cap` | not dispatched: run cap | no |
| `timeout` | run deadline | no |
| `infra` | rate limit, overload, auth or network | no |
| `unverified` | verifier on, page not verified | no |
| `blurb` | a category blurb is false | no |

**Sources** of a claim: `brief`, `verifier:residual`, `verifier:accuracy`, `check:removed-name`, `check:quote-residue`, `check:anchor`, `writer:other`. Verifier sources stay in the schema because the verifier is a knob.

**Every claim is on the page.** Code keeps only claims whose quote is found on the page at finalize; a gap whose quotes all vanished gets one placeholder claim naming its cause.

**Carry-over.**

- The next range run takes every open, non-stuck gap first (**carried gap**); a gap-only run takes the major ones.
- A carried page that ends corrected and unflagged is resolved: removed from the file and listed in the log entry.
- A carried gap the run did not work on stays as it was. A gap whose page no longer exists is dropped and noted in the log.
- `since` is kept across carries; `attempts` grows only on dispatched failures (table above).
- **Stuck gap:** `attempts` reaches 3 (fixed). It is never dispatched again automatically; the log marks it "needs a human" and doctor reports it.

**`otherStale`.** A writer's report on a page it does not own is kept only if code finds the quote on that page. If the page is outside this run's pages it becomes a `flagged` gap with source `writer:other` and no attempt counted; if it is in the run, its own writer had it. Unfound quotes are dropped and noted in the log.

**Log entry** (code-written, appended to `log.md`):

```
## [YYYY-MM-DD] ingest | <base7>..<head7>: v3 — N updated, M created, G gaps (K major)

Agent `<id>`, $X.XX list estimate. Themes: <id> <title>; ...

Pages affected: [[page]] (updated), [[page]] (created), [[category index]]

Gaps (every claim is in `llake/ingest-gaps.json`):
- [[page]] — <severity>, cause: <cause>: <first claim> (+n more) — needs a human

Carried gaps resolved: [[page]], ...
```

A gap-only run's heading reads `gap-only at <head7>` and says "No new range: carried gaps only."

**Failure classes.** An agent failure is **infra** when the CLI reports a rate limit, overload, auth or network error, when the stream shows a rejected rate-limit event, or when the agent produced no first turn. Anything else (budget, timeout, max turns, invalid output) is **work**. An infra failure during writing stops further dispatch: in-flight bundles are reverted and every unwritten page becomes an `infra` gap. Across the downstream sweep there were 2 infra events and 0 work failures.

**Cursor.** The **ingest cursor** means "accounted for": every change up to it is reflected in the wiki or owed in the gap record.

| # | Situation | Cursor | Also |
|---|---|---|---|
| 1 | No watched changes, nothing major owed | advances to head | clock reset |
| 2 | No watched changes, major gaps owed | advances to head after the gap-only run | clock reset |
| 3 | Range run finalized, with or without gaps | advances to head | counter cleared |
| 4 | Split run finalized | advances to the midpoint | counter cleared |
| 5 | Analysis or brief work failure | holds | counter +1 |
| 6 | Analysis infra failure | holds | counter unchanged |
| 7 | Single-commit range failed twice | advances past it | `ranges[]` entry |
| 8 | Writer, fixer or verifier failure; run cap; deadline after analysis; infra during writing | advances | gaps recorded |
| 9 | User kill or crash before finalize | holds | kill: run's writes reverted; crash: journal recovery next run |
| 10 | Finalize fails | holds | next run redoes the range |

**Budget exhaustion across runs.** Nothing is lost when a run runs out of budget or time after analysis: undone pages are gaps, majors trigger the next run through the gate, and stuck gaps stop retrying after 3 attempts. In a robustness test on the small fixture, a low run cap left 7 `run-cap` gaps (5 major); the gap-only run that followed cleared all 5 majors for $0.63.

## 13. Config surface `ingest.v3.*`

Defaults belong in `templates/config.default.json` (added when v3 is implemented; the config fallback contract applies).

| Key | Default | Values | Notes |
|---|---|---|---|
| `analysisModel` | `opus` | model alias | Sonnet failed the brief (§5) |
| `analysisEffort` | `medium` | low\|medium\|high | |
| `analysisBudgetUsd` | `10.00` | USD | |
| `analysisTimeoutSeconds` | `1800` | s | |
| `recallPass` | `opus` | off\|sonnet\|opus | Sonnet found nothing |
| `recallEffort` | `medium` | low\|medium\|high | |
| `recallBudgetUsd` | `1.00` | USD | |
| `recallTimeoutSeconds` | `900` | s | |
| `writerModel` | `sonnet` | model alias | `opus` is the high-accuracy setting |
| `writerEffort` | `medium` | low\|medium\|high | also used by fixers |
| `writerBudgetUsd` | `3.00` | USD | per bundle |
| `writerTimeoutSeconds` | `900` | s | |
| `writerConcurrency` | `4` | int | |
| `bundleMaxPages` | `4` | int | |
| `bundleMaxWeight` | `80` | page KB + 1 per claim | |
| `writeMode` | `edit` | edit\|write | |
| `fixRound` | `on` | on\|off | |
| `verifierMode` | `off` | off\|accuracy\|accuracy+residual | |
| `verifierModel` | `sonnet` | model alias | best cost per error found |
| `verifierEffort` | `medium` | low\|medium\|high | |
| `verifierBudgetUsd` | `1.00` | USD | per bundle |
| `verifierTimeoutSeconds` | `600` | s | |
| `cacheTtl` | `5m` | 5m\|1h | |
| `maxRunBudgetUsd` | `40.00` | USD | run cap |
| `timeoutSeconds` | `3600` | s | run deadline |

**High-accuracy setting:** `writerModel: "opus"`, and `maxRunBudgetUsd: 80` for large backlogs (the Opus-writer run cost $34.5 end to end; with four $3 writer budgets reserved, $40 would cap it).

**Shared keys** (not under `v3`): `ingest.pipeline`, `ingest.include`, `ingest.branch`, `ingest.schedule`.

**No `allowedTools` keys.** Every surface is computed by code from the stage and the bundle.

**Fixed in code** (settled by the benchmark; not knobs): hit index as analysis input; framed record rule; change leads on; batched brief writes; `majorQuotes` claims; shared-theme bundling; shared prefix in the system prompt; prefix warm-up; gap-only runs take major gaps; full catalog for writers; hit index cap 20; split after 2 work failures; stuck after 3 attempts.

**Benchmark-only hooks** stay out of the config file and are read from the environment: `LLAKE_V3_FROZEN_BRIEF` (reuse a **frozen brief**, skip analysis) and `LLAKE_V3_STOP_AFTER` (stop after a stage; the cursor holds).

## 14. Evidence

All numbers are from the two benchmark fixtures ("big": a large backlog, 58 affected pages; "small": a short range), with the prompt-cache TTL at 5 minutes. Costs are CLI list estimates. Downstream runs used a frozen brief ("Stage A" is the brief of the first analysis sweep, before quoted claims; "v4 with quotes" is the default brief); "e2e" adds the measured analysis and recall cost (≈$4.8 big). Details stay in the tickets linked from the map.

**References.**

| | Big Q | Big $ | Small Q | Small $ |
|---|---|---|---|---|
| Legacy | 67.0 | 9.36 (1161 s) | 76.1 | 6.51 |
| v2 | 34.3 | 1.52 | 62.5 | 1.27 |

**Curve, big fixture.**

| Rung | Writers | Brief | Q | $ down / e2e | Screen |
|---|---|---|---|---|---|
| R0 | Sonnet low, bundles of 8 | Stage A | 85.1 | 7.23 / 12.02 | 14/21 |
| R1 / R1 + fix round | Sonnet medium | Stage A | 89.2 / 89.0 | 9.54 / 14.33; 10.74 / 15.53 | 14 / 17 |
| K1, lean brief | Sonnet medium + fix round | v4 | 92.3 | 11.68 / 16.16 | 16/21 |
| **K1 (default)** | Sonnet medium + fix round | v4 with quotes | **94.25** (mean of 6, 92.6–94.9) | 11.81 / ~16.6 (15.8–17.9) | best 17/21 |
| K1 + Sonnet verifier | | | 94.6–94.9 | +2.8–4.1 | 16–17 |
| K1 + Opus verifier | | | 92.6 | 18.68 e2e | 16 |
| O-low | Opus low | v4 with quotes | 85.7 | 13.41 / 18.21 | dominated |
| R2 | Opus medium | Stage A | 96.5 | 26.01 / 30.80 | 14/21 |
| **O-med** | Opus medium + fix round | v4 with quotes | **97.5** | 29.72 / 34.52 | 16/21 |

**Small fixture.** Saturated: every Sonnet-writer rung scored Q 100. K1 passed the harness screen 21/21 at $1.05 down, $2.61 e2e. Opus medium writers (R2) cost $3.12, 0.48× legacy.

**Accuracy** (big fixture, **union audit**, **writer-introduced errors** in added lines).

| | Introduced errors | Per 100 added lines | False statements left on the pages after the run |
|---|---|---|---|
| Legacy | 1 minor in 2081 lines | 0.05 | ≈307 (62 major) |
| K1 (3 audited runs) | 4–13 per run, mean 8.7; 1 major across the 3 | 0.52 | ≈60–74 (8–9 major) |
| O-med (1 audited run) | 1 minor in 2592 lines | 0.04 | ≈28 (3 major) |

K1 fails **legacy parity**: one major introduced error and ten times legacy's rate. It leaves about a fifth of legacy's false statements on the pages. O-med meets parity on one audited run; that verdict rests on one borderline severity call. Writer error classes seen (several recur across runs): wrong counts; a heading or lead sentence contradicting the body; anchors off by one or pointing at a sibling or fallback path; new worked examples with wrong details; false caller attributions; false facts carried from the brief.

**Cost by token class.** Writers spend 40–43% on output, 21–25% on cache reads and 35–36% on cache writes, for both models. Cache reads cost the same on Opus and Sonnet; Sonnet output and cache writes cost about half. Hence Sonnet writers cost about half of Opus writers.

**Analysis.**

| Brief | Big | Small |
|---|---|---|
| Stage A (Opus medium) | $4.79–4.98, ~120 turns, 864–897 s | — |
| v4 with quotes (default) | brief Q 99.9, claim Q 40.8; 93 of 229 major claims quoted; must-correct 28/28, correct-or-declare 29/30; 1 **silent miss** candidate; $4.27 + recall $0.53; 67 turns | brief Q 100, claim Q 100, 23/23; $1.46 + recall $0.10; peak context 142k |

The brief is a large lever: the same writers and fix round on the Stage A brief score 89.0; on the default brief, 94.1, at about equal cost. The lean v4 brief without quotes scores 1.8 less.

**Noise.** K1 over 6 runs: Q standard deviation 0.85. Differences under about 2 Q between single runs are not signal.

**Where K1 still loses.** Of the 60 stale quotes K1 left (all listed in the brief but not quoted), 53% are a restatement on another bundle's page, 32% an unswept sibling sentence on the writer's own page, 15% brief misses. Cross-page restatement is the main follow-up (§16).

**The knee.** Marginal gain is 3.8 Q per dollar from legacy to K1, and 0.18 Q per dollar from K1 to O-med. Verifiers add 0.09–0.23 Q per dollar (Sonnet) or lose Q (Opus). K1 is the knee on Q per dollar; under legacy parity (introduced errors only) the knee would be O-med. K1 costs ≈1.8× legacy on the big backlog and ≈0.4× on the small range; no eligible setting reached "a fraction of legacy cost" on the big backlog. K1 is the default although it fails legacy parity. On 2026-10-02 the operator delegated config defaults ("use the defaults you think would work best"), and the orchestrator chose K1 for its false-statements-left count and price; O-med is one knob away (`writerModel`). The [ADR](../adr/0001-ingest-v3-pipeline.md) records this as a deliberate exception.

**Not measured.** A full end-to-end run with live analysis and recall feeding the writers (downstream was measured on frozen briefs); an accuracy audit on the small fixture; the reviewer's §5 checks on a finalist; anything above O-med.

**Fixture gap.** Runs repeatedly flag pages outside the fixtures' affected sets that look stale (about 8 on the big fixture, 1 on the small), unconfirmed by a reviewer. The fixtures were built from pages earlier briefs flagged, so Q cannot reward recall beyond them, and v3 may already fix these pages.

## 15. Validation: first real-install shakedown

Before v3 is offered to other projects, run it once on a real install on a real merge, end to end, with defaults, and check:

1. **Gap record:** the file parses and matches the schema; every claim's quote is on its page; every gap has a valid cause and source; `attempts` and `stuck` agree; carried gaps resolved this run are gone and listed in the log; no page is both corrected and a gap; every major brief page is corrected or a gap; `ranges[]` entries, if any, have leads.
2. **Union audit** of the written pages: no major writer-introduced error; the rate is reported next to the benchmark's.
3. **Reviewer checks** of the quality bar's §5 (sampled claims, anchor flags confirmed) on the must-correct pages.
4. **Cost and wall** against the run cap and deadline; per-stage cost and peak context against §11.
5. **Plan-usage movement** across the run (a $3.5 run moved the five-hour window by 5–6 points).
6. **Failure classes:** every agent failure in the run is classified infra or work, and the cursor moved as the cursor table says.
7. **No writes outside `llake/`**; no permission denials on owned pages.

A failed check is a bug against this spec, fixed before v3 is offered.

## 16. Follow-ups (not decided here)

- **Tuning levers not adopted** in the downstream sweep, kept for later: bundles of 1 or 8, bundling by theme, weight cap 200, a bundle-only catalog for writers, a writer-side restatement sweep (+0.5 Q, not adopted), writer fact digests, a second writer wave, Sonnet low or high effort for writers, a quality ceiling above O-med.
- **Cross-page restatement propagation:** the largest residual loss (§14).
- **Checker false positives and gaps** (§10).
- **Systematic writer-error classes** (§14) turned into contract rules or a lint.
- **Batching-gate cadence** for v3: larger, rarer runs change cost per merged line.
- **Plan usage against list price:** list estimates are the cost axis; plan-usage movement is only recorded.
- **LLM judge** for reviewer checks, once enough human verdicts exist.
- **Fixture extension** with reviewer verdicts on the out-of-fixture pages runs flag (§14).
- **Verifier recall:** a single verification pass misses too much to be worth its cost.

## 17. What transfers to bootstrap and capture (notes only)

No action in this effort. Candidates for a later look:

- per-writer permission-level surfaces plus a post-write surface check;
- snapshot, diff and revert around every agent write;
- the 5-minute cache TTL, shared-prefix ordering and prefix warm-up;
- code-owned bookkeeping (`updated:`, indexes, log entry);
- the writer contract's "leads, not facts" and anchor rules;
- infra/work failure classes.

## 18. Implementation notes for planning

**Components** (named by responsibility, not file layout):

- an orchestrator script called by `post-merge.sh` when `ingest.pipeline` is `v3`, under the existing lock and recursion guard;
- run planning: gate rule, run kinds, failure counter, split, journal recovery;
- name derivation, hit index, change leads, input staging;
- one agent-spawn helper: flags, surface, deadline clipping, stream summary, infra/work class, cost ledger;
- brief assembly and validation; bundling;
- writer dispatch: run cap reservation, prefix warm-up, snapshots, singleton retry, infra stop;
- $0 checks; fix round; optional verifier;
- finalize: `updated:`, indexes, gap record, log entry, cursor and clock;
- prompt templates: analysis, recall, writer shared prefix, writer bundle part, verifier;
- JSON schemas: brief, writer status, verifier findings, gap record.

**Tests the plan must include:** range split and single-commit skip; stuck after 3 attempts; infra/work classification; journal recovery after a mid-write crash; the cursor table rows; run-cap reservation; renderer strictness for every new placeholder; bash 3.2 portability of shell code.

**Touch points:** doctor validates `llake/ingest-gaps.json` and reports stuck gaps and `ranges[]` entries; bootstrap resets the gap record; `ingest.v3.*` defaults go into `templates/config.default.json`; the plugin version is bumped in `plugin.json` and `marketplace.json`.
