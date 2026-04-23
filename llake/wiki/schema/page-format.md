---
title: "Page Format"
description: "Complete page format spec: frontmatter, required sections, naming, categories, wikilinks"
tags: [schema, page-format, conventions]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[schema-overview]]"
  - "[[content-standards]]"
  - "[[runtime-layout]]"
---

# Page Format

## Overview

Every wiki page in LoreLake follows a single, strictly-enforced format. The format exists so that agents can read pages predictably, inject them into context windows efficiently, and maintain them consistently over time. This page is the complete reference for that format — frontmatter fields, required sections, naming conventions, category taxonomy, wikilink syntax, and the index architecture. It is derived from `schema/core.md`, which every writer loads at runtime.

## Frontmatter

Every page must begin with a YAML frontmatter block. Required and optional fields:

```yaml
---
title: "Page Title"
description: "One-line summary for category index (under 100 characters)"
tags: [category, topic, subtopic]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: current
related:
  - "[[page-name]]"
---
```

### Field reference

| Field | Required | Type | Allowed values / notes |
|---|---|---|---|
| `title` | Yes | String | Human-readable page title. Quoted. |
| `description` | Yes | String | One-line summary, under 100 characters. Used verbatim in category indexes. |
| `tags` | Yes | Array of strings | Lowercase, hyphen-separated. Category tag first (e.g., `schema`), then specifics. |
| `created` | Yes | Date (`YYYY-MM-DD`) | Date the page was first written. Never updated. |
| `updated` | Yes | Date (`YYYY-MM-DD`) | Date of the most recent substantive edit. Updated on every write. |
| `status` | Yes | Enum | `current` — up to date; `draft` — work in progress; `stale` — likely outdated, verify; `deprecated` — no longer relevant; `immutable` — never edited after creation (discussion entries). |
| `related` | No | Array of wikilink strings | Other pages that are closely related. Use wikilink syntax: `"[[slug]]"`. |
| `commits` | No | Array of strings | Short-SHA + commit message. Used in discussion entries. |

### Discussion-entry-only fields

Discussion entries (files in `wiki/discussions/`) include two additional frontmatter fields:

```yaml
status: immutable
commits:
  - "abc1234: Add retry logic to HTTP client"
  - "def5678: Update timeout defaults"
```

The `status: immutable` value signals that the Key Facts block must not be edited. `commits` records the git commits that were discussed in the session.

## Required sections

Every page must include these five sections in order:

### 1. Overview (or brief overview paragraph)

A short paragraph (2–5 sentences) immediately after the H1 title. Sets up what the page covers and why it matters. Does not need its own `## Overview` heading on short pages — an introductory paragraph before the first section is acceptable. On longer pages, use `## Overview` explicitly.

### 2. Main content

The substantive content of the page. Structure varies by page type (see [[content-standards]] for the nine questions a complete code-documenting page must answer). Use `##` and `###` headings to organize. Embed code snippets, configuration examples, and concrete illustrations wherever they add clarity.

### 3. Key Points

A `## Key Points` section containing a bulleted list of 3–8 facts a reader must retain after reading the page. These are the non-negotiable takeaways — the things most likely to matter when someone skims the page on a return visit.

```markdown
## Key Points

- The schema/ directory is immutable to all agents.
- Standard 3 overrides every other content guideline, including Key-Facts immutability.
- Wikilink slugs are globally unique across the entire wiki.
```

### 4. Code References

A `## Code References` section listing the specific files (and, where useful, line numbers) that back the content of this page. Format: `path/to/file:line` or just `path/to/file` when the whole file is relevant. This section makes it possible to check whether a page has gone stale after code changes.

```markdown
## Code References

- `schema/core.md` — page format and category taxonomy rules
- `hooks/lib/render-prompt.py:42` — placeholder substitution logic
- `hooks/post-merge.sh` — ingest trigger
```

### 5. See Also

A `## See Also` section with wikilinks to related pages. Minimum one link. This is the primary navigation mechanism inside the wiki.

```markdown
## See Also

- [[schema-overview]] — how the schema/ directory fits into the system
- [[content-standards]] — what "complete" means for a code-documenting page
- [[three-writer-model]] — the agents that follow these format rules
```

## Naming conventions

### Files

- Lowercase, hyphen-separated words. No uppercase, no underscores.
- Example: `tick-loop.md`, `http-client.md`, `session-end-hook.md`.

### Directories (categories)

- Lowercase, singular noun.
- Examples: `architecture/`, `strategy/`, `schema/`, `gotcha/`.
- The fixed categories are exceptions to strict singularity: `discussions/`, `decisions/`, `gotchas/`, `playbook/`.

### Tags

- Lowercase, hyphen-separated. Category tag first, then specifics.
- Example: tags for a page in `architecture/` about the tick loop: `[architecture, tick-loop, event-loop]`.

### Decision records

Named `adr-NNN-short-title.md` where NNN is a zero-padded three-digit sequence number.

```
wiki/decisions/adr-001-use-sqlite-for-state.md
wiki/decisions/adr-002-two-pass-triage.md
```

### Discussion entries

Named `YYYY-MM-DD-topic.md`. The date is the session date (not a timestamp).

```
wiki/discussions/2026-03-14-retry-strategy.md
wiki/discussions/2026-04-01-monorepo-layout.md
```

## Wikilink syntax

Internal cross-references use double-bracket wikilink syntax: `[[slug]]`. The slug is the file's base name without the `.md` extension.

```markdown
See [[session-end-hook]] for how the transcript is extracted.
The full format is defined in [[page-format]].
```

**Slugs are globally unique across the entire wiki**, including under nested category structures (monorepos). If two packages would produce the same natural slug, prefix with the package name:

