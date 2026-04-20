---
title: "LoreLake Schema — Operations"
description: "Workflow descriptions (human reference, NOT loaded at runtime)"
---

# Operations

These descriptions are for **plugin maintainers and curious users** — they are NOT loaded by any writer at runtime. Each writer is governed by its own SKILL.md (bootstrap) or prompt template (ingest, capture). This file documents how those writers behave at a high level so a maintainer can understand the system without reading the prompts in detail.

## Bootstrap

**Trigger:** Manual — user invokes `/llake-bootstrap` after install.

**One-time** — bootstrap is not a recurring tool.

**Process:**

1. Build a smart task plan that decomposes the project (file tree, package manifests, top-level dirs, `CLAUDE.md`, README) into subagent-sized tasks, with page slugs chosen up front for cross-link resolvability.
2. Delegate each task to a subagent (`Task` tool) that reads the relevant source and writes the corresponding wiki pages following Standards 1 + 3.
3. Coordinate cross-linking — every page that references another should use `[[wikilinks]]`; subagents return manifests of references and pages written; the orchestrator owns global coherence (no broken links, no duplicates, no contradictions, consistent terminology).
4. Finalize: write `index.md`, write category index files (`wiki/<cat>/<cat>.md`), append a `bootstrap` entry to `log.md`.
5. Set `last-ingest-sha` to current `git rev-parse HEAD`.

**Invariants after bootstrap:**

- The wiki paints a full picture of the project per Standard 1.
- Every page has at least one inbound `[[wikilink]]`.
- Every `[[wikilink]]` resolves to an existing page.
- `index.md` lists every category that has pages; every category index lists every page in that category.
- `last-ingest-sha` is set so post-merge ingest can take over.

## Ingest

**Trigger:** Automated — git `post-merge` hook detects new commits on the configured branch and spawns a background Claude CLI agent.

**Process:**

1. `git log --oneline <last-ingested>..HEAD --name-status -- <include-paths>` to see what changed.
2. Read changed files, understand semantic value of each change.
3. Map changes to wiki categories; check if existing pages are stale; check if new gotchas, patterns, or decisions emerged.
4. Update affected pages per Standards 1 + 3.
5. Update category indexes for touched categories.
6. Append entry to `log.md`.
7. Write the new HEAD SHA to `llake/last-ingest-sha`.

**Invariants after ingest:**

- Category indexes reflect all pages.
- Root `index.md` lists all categories.
- No orphan pages (every page has at least one inbound link).
- All `updated:` dates are current for modified pages.
- `log.md` has an entry for this ingest with the commit SHA range.

## Session capture

**Trigger:** Automated — Claude Code `SessionEnd` hook extracts the session transcript and spawns a triage→capture two-pass agent.

**Triage pass** classifies the session as `CAPTURE`, `PARTIAL`, or `SKIP`. SKIP means the agent does nothing.

**Capture pass** (only if triage was CAPTURE or PARTIAL):

1. Read the extracted transcript from `llake/.state/sessions/<session-id>/transcript.md`.
2. Extract knowledge per Standards 2 + 3:
   - **Decisions made** → create/update entries in `wiki/decisions/`
   - **Bugs fixed / issues resolved** → add to `wiki/playbook/` or `wiki/gotchas/`
   - **Architectural changes** discussed → captured ONLY in the discussion entry's Key Facts block (not in other category pages — those are ingest's domain when the code lands)
3. Create or append the immutable discussion entry in `wiki/discussions/`.
4. Update relevant category indexes.
5. Append entry to `log.md`.

## Lint

**Trigger:** On-demand — user asks: "lint LoreLake."

**Process:**

1. **Orphan check:** find pages with no inbound links.
2. **Stale check:** find pages where `updated` is >30 days old AND source files have changed since.
3. **Index sync:** verify every page in `wiki/` has an entry in its category index.
4. **Link check:** verify all `[[wikilinks]]` resolve.
5. **Tag consistency:** check for tag typos.
6. **Content gaps:** look for concepts mentioned but lacking their own page.
7. **Contradiction scan:** check for pages making conflicting claims.
8. **Security scan:** grep for credential-shaped strings (high-entropy, key-like patterns) per Standard 3.

Output a report. Fix issues immediately or note them in `log.md`.
