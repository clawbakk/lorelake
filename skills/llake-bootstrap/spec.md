# Skill: /llake-bootstrap

> Use this file as input for `/skill-creator`. Source of truth: `docs/specs/2026-04-18-lorelake-plugin-design.md` — section 8.4.

## Identity

- **Slash command:** `/llake-bootstrap`
- **Name:** LoreLake Bootstrap
- **Purpose:** Populate a freshly-installed LoreLake with a comprehensive initial wiki covering the configured codebase scope.
- **Frontmatter required:** `disable-model-invocation: true`.

## What it does

Performs a one-time, full-project ingest **inside the user's active Claude Code session.** The session itself is the orchestrator: it reads the codebase, decomposes the work, and dispatches subagents (via the `Task` tool) to write the wiki pages in parallel. There is **no background process, no detached `claude -p` agent, and no separate budget** — bootstrap runs at whatever model and budget the user's CC session is configured with.

After completion, the wiki must paint a full, comprehensive picture of the in-scope codebase per the new-employee test in `schema/code-content-standard.md` (Standard 1), and must contain no sensitive data per Standard 3 in the same file.

## When to use

- After `/llake-lady` has installed LoreLake.
- Before relying on session-capture or post-merge ingest to maintain the wiki.
- One-time only — bootstrap is not a recurring tool. For ongoing updates, post-merge ingest takes over.

## Scope of analysis

The skill reads `ingest.include` from `<project>/llake/config.json` and analyzes **only** the paths listed there. Anything outside `ingest.include` — generated artifacts, vendored deps, scratch dirs, anything the user excluded — is invisible to bootstrap. This is the same scope future post-merge ingest will track, so the user only ever configures it once.

Within that scope, the analysis must be **comprehensive**: every meaningful subsystem, module, configuration block, integration seam, and convention must end up represented in the wiki. Pages must capture three things: **structure** (what the thing is), **semantic value** (why it matters, what role it plays in the project, what would break if it changed), and **concrete examples when applicable** (code snippets, sample config, payload shapes, worked invocations — preferring real examples from tests/fixtures/docstrings over invented ones). A new contributor reading only the wiki should be able to start work on any in-scope area without flagging a senior engineer.

## Inputs

- The user's current working directory (must contain a populated `llake/`).
- `<project>/llake/config.json` — only `ingest.include` is consulted.
- Plugin's `schema/core.md` — page format, write surface, monorepo rule, naming, indexing.
- Plugin's `schema/code-content-standard.md` — Standard 1 (new-employee test) and Standard 3 (security).
- The codebase under each path in `ingest.include`.

## Flow (executed by the skill in-session)

1. **Detect & validate.**
   - Determine the project root (`lib/detect-project-root.sh` semantics).
   - Confirm `<project>/llake/config.json` exists.
   - Inspect `<project>/llake/log.md`:
     - If a terminal `bootstrap` entry is present → bootstrap is complete; refuse and direct the user to ingest.
     - If `bootstrap-task` entries are present but no terminal `bootstrap` entry → **partial state**; offer the user the choice to resume (skip already-completed tasks) or start over (delete the partial wiki and re-plan).
     - Otherwise the wiki should contain only the four stub category indexes; any extra page files in that case suggest manual edits — refuse and ask the user to clarify.
   - Confirm `<project>/llake/last-ingest-sha` exists.
   - Confirm the project is a git repo (otherwise `last-ingest-sha` cannot be set at the end).
   - On any failure: print a clear error naming the missing prerequisite and exit. Do not start dispatching subagents.

2. **Read inputs.** Read `ingest.include` from `<project>/llake/config.json`. Read `README.md`, (and CLAUDE.md which is already injected in the session), top-level package manifests, the current `index.md` (only the four fixed categories at this point), and the plugin's `schema/core.md` + `schema/code-content-standard.md`. Do NOT load `schema/operations.md` — that file is human reference only.

3. **Build a smart task plan (in-session).** Decompose the in-scope codebase into focused, subagent-sized tasks. Each task owns a defined scope (one subsystem, one module group, one configuration domain) and produces a **defined, named set of wiki pages with slugs chosen up front**. Choosing slugs in the plan (not letting subagents invent them) is what makes cross-references between subagents resolvable: subagent A can write `[[some-other-thing]]` knowing subagent B will produce `wiki/<cat>/some-other-thing.md`. The plan must also guarantee **no two subagents are assigned the same topic** — a duplicate page is worse than a missing one. Include coordination tasks: write `index.md`, write category index files, run the consistency & cross-link pass.

   **Do NOT log the plan to `log.md` upfront.** `log.md` records what *actually happened*, not intent. Logging the plan before dispatch would misrepresent state if the session is interrupted. The plan lives in the orchestrator's session memory; if a resume is needed, validation in step 1 reconstructs progress from the existing `bootstrap-task` entries plus the current wiki contents.

4. **Dispatch subagents (`Task` tool).** For each task in the plan, spawn a subagent with:
   - The scope (the directory or files it owns, restricted to `ingest.include` paths).
   - The exact slugs of the pages it must produce.
   - The slugs of any other planned pages it should `[[wikilink]]` to (so cross-references resolve when both ends land).
   - Naming/terminology rules from `schema/core.md`.
   - The Standard 1 (new-employee test) and Standard 3 (no sensitive data) excerpts from `schema/code-content-standard.md`.
   - **Write-surface rules:** the subagent may write only to pages within its assigned slugs under `wiki/<cat>/`. **It must NOT write to `wiki/discussions/**`** (that namespace is owned by session-capture).
   - Instructions to return a JSON manifest: `{ "pages_written": [...], "references_made": [...] }`.

   Run independent subagents in parallel where possible. **As each task returns, append a `bootstrap-task` entry to `<project>/llake/log.md`** capturing the task name, scope, and pages written. This way `log.md` reflects only completed work, and the tail of `log.md` is a true progress cursor for any future resume. Aggregate manifests as they come back. The orchestrator never has to hold every page's content in its own context, but it **does** own the global view.

