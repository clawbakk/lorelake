---
title: "/llake-bootstrap — Initial Wiki Population"
description: "One-time in-session orchestrator that populates the wiki from scratch via parallel subagents"
tags: [skills, bootstrap, wiki]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[llake-lady-skill]]"
  - "[[llake-doctor-skill]]"
  - "[[three-writer-model]]"
  - "[[config-schema]]"
  - "[[runtime-layout]]"
---

# /llake-bootstrap — Initial Wiki Population

## Overview

`/llake-bootstrap` is the one-time orchestrator that populates a fresh LoreLake wiki from scratch. It runs in the user's **foreground** Claude Code session — no background process, no `claude -p` spawn, no separate budget. The user sees progress as it happens and can interrupt or course-correct at any point.

The skill reads `ingest.include` from the project's `config.json`, decomposes the in-scope codebase into focused subagent-sized tasks, dispatches those tasks in parallel via the `Task` tool, and then runs a cross-wiki consistency pass before finalizing. When it completes, a new contributor reading only the wiki should be able to start work on any in-scope area without flagging a senior engineer.

After bootstrap, ongoing wiki maintenance is handled automatically by the `post-merge` ingest hook — see [[three-writer-model]].

---

## When to use it

- Once, after `/llake-lady` has installed LoreLake and `/llake-doctor` reports the install is healthy.
- The wiki must be in a "fresh" state (only the four category stub indexes, no prior bootstrap entry in `log.md`).

Do **not** run it if bootstrap has already completed — a terminal `bootstrap` entry in `log.md` means it is done, and ongoing ingest takes over. To start over, manually delete `llake/wiki/` and `llake/log.md`, then re-invoke.

---

## Preconditions (Phase 1 — four hard stops)

Bootstrap checks four preconditions in order and refuses on any failure:

1. **`llake/config.json` exists.** If missing: "No LoreLake install found. Run `/llake-lady` first."
2. **Git repo with at least one commit.** `git rev-parse HEAD` must succeed. Bootstrap writes `last-ingest-sha` at the end, which requires a real HEAD commit.
3. **`llake/last-ingest-sha` file exists.** If missing: "Your install is incomplete. Run `/llake-doctor` first."
4. **`ingest.include` is a non-empty array.** If missing or empty: edit `config.json` to list the paths to document, then re-run.

---

## Wiki state classification

After the four hard stops, Phase 1 classifies the current wiki state and routes accordingly:

| State | Condition | Action |
|---|---|---|
| **Fresh** | `log.md` missing or empty; wiki has only the four stub indexes | Proceed to Phase 2 |
| **Partial (resumable)** | `bootstrap-task` entries in `log.md` but no terminal `bootstrap` entry | Ask user: Resume (reuse existing pages, plan the remainder) or Start Over (delete pages, replan from scratch) |
| **Complete** | Terminal `bootstrap` entry in `log.md` | Refuse — ingest takes over |
| **Dirty** | Wiki has pages beyond the four stubs with no matching `bootstrap-task` entries | Refuse — manual edits detected; user must clarify |

A failed precondition is always better than a half-bootstrapped wiki.

---

## The five bootstrap phases

### Phase 2 — Read inputs
Loads the minimum context needed to plan: `config.json` (`ingest.include` only), `README.md`, `CLAUDE.md`, top-level package manifests, `llake/index.md`, and the relevant `schema/` files (`schema/core.md` and `schema/code-content-standard.md`). Does **not** recursively read every source file — subagents do their own detailed reads within their assigned scope.

### Phase 3 — Build the task plan (orchestrator-private)
Decomposes the codebase into focused tasks. Each task gets:
- **Scope paths** — the `ingest.include` subset the subagent may read.
- **Assigned slugs** — exact page slugs the subagent must produce (globally unique across the wiki).
- **Category** — which `wiki/<cat>/` each page belongs to.
- **Expected outbound wikilinks** — slugs of pages other tasks will produce, so cross-references resolve.

The plan is **not written to `log.md`** up front — `log.md` records only completed work. Logging intent ahead of time would misrepresent state if the session is interrupted, and the tail of `log.md` is the truthful resume cursor.

Plan invariants:
- No duplicate slugs (two tasks for the same slug would race and one would clobber the other).
- No orphaned references (every planned outbound link points at a slug assigned to some task or already on disk).
- Every top-level path in `ingest.include` is covered by at least one task.
- Monorepo layout is committed up front: cross-cutting content at `wiki/<cat>/`, package content nested under `wiki/packages/<pkg>/`.

