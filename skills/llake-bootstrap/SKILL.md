---
name: llake-bootstrap
description: Use when populating a fresh LoreLake install with an initial wiki from the configured codebase scope. Invoked via /llake-bootstrap.
disable-model-invocation: true
---

# LoreLake Bootstrap — Initial Wiki Population

The user invoked `/llake-bootstrap`. Your job, running here in their active Claude Code session, is to populate the project's LoreLake wiki from scratch with a comprehensive, coherent body of pages covering every in-scope subsystem. When you are done, a new contributor reading only the wiki should be able to start work on any in-scope area without flagging a senior engineer.

You are an **orchestrator, not a writer**. You plan the work, dispatch subagents via the `Task` tool to produce the pages in parallel, then reconcile the result into a single coherent wiki. Subagents own their slice; you own global coherence — cross-links, terminology, bidirectional `related:` frontmatter, no duplicates, no contradictions.

**No background process. No `claude -p` spawn. No separate budget.** Bootstrap runs in the foreground session at whatever model and limits the user configured when they started Claude Code. That is the design — the user sees everything as it happens, and they can interrupt or course-correct.

---

## Resolve paths before doing anything

Two absolute paths drive every phase. Compute them once at the start; use them literally thereafter.

- **`$PLUGIN_ROOT`** — where this skill lives. Claude Code exposes the skill's base directory at invocation (the `skills/llake-bootstrap/` directory). `$PLUGIN_ROOT` is two levels up from there. If you are unsure, resolve `realpath` against the SKILL.md path you were given and strip `/skills/llake-bootstrap/SKILL.md`.

- **`$PROJECT_ROOT`** — the user's current working directory (`pwd` at invocation). The project being bootstrapped.

Echo both paths to the user in a single line before Phase 1 so a wrong CWD is caught before any file is written.

---

## Phase 1 — Detect & validate

Bootstrap refuses to run against an invalid or already-populated install. Collect prerequisites first; on any hard failure, print a clear error naming the missing prerequisite and exit without dispatching subagents.

Run every check in order:

1. **LoreLake install present.** `$PROJECT_ROOT/llake/config.json` must exist. If missing: stop. "No LoreLake install found at `$PROJECT_ROOT`. Run `/llake-lady` first."

2. **Git repo.** `git -C "$PROJECT_ROOT" rev-parse HEAD` must succeed. If it fails: stop. "`$PROJECT_ROOT` is not a git repo (or has no commits yet). Bootstrap writes `last-ingest-sha` at the end, which requires a HEAD commit. Commit at least once, then re-run."

3. **`last-ingest-sha` file exists.** `$PROJECT_ROOT/llake/last-ingest-sha` must exist (contents can be empty). If missing: stop. "`llake/last-ingest-sha` is missing — your install is incomplete. Run `/llake-doctor` first."

4. **`ingest.include` is non-empty.** Parse `config.json`; `ingest.include` must be a non-empty array of paths. If missing or empty: stop. "`config.json` has an empty `ingest.include`. Bootstrap has nothing to scan. Edit the file to list the paths you want documented, then re-run."

5. **Wiki state classification.** Read `$PROJECT_ROOT/llake/log.md` if present, and inspect `$PROJECT_ROOT/llake/wiki/`. Three valid states, one invalid:

   | Condition | State | Action |
   |---|---|---|
   | A terminal `bootstrap` entry is present in `log.md` | **Complete** | Refuse. "LoreLake bootstrap has already completed for this project (see `log.md`). For ongoing updates, post-merge ingest takes over. Re-running bootstrap is not supported — if you truly want to start over, delete `llake/wiki/` and `llake/log.md` manually first." |
   | `bootstrap-task` entries are present but no terminal `bootstrap` entry | **Partial (resumable)** | See step 6 below. |
   | `log.md` is missing or empty AND `wiki/` contains only the four category stub indexes (`discussions/discussions.md`, `decisions/decisions.md`, `gotchas/gotchas.md`, `playbook/playbook.md`) | **Fresh** | Proceed to Phase 2. |
   | `wiki/` contains any page beyond the four stubs AND there are no `bootstrap-task` entries | **Dirty (manual edits)** | Refuse. "The wiki contains pages that weren't written by bootstrap (no matching `bootstrap-task` entries in `log.md`). This suggests manual edits. Please clarify: either delete the unexpected pages and re-run, or move them aside so bootstrap has a clean slate." |