5. **Consistency & cross-link pass — orchestrator's responsibility.** Subagents only see their own slice; **the orchestrator (this skill, in the user's session) owns global coherence.** The wiki must read as a single, consistent body of work — not a stack of independently-written silos. Using the aggregated manifests:
   - **Every `[[wikilink]]` resolves.** Walk every outbound reference and confirm the target page exists with the expected slug. Fix any broken link by either updating the source's wording or dispatching a subagent to write the missing page.
   - **`related:` frontmatter is bidirectional.** If page A's `related:` lists `[[B]]`, page B's must list `[[A]]`. Reconcile both ends.
   - **No duplicates, no contradictions.** Scan for two pages claiming to be the canonical home for the same concept; merge or rename. Scan for pages making conflicting claims (different signatures, conflicting invariants, contradictory dependency directions); resolve by re-reading source and editing the wrong page.
   - **Terminology is consistent across the wiki.** The same concept must be named the same way everywhere (e.g., not "tick loop" on one page and "main loop" on another). Pick one, fix the others.
   - **Spot-check the new-employee test.** Sample pages and ask: would a new hire's questions be answered? Dispatch a focused subagent to flesh out anything that falls short.

   On completion, append a `bootstrap-consistency` entry to `log.md`.

6. **Finalize.**
   - Write `<project>/llake/index.md` listing every category that has pages (fixed + new).
   - Write each category index (`wiki/<cat>/<cat>.md`) listing every page with one-line summaries.
   - Write `git rev-parse HEAD` (run from `<project>`) to `<project>/llake/last-ingest-sha` so post-merge ingest can take over.
   - **Last:** append a terminal `bootstrap` entry to `log.md` summarizing the run (page count, category count). Its presence is what marks bootstrap as complete on future invocations; its absence (with `bootstrap-task` entries present) indicates a partial state available for resume.

## Configuration knobs

The skill reads exactly **one** key from `<project>/llake/config.json`:

- `ingest.include` — the paths to analyze.

There are **no** `bootstrap.*` config keys. Model, effort, budget, and timeout are all governed by the user's active CC session — bootstrap inherits them.

## Write surface (matches ingest)

| Allowed | Forbidden |
|---|---|
| `wiki/**` (any category, including new project-specific ones the plan creates) | **`wiki/discussions/**`** — owned exclusively by session-capture; bootstrap-generated content there would corrupt the discussion record |
| `index.md`, category index files (`wiki/<cat>/<cat>.md`) | `schema/**` — schema files are immutable to agents |
| `log.md` (append-only) | `config.json` — configuration |
| `last-ingest-sha` (set once at finalize) | `.state/**` — runtime working dir |
| | Anything outside `<project>/llake/` |

This restriction also applies to every subagent the skill dispatches — it is included in their prompt.

## Allowed tools

- `Read`, `Glob`, `Grep` — survey the codebase and the existing LoreLake.
- `Bash` — `git rev-parse HEAD`, light shell utilities. **Not** used to spawn detached agents.
- `Write`, `Edit` — write `log.md`, `index.md`, `last-ingest-sha`, category index files; subagents write the wiki pages within their assigned scope.
- `Task` — dispatch subagents for per-scope wiki writing and cross-link spot-checks.

## Validation (before dispatching subagents)

- `<project>/llake/config.json` exists and contains a non-empty `ingest.include`.
- `<project>/llake/wiki/` exists and contains only the four stub category indexes.
- `<project>/llake/last-ingest-sha` exists.
- The project is a git repo.

If any check fails: print a clear error naming the missing prerequisite and the fix command. Do NOT dispatch.

## Output

After bootstrap completes, the skill prints a summary in the user's session:

```
LoreLake Bootstrap — Complete

Project:           /path/to/project
Scope:             <ingest.include paths>
Pages written:     <count>
Categories:        <list>
last-ingest-sha:   <sha>

Next steps:
- Browse the wiki at <project>/llake/wiki/
- Run /llake-doctor to verify integrity
- Future commits on the configured branch will be processed automatically by the post-merge hook
```

If orchestration fails mid-run: surface the failure inline (subagent error, partial state, what was written so far). Because the skill runs in the user's foreground session, recovery and re-runs are interactive — there is no separate log file to grep.

## Behaviors out of scope

- Re-running on a populated wiki (refuse and direct the user to ingest instead).
- Editing `<project>/CLAUDE.md`.
- Wiring hooks (that is `/llake-doctor`).
- Reading any `bootstrap.*` config keys (none exist).
- Spawning a detached or background `claude -p` agent (the skill runs in the user's foreground session and uses `Task` for delegation).
- Analyzing code outside `ingest.include`.

## Reference

- Design spec section 8.4 — bootstrap mental model and flow.
- `schema/code-content-standard.md` Standard 1 — Code-content completeness (the new-employee test).
- `schema/code-content-standard.md` Standard 3 — Security (no sensitive data).
- `schema/core.md` — page format, write surface, monorepo rule, naming, indexing.