```markdown
[[apposum-runtime]]   # not [[runtime]] — would collide with beejector-runtime
[[beejector-runtime]]
```

This keeps wikilink resolution simple: a slug always identifies exactly one page, with no need for path-qualified lookups.

## Category taxonomy

Two kinds of categories exist:

### Fixed categories

Present in every LoreLake install. Declared in `config.json` under `llake.fixedCategories`.

| Category | Purpose |
|---|---|
| `discussions/` | Capture-agent output: session transcripts, Key Facts, topic continuations |
| `decisions/` | Architecture decision records (ADRs) |
| `gotchas/` | Pitfalls, non-obvious constraints, things that burned the team |
| `playbook/` | Operational procedures, runbooks, how-tos |

### Project-specific categories

Vary per project, reflecting the project's conceptual model. Discovered at runtime by listing `llake/wiki/*/`. Created on demand by ingest or bootstrap when no existing category is a reasonable home for new content.

Examples from a backend API project: `api/`, `data-model/`, `auth/`, `infrastructure/`, `architecture/`.

### Category creation protocol

Only bootstrap and ingest may create new categories. Rules:

1. **Prefer existing.** Only create a new category when no existing category (fixed or project-specific) is a reasonable home for the page.
2. **Align naming.** Read all existing category one-liners before naming a new one. Do not create `monitoring/` if `observability/` already exists.
3. **Name broadly.** Lowercase, singular noun. Choose a name broad enough that future related pages can live there too.
4. **Create the category index.** `wiki/<category>/<category>.md` must be created as part of the same operation.
5. **Update the root index.** `llake/index.md` must be updated to add the new category with a one-line description.

## Index architecture

LoreLake uses a hierarchical index to keep context injection efficient.

### Root index (`llake/index.md`)

Lists all categories with one-line descriptions. Kept small (~20 lines). Injected at every session start so agents can decide which category to drill into without loading the full wiki.

```markdown
# LoreLake

## Categories

- **architecture/** — System architecture, component interactions, design decisions
- **schema/** — Page format rules and content standards
- **hooks/** — Hook implementations and their behaviors
```

### Category index (`wiki/<category>/<category>.md`)

Lists every page in that category with a one-line summary (under 100 characters each). Loaded only when an agent needs to work in that category.

```markdown
# Schema

- [[schema-overview]] — The schema/ directory and how it governs all wiki writers
- [[page-format]] — Complete page format spec: frontmatter, required sections, naming
- [[content-standards]] — Standards 1, 2, 3 — new-employee test, conversation fidelity, no sensitive data
```

### Monorepo: three-level index

In a monorepo, the index extends one level deeper. See the monorepo layout section below for details. The pattern:

- `llake/index.md` — lists top-level categories including grouping dirs (`packages/`, `apps/`, etc.)
- `wiki/packages/packages.md` — group index, lists each package as a sub-category
- `wiki/packages/apposum/apposum.md` — sub-category index, lists every page within that package
- Pages live at `wiki/packages/apposum/<page>.md`

## Monorepo layout

A project is treated as a monorepo when two or more project marker files exist at non-root paths. Marker files include: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `composer.json`, `Gemfile`, `*.csproj`, and equivalents across all major ecosystems.

Once detected as a monorepo, the wiki adopts nested layout **permanently**, regardless of how many packages currently exist. Restructuring later (renaming pages, rewriting wikilinks, re-indexing) is expensive — commit to the structure on first detection.

```
wiki/
  packages/
    packages.md                 # group index
    apposum/
      apposum.md                # sub-category index
      http-client.md
      retry-logic.md
    beejector/
      beejector.md
      pipeline.md
  apps/
    apps.md
    dashboard/
      dashboard.md
      routing.md
  architecture/                 # cross-cutting: top-level
  decisions/                    # fixed categories: always top-level
  gotchas/
  playbook/
  discussions/
```

Project-wide concerns (architecture, decisions, gotchas, playbook, discussions) stay at the top level. Only package/app/service-specific content lives under the nested groups.

## `log.md` format

The activity log at `llake/log.md` is append-only. Format:

```markdown
# LoreLake Activity Log

## [YYYY-MM-DD] <operation> | <brief description>

<2–3 sentence summary of what happened>

Pages affected: [[page-1]], [[page-2]]
```

Valid operation values: `bootstrap`, `bootstrap-task`, `bootstrap-consistency`, `ingest`, `session-capture`, `lint`, `manual-update`.

## Key Points

- Every page requires YAML frontmatter with `title`, `description`, `tags`, `created`, `updated`, `status`, and optionally `related` and `commits`.
- Five sections are required on every page: Overview, main content, Key Points, Code References, See Also.
- Wikilink slugs are globally unique across the entire wiki — prefix with package name in monorepos to avoid collisions.
- Decision records are named `adr-NNN-short-title.md`; discussion entries are named `YYYY-MM-DD-topic.md`.
- Category creation requires: prefer existing, align naming, name broadly, create the category index, update the root index.
- Monorepo layout is permanent once adopted — do not restructure retroactively.
- The root `index.md` is injected at every session start and must stay small (~20 lines).

## Code References

- `schema/core.md` — page format, frontmatter spec, category taxonomy, naming conventions, index architecture, monorepo layout, log.md format
- `schema/index.md` — file-to-audience loading guide
- `llake/index.md` — root index (runtime instance in the project)
- `llake/wiki/schema/schema.md` — category index for the schema category

## See Also

- [[schema-overview]] — role of the schema/ directory and its files
- [[content-standards]] — what goes in the main content section (Standards 1, 2, 3)
- [[runtime-layout]] — the full llake/ directory structure at runtime
- [[three-writer-model]] — which writers follow these format rules and when