6. **Partial-state branch (resumable).** Count the `bootstrap-task` entries in `log.md` and list the pages already on disk under `wiki/` that aren't the four stub indexes. Show a short summary to the user, then ask — use the `AskUserQuestion` tool — how to proceed:

   - **Resume** *(recommended when the partial set is non-trivial)* — reuse what's already written, plan only the remainder, continue.
   - **Start over** — delete everything under `wiki/` except the four stub category indexes, truncate `log.md` back to before the first `bootstrap-task` entry, replan from scratch.

   On Resume, treat the already-written pages as fixed: the plan in Phase 3 must not reassign their slugs, and their content is authoritative for the consistency pass in Phase 5. On Start over, perform the deletes, then proceed as Fresh.

Only after every check passes do you move on. A failed precondition is always better than a half-bootstrapped wiki.

---

## Phase 2 — Read inputs

Load the minimum context needed to plan well. You are deliberately not loading every source file here — the subagents will read their own slices. You are building the *map*, not the territory.

Read once:

- `$PROJECT_ROOT/llake/config.json` — **only** `ingest.include`. Other keys are not consulted.
- `$PROJECT_ROOT/README.md` — purpose, top-level overview.
- `$PROJECT_ROOT/CLAUDE.md` — likely already injected in the session; re-read if you need specifics.
- Top-level package manifests that exist (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`) — project shape, workspaces, dependency surface.
- `$PROJECT_ROOT/llake/index.md` — current state (should list only the four fixed categories right now).
- `$PLUGIN_ROOT/schema/core.md` — page format, write surface, monorepo rule, naming, indexing.
- `$PLUGIN_ROOT/schema/code-content-standard.md` — Standard 1 (new-employee test) and Standard 3 (no sensitive data).

Do **not** load `$PLUGIN_ROOT/schema/operations.md`. That file is human-reference only; loading it into the orchestration context wastes tokens without improving the plan.

Do **not** recursively read every source file in `ingest.include` at this stage. Use `Glob` and targeted `Read`s to understand the shape of each top-level path — directory structure, module boundaries, entry points. The detailed reads happen inside subagents, where the scope is narrow and the context is fresh.

---

## Phase 3 — Build a smart task plan

This plan is the bootstrap's most important artifact. A weak plan produces a disjointed wiki full of duplicate pages and broken `[[wikilinks]]`; a strong plan produces a single coherent body of work. Take your time here.

The plan is **orchestrator-private**: it lives in your session memory only. **Do not write it to `log.md` up front.** `log.md` records work that actually happened; logging intent ahead of time misrepresents state if the session is interrupted, and makes resume harder (the tail of `log.md` would no longer be a truthful progress cursor). If a resume is ever needed, Phase 1 step 6 reconstructs progress from the existing `bootstrap-task` entries plus the current wiki contents.

The plan's job is to decompose the in-scope codebase into **focused, subagent-sized tasks**, each producing a **named set of wiki pages with slugs chosen up front**.

### What makes a task

Each task owns one coherent slice — a subsystem, a module group, a configuration domain, an integration seam, a cross-cutting concern. A good task is narrow enough that a subagent can finish it without branching into unrelated territory, and broad enough that it produces several related pages whose `[[wikilinks]]` to each other are easy for the subagent to make correct.

For every task, the plan records:

- **Scope paths** — the subset of `ingest.include` the subagent is allowed to read. Be explicit; subagents will honor this.
- **Assigned slugs** — the exact page slugs the subagent must produce (e.g., `["tick-loop", "event-dispatcher", "frame-scheduler"]`). Slugs are globally unique across the wiki.
- **Category** — which `wiki/<cat>/` each page belongs to. Use existing fixed categories where they fit; create project-specific categories where they don't. Apply the naming rules in `schema/core.md` (lowercase, singular, aligned with existing names — don't create `monitoring/` when `observability/` already exists).
- **Expected outbound `[[wikilinks]]`** — slugs of pages *other* tasks are planned to produce, which this task is likely to reference. This is what makes cross-references resolvable: subagent A can write `[[some-other-thing]]` knowing subagent B will produce `wiki/<cat>/some-other-thing.md`.

### Invariants the plan must satisfy

- **No duplicate slugs.** Two subagents assigned the same slug would race and one would clobber the other; a duplicate page is worse than a missing one.
- **No orphaned references.** Every planned outbound link points at a slug that is either (a) already on disk (resumed partial state) or (b) assigned to some other task in this plan. If neither, remove the reference or add the target as a new task.
- **Monorepo layout respected.** If the project is a monorepo (two or more project marker files at non-root paths — see `schema/core.md` "Monorepo detection"), nest categories under group dirs (`wiki/packages/<pkg>/...`, `wiki/apps/<app>/...`). Commit to that structure up front; retroactive restructuring is expensive.
- **Cross-cutting content stays at top-level.** Architecture, decisions, gotchas, playbook, discussions all live at `wiki/<cat>/` regardless of monorepo layout.
- **Coverage.** Every top-level path in `ingest.include` is reached by at least one task. Nothing in-scope is silently skipped.

### Include coordination tasks

The plan must also include:

- Writing `$PROJECT_ROOT/llake/index.md` (listing every category that has pages) — this is the orchestrator's own task for Phase 6.
- Writing each category index (`wiki/<cat>/<cat>.md`) with one-line summaries of every page in it — also orchestrator-owned, Phase 6.
- The consistency & cross-link pass — orchestrator-owned, Phase 5.

### If the plan is large

Prefer more focused tasks over fewer large ones. A task whose subagent is trying to document ten unrelated things will produce shallow pages; a task with three tightly related pages produces deep ones. The `Task` tool scales with parallelism.

---

## Phase 4 — Dispatch subagents

**Concurrency cap: at most 5 subagents running at once.** Send batches of up to 5 `Task` calls per message, wait for the batch to complete, then dispatch the next batch. For 11 tasks, the pattern is batch 1 (5), batch 2 (5), batch 3 (1). The cap is a hard limit — do not exceed 5 concurrent subagents under any circumstance. Rationale: provider rate limits, bounded session token usage, predictable wall-clock.

Within a batch, run subagents in parallel — a single message with multiple `Task` calls. Dispatch each subagent with a self-contained prompt (the subagent cannot see this conversation).

### Subagent prompt template

Every subagent prompt must include, at minimum:

```
You are writing wiki pages for LoreLake's bootstrap.

Task: <one-line task description>

Scope (you may Read only within these paths):
<list of absolute paths or project-relative paths from ingest.include>

Pages you must produce (exact slugs, exact categories):
- wiki/<cat>/<slug-1>.md
- wiki/<cat>/<slug-2>.md
...

Pages other subagents are producing, which you may reference via [[wikilink]]:
- [[other-slug-1]] (target: wiki/<cat>/other-slug-1.md)
- [[other-slug-2]] (target: wiki/<cat>/other-slug-2.md)
...

Page-format rules (from schema/core.md):
<paste the "Page format" section verbatim — frontmatter fields, required sections>

Naming rules (from schema/core.md):
<paste the "Naming conventions" section verbatim>

Content standards (non-negotiable):
- Standard 1 — Code-content completeness. Every page must hold up to the "new-employee test": a new contributor reading only the wiki should be able to start work on any in-scope area without flagging a senior engineer. Capture structure (what the thing is), semantic value (why it matters, what breaks if it changes), and concrete examples where applicable — prefer examples from tests/fixtures/docstrings over invented ones.
- Standard 3 — No sensitive data. No credentials, connection strings, internal hostnames or IPs, private URLs, personal data. Abstract ("the API key") instead of capturing values. When in doubt, abstract.

Write surface:
- You MAY write only the page files listed above under wiki/<cat>/.
- You MUST NOT write to wiki/discussions/** — that namespace is owned by session-capture.
- You MUST NOT write anywhere outside <project>/llake/wiki/.
- You MUST NOT edit config.json, schema/**, log.md, last-ingest-sha, .state/**, or any file outside <project>/llake/.

When done, return this JSON manifest (exactly this shape, no prose around it):

{
  "pages_written": [
    {"slug": "<slug>", "path": "wiki/<cat>/<slug>.md", "category": "<cat>", "title": "<title>", "description": "<one-line summary>"}
  ],
  "references_made": [
    {"from": "<slug>", "to": "<slug-or-external>", "resolved": true|false}
  ]
}
```

Fill the placeholders with that task's specifics. Paste the schema excerpts in full — subagents do not have time or context to look them up themselves, and the standards must not drift between tasks.

### Logging as work completes

As each subagent returns, **append a `bootstrap-task` entry to `$PROJECT_ROOT/llake/log.md`** using the format from `schema/core.md`:

```markdown
## [YYYY-MM-DD] bootstrap-task | <short description of the task>

<2-3 sentence summary of what the subagent produced>

Pages affected: [[slug-1]], [[slug-2]], [[slug-3]]
```

Append on completion, not on dispatch. `log.md` reflects only completed work — its tail is the truthful progress cursor for any future resume, and Phase 1 step 6 depends on that truth.

Aggregate each returned manifest in session memory. You will need all of them together for Phase 5. You never have to hold every page's *content* in your own context — the subagent's manifest is enough.

### If a subagent fails or returns a malformed manifest

Surface the failure inline in the user's session. Do not silently retry with the same prompt — a subagent that failed once usually reveals a scope or slug problem that needs adjustment. Options: shrink the scope, re-dispatch with a cleaner prompt, or mark the task failed and flag it in the final summary. Do not fabricate entries in `log.md` for work that didn't complete.

---

## Phase 5 — Consistency & cross-link pass (orchestrator-owned)

Each subagent only saw its own slice. The wiki must still read as a single, consistent body of work. This is your job, and it is not optional — skipping it is how wikis ossify into stacks of independently-written silos.

Using the aggregated manifests from Phase 4 and targeted `Read`s of the produced pages, walk through every check below. When you find an issue, fix it — either by editing the affected page directly (small fixes) or by dispatching a focused subagent (larger re-writes).

### Check 1 — Every `[[wikilink]]` resolves

Walk every outbound reference recorded in the manifests (and every `[[...]]` you find while reading the pages for other checks). For each:

- Target slug exists under `wiki/<cat>/<slug>.md` → OK.
- Target missing → either update the source page's wording to drop the reference, or dispatch a subagent to write the missing target. Prefer writing the target; the reference was planned for a reason.

### Check 2 — `related:` frontmatter is bidirectional

For each page A whose `related:` frontmatter lists `[[B]]`, confirm page B's `related:` lists `[[A]]`. Reconcile both ends — the relationship is symmetric by definition, and a one-sided link misleads readers who arrive from B.

### Check 3 — No duplicates, no contradictions

- **Duplicates.** Scan for two pages claiming to be the canonical home for the same concept. Merge or rename. A good tell is near-identical titles or overlapping tags.
- **Contradictions.** Scan for pages making conflicting claims — different function signatures, conflicting invariants, contradictory dependency directions. Resolve by re-reading the source and editing the wrong page.

### Check 4 — Terminology is consistent

Pick a canonical name for each concept and enforce it across the wiki. Don't let the same thing be called "tick loop" on one page and "main loop" on another — a reader searching for either will find only half the coverage. Pick one, fix the others.

### Check 5 — New-employee spot check

Sample a handful of pages (say, one from each category) and ask the question Standard 1 asks: would a new hire's follow-up questions be answered from this page and one or two `[[wikilinks]]` away? If a page feels structure-only — a function list, a class hierarchy without context — dispatch a focused subagent to flesh it out. The page is not "complete" just because it has content; it is complete when a reader can act on it.

### Log the pass

After the pass, append a `bootstrap-consistency` entry to `log.md` summarizing what was checked and what was fixed:

```markdown
## [YYYY-MM-DD] bootstrap-consistency | Cross-link and coherence pass

<summary of issues found and fixed — e.g., "Resolved 3 broken wikilinks, reconciled 2 one-sided related:, merged duplicate pages foo/bar into foo, renamed 'tick loop' → 'main loop' across 4 pages.">

Pages affected: [[page-1]], [[page-2]], ...
```

---

## Phase 6 — Finalize

Only run this phase if Phase 5 finished cleanly. Partial finalization would leave `last-ingest-sha` pointing at a commit whose wiki is incomplete, which would silently corrupt future post-merge ingest (ingest would skip the backlog, thinking it's already processed).

### Step 1 — Write `index.md`

Render `$PROJECT_ROOT/llake/index.md` as a catalog listing every category that now has pages — the four fixed categories (`discussions`, `decisions`, `gotchas`, `playbook`) plus every project-specific category created during this run, plus monorepo grouping dirs if the project is a monorepo. Each entry gets a one-line description.

### Step 2 — Write every category index

For each category `<cat>` that now has at least one page, write (or overwrite) `wiki/<cat>/<cat>.md` to list every page in it with a one-line summary (under 100 chars each). Use the one-line `description` from each page's frontmatter — that's why subagents set it. The stub "_No entries yet._" placeholder line is replaced by the real listing.

Under monorepo layouts, also write the group indexes (`wiki/packages/packages.md`, `wiki/apps/apps.md`, etc.) and each sub-category index (`wiki/packages/<pkg>/<pkg>.md`).

### Step 3 — Update `last-ingest-sha`

Run `git -C "$PROJECT_ROOT" rev-parse HEAD` and write the output (a single SHA, no trailing newline) to `$PROJECT_ROOT/llake/last-ingest-sha`. This is the cursor post-merge ingest uses on future merges — every commit after this SHA is the ingest agent's backlog.

### Step 4 — Append the terminal `bootstrap` entry

This is **last**. Its presence in `log.md` is what marks bootstrap as complete on any future invocation; its absence (with `bootstrap-task` entries present) is what marks a partial state available for resume. Writing it before the finalize steps is a bug — it would hide an incomplete run.

Append to `log.md`:

```markdown
## [YYYY-MM-DD] bootstrap | Initial wiki populated

Populated <N> pages across <M> categories (<list>). Scope: <ingest.include paths>. Future commits on the configured branch will be processed automatically by the post-merge ingest hook.

Pages affected: <N> total (see category indexes)
```

### Step 5 — Print the summary

Print to the user's session:

```
LoreLake Bootstrap — Complete

Project:           <absolute path>
Scope:             <ingest.include paths>
Pages written:     <count>
Categories:        <comma-separated list>
last-ingest-sha:   <sha>

Next steps:
- Browse the wiki at <project>/llake/wiki/
- Run /llake-doctor to verify integrity
- Future commits on the configured branch will be processed automatically by the post-merge hook
```

If orchestration failed mid-run (subagent errors, partial state you chose not to auto-recover, consistency pass failures you couldn't fix), surface the failure inline: what was written so far, what went wrong, what the user's options are. Because you run in the user's foreground session, recovery and re-runs are interactive — there is no separate log file to grep and no background process to babysit.

---

## Write surface

Subagents and the orchestrator share the same write surface. Include the restrictions below verbatim in every subagent prompt; they back up the tool-level allowances.

| Allowed | Forbidden |
|---|---|
| `wiki/**` — any category, including new project-specific ones created by the plan | `wiki/discussions/**` — owned exclusively by session-capture; bootstrap-generated content there would corrupt the discussion record |
| `index.md` and category index files (`wiki/<cat>/<cat>.md`) — written in Phase 6 | `schema/**` — immutable to agents |
| `log.md` — append-only, via `bootstrap-task`, `bootstrap-consistency`, and the terminal `bootstrap` entry | `config.json` |
| `last-ingest-sha` — set exactly once, in Phase 6 step 3 | `.state/**` — runtime working dir |
| | Anything outside `<project>/llake/` |

---

## Allowed tools

- `Read`, `Glob`, `Grep` — survey the codebase and the existing LoreLake.
- `Bash` — `git rev-parse HEAD`, light shell utilities. **Not** used to spawn detached agents.
- `Write`, `Edit` — write `log.md`, `index.md`, `last-ingest-sha`, category index files; fix up individual wiki pages during the consistency pass.
- `Task` — dispatch subagents for per-scope wiki writing and for focused fixes during Phase 5.
- `AskUserQuestion` — the Phase 1 resume-or-restart choice when a partial state is detected.

---

## Behaviors out of scope

- **Re-running on a populated wiki.** A terminal `bootstrap` entry in `log.md` means bootstrap is done; refuse and direct the user to ongoing ingest.
- **Editing `<project>/CLAUDE.md`.** Operating context is injected by the SessionStart hook — bootstrap never writes there.
- **Wiring hooks.** That is `/llake-doctor`'s job.
- **Reading `bootstrap.*` config keys.** None exist. Model, effort, budget, and timeout all come from the user's active CC session.
- **Spawning a detached or background `claude -p` agent.** Delegation is via the in-session `Task` tool.
- **Analyzing code outside `ingest.include`.** Whatever the user excluded is invisible.
- **Writing to `wiki/discussions/**`.** That namespace is session-capture's alone — bootstrap's hands off.
- **Populating an initial install from scratch.** If no `<project>/llake/` exists, bootstrap refuses and directs the user to `/llake-lady`.

## References

- Page format, write surface, monorepo rule, naming, indexing: `schema/core.md`.
- Standard 1 (new-employee test) and Standard 3 (no sensitive data): `schema/code-content-standard.md`.
- Sibling skills: `/llake-lady` (initial install), `/llake-doctor` (diagnose and repair).