### Phase 4 — Dispatch subagents
Dispatches independent tasks in parallel via multiple `Task` calls in a single message. Each subagent prompt is self-contained (the subagent cannot see the orchestrator's conversation) and includes:
- The exact list of page slugs to produce.
- The scope paths the subagent may read.
- The slugs of pages other subagents are producing (for cross-references).
- Page-format rules from `schema/core.md` pasted verbatim.
- Standard 1 (new-employee test) and Standard 3 (no sensitive data) pasted verbatim.
- Write-surface restrictions pasted verbatim.

As each subagent returns a JSON manifest, the orchestrator appends a `bootstrap-task` entry to `log.md`:
```markdown
## [YYYY-MM-DD] bootstrap-task | <short description>

<2-3 sentence summary of what the subagent produced>

Pages affected: [[slug-1]], [[slug-2]], [[slug-3]]
```

Only completed work is logged. A failed subagent does not get a `bootstrap-task` entry. Subagents never write to `wiki/discussions/**` — that namespace belongs to session-capture.

### Phase 5 — Consistency and cross-link pass (orchestrator-owned)
The orchestrator walks the aggregated manifests and performs five checks on the produced pages:
1. **Every `[[wikilink]]` resolves** — broken links are fixed by editing the source or dispatching a subagent to write the missing target.
2. **`related:` frontmatter is bidirectional** — if page A lists `[[B]]`, page B must list `[[A]]`.
3. **No duplicates or contradictions** — near-identical titles, overlapping tags, or conflicting claims are merged or resolved.
4. **Terminology is consistent** — one canonical name per concept across the wiki.
5. **New-employee spot check** — a sample of pages is audited against Standard 1; structure-only pages are dispatched for fleshing out.

After the pass, a `bootstrap-consistency` entry is appended to `log.md`.

### Phase 6 — Finalize
Only runs if Phase 5 finishes cleanly. Partial finalization would corrupt future post-merge ingest.

1. **Write `llake/index.md`** — catalog of all categories now present.
2. **Write every category index** (`wiki/<cat>/<cat>.md`) — one-line summaries of every page from each page's `description:` frontmatter.
3. **Write `llake/last-ingest-sha`** — `git rev-parse HEAD` written as a single SHA. This is the ingest cursor: every commit after this SHA is the post-merge hook's backlog.
4. **Append the terminal `bootstrap` entry** — this is written **last**. Its presence marks bootstrap as complete; its absence (with `bootstrap-task` entries present) marks a partial state available for resume.
5. **Print the completion summary** — project path, scope, page count, categories, `last-ingest-sha`, and next steps.

---

## No `bootstrap.*` config keys

Bootstrap has no per-session config knobs of its own. Model, reasoning effort, token budget, and timeout all come from the user's active Claude Code session — that is the design. The user configured those when they started Claude Code. Only `ingest.include` is consulted from `config.json`. See [[config-schema]] for the keys that do control other agents (`ingest.*`, `sessionCapture.*`, `lint.*`).

---

## Write surface

| Allowed | Forbidden |
|---|---|
| `wiki/**` (any category, including new project-specific ones) | `wiki/discussions/**` — owned by session-capture |
| `llake/index.md` and category indexes | `schema/**` — immutable to agents |
| `llake/log.md` (append-only) | `config.json` |
| `llake/last-ingest-sha` (set exactly once, Phase 6 step 3) | `.state/**` |
| | Anything outside `<project>/llake/` |

---

## Key Points

- Bootstrap runs **in the foreground session** — no background process, no extra budget. The model and limits are whatever the user set when starting Claude Code.
- The orchestrator plans, dispatches, and reconciles. Subagents write their assigned pages. The orchestrator owns global coherence.
- `log.md` records only *completed* work. The tail of `log.md` is the truthful resume cursor for interrupted runs.
- The terminal `bootstrap` entry in `log.md` is written **last** — its presence or absence is how Phase 1 distinguishes "complete" from "partial/resumable".
- After bootstrap, `last-ingest-sha` is set to HEAD. Every subsequent merge on the configured branch is processed automatically by the post-merge hook — bootstrap is a one-time operation.
- Subagents never touch `wiki/discussions/**` — that namespace is session-capture's alone.

---

## What to do if it fails

| Symptom | Likely cause | Fix |
|---|---|---|
| "No LoreLake install found" | `llake/config.json` missing | Run `/llake-lady` first |
| "Not a git repo or no commits yet" | No HEAD commit | `git commit` at least once, then re-run |
| "`last-ingest-sha` is missing" | Incomplete install | Run `/llake-doctor` |
| "`ingest.include` is empty" | Config not configured | Edit `llake/config.json` to list paths |
| "Bootstrap has already completed" | Terminal `bootstrap` entry in `log.md` | Use ongoing ingest; or delete `llake/wiki/` and `llake/log.md` to start over |
| "Wiki contains pages not written by bootstrap" | Manual edits before bootstrap | Remove unexpected pages or move them aside, then re-run |
| Subagent fails mid-run | Scope or slug problem | Review subagent error in the session; re-dispatch with a corrected prompt |
| Partial state on re-run | Prior run was interrupted | Bootstrap detects partial state and offers Resume or Start Over |

---

## Code References

- `skills/llake-bootstrap/SKILL.md` — full orchestrator spec (all six phases, subagent prompt template, write surface, allowed tools)
- `schema/core.md` — page format, write surface, monorepo rule, naming conventions, indexing
- `schema/code-content-standard.md` — Standard 1 (new-employee test) and Standard 3 (no sensitive data)
- `hooks/post-merge.sh` — the hook that takes over after bootstrap completes
- `hooks/lib/read-config.py` — config layering used to read `ingest.include`

---

## See Also

- [[three-writer-model]] — how bootstrap, ingest, and capture relate as the three wiki writers
- [[llake-lady-skill]] — must run before bootstrap; creates the `llake/` structure
- [[llake-doctor-skill]] — run after `/llake-lady` and before bootstrap to verify install health
- [[config-schema]] — `ingest.include` and other config knobs
- [[runtime-layout]] — `last-ingest-sha`, `log.md`, and the full directory tree bootstrap populates
- [[post-merge-hook]] — the hook that handles ongoing wiki updates after bootstrap
