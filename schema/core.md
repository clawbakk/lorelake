---
title: "LoreLake Schema — Core"
description: "Universal rules every writer needs: page format, write surface, taxonomy, layout, naming, indexing"
---

# LoreLake Schema — Core

Universal rules for every writer (bootstrap, ingest, capture).

## Purpose

LoreLake is a persistent, structured wiki that serves as the project's long-term knowledge base. It complements `CLAUDE.md` (operational instructions) by offering deep, queryable knowledge about how the project works.

- **CLAUDE.md** → "How to work in this project" (always loaded, short)
- **LoreLake** → "How this project works" (loaded on-demand, comprehensive)

## Directory structure

```
llake/
├── index.md                    # Root catalog (categories → category indexes)
├── log.md                      # Activity log (append-only)
├── config.json                 # Configuration (tracked)
├── last-ingest-sha             # Ingest cursor (tracked)
├── .state/                     # Runtime working dir (gitignored)
│   ├── agents/                 # Per-agent log files
│   ├── sessions/               # Session lock + transcript extraction
│   └── hooks.log               # Rolled hook audit log
└── wiki/
    ├── discussions/
    │   └── discussions.md      # Category index
    ├── decisions/
    │   └── decisions.md
    ├── gotchas/
    │   └── gotchas.md
    ├── playbook/
    │   └── playbook.md
    └── <project-categories>/
        └── ...
```

The plugin's schema lives in `<plugin>/schema/` (this directory); agents read its files via the absolute `{{SCHEMA_DIR}}` substitution in their prompts.

## Page format

Every wiki page requires YAML frontmatter:

```yaml
---
title: "Page Title"
description: "One-line summary for category index"
tags: [category, topic, subtopic]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: current  # current | draft | stale | deprecated | immutable
related:
  - "[[page-name]]"
---
```

Required sections: a brief overview, main content, "Key Points" summary, "Code References" with `file:line` links, and "See Also" with wikilinks.

## Category taxonomy

Two kinds of categories exist:

- **Fixed categories** — universal across every project: `discussions`, `decisions`, `gotchas`, `playbook`. Declared in `config.json` under `llake.fixedCategories`.
- **Project-specific categories** — vary per project, reflecting the project's conceptual model. Discovered at runtime by listing `llake/wiki/*/`. Created on demand by the ingest or bootstrap agents when no existing category is a reasonable home for new content.

## Wiki layout: monorepo & nested categories

The wiki layout mirrors the project's structural shape. Single-project repos use flat top-level categories under `wiki/`. Monorepos use **nested** categories that mirror the workspace layout. This is a single canonical rule — both bootstrap and ingest follow it, and there is no config knob.

### Monorepo detection (tech-stack-agnostic)

A project is treated as a monorepo when **two or more project marker files exist at non-root paths.** A "project marker file" is any file that identifies a unit of code in any ecosystem:

`package.json`, `pyproject.toml`, `setup.py`, `setup.cfg`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `build.gradle.kts`, `composer.json`, `Gemfile`, `*.csproj`, `*.fsproj`, `mix.exs`, `Project.toml`, `pubspec.yaml`, `Package.swift`, `BUILD`, `BUILD.bazel`, `dune-project`, `stack.yaml`, `cabal.project`, etc.

The detection signal is the *existence of multiple project units*, not the names of any specific tool. Common grouping directories (`packages/`, `apps/`, `services/`, `crates/`, `modules/`, `libs/`, `subprojects/`, `cmd/`) are hints — the authoritative signal is the marker files themselves.

### One-time, count-independent

Once a project is detected as a monorepo, the wiki adopts the nested layout **regardless of how many packages or apps currently exist.** A monorepo is a monorepo from day one; the user will add packages over time, and restructuring the wiki retroactively (renaming pages, rewriting `[[wikilinks]]`, re-indexing) is expensive. Commit to the structure on first detection.

### Wiki layout under a monorepo

For each top-level grouping directory the project uses (`packages/`, `apps/`, `services/`, etc.), create a corresponding wiki sub-tree:

```
wiki/
  packages/
    packages.md            # group index — lists each package
    apposum/
      apposum.md           # sub-category index for apposum
      <pages>.md
    beejector/
      beejector.md
      <pages>.md
  apps/
    apps.md
    <app-name>/
      <app-name>.md
      <pages>.md
  architecture/            # cross-cutting: how packages fit together (top-level)
    ...
  decisions/               # fixed cross-cutting category (top-level)
    ...
  gotchas/                 # fixed (top-level)
    ...
  playbook/                # fixed (top-level)
    ...
  discussions/             # fixed (top-level)
    ...
```

Project-wide concerns — architecture spanning multiple packages, terminology, decisions, gotchas, playbook, discussions — stay at the top level. Only package/app/service-specific content lives under the nested groups.

### Indexing rules under nesting

The hierarchical index pattern extends one level:

- `<project>/llake/index.md` — lists top-level categories, including grouping dirs (e.g., `packages/`, `apps/`).
- `wiki/packages/packages.md` — group index, lists each package as a sub-category with a one-line description.
- `wiki/packages/apposum/apposum.md` — sub-category index, lists every page within that package.
- Pages live at `wiki/packages/apposum/<page>.md`.

### Slug uniqueness across nested categories

`[[wikilink]]` slugs remain **globally unique across the entire wiki**, even under nested categories. This keeps wikilink resolution simple — a slug always identifies exactly one page. When two packages would naturally produce the same page slug (e.g., both have a `runtime.md`), prefix with the package name: `[[apposum-runtime]]`, `[[beejector-runtime]]`. Bootstrap and ingest enforce this when planning page slots.

## Write surface

| Agent | May write | Must NOT write |
|---|---|---|
| `bootstrap` | `wiki/**` (any category, including new); `index.md`; `log.md` (append) | `discussions/**` (capture's domain); the `schema/` files; `config.json`; `last-ingest-sha`; `.state/**`; anything outside `llake/` |
| `capture` | `wiki/discussions/**`, `wiki/decisions/**`, `wiki/gotchas/**`, `wiki/playbook/**` (the fixed categories); category-index updates for the touched categories; `log.md` (append) | anywhere else |
| `ingest` | `wiki/**` except `wiki/discussions/**`; `index.md` on new-category creation; `log.md` (append); category-index updates | `discussions/**`; the `schema/` files; `config.json`; `last-ingest-sha`; `.state/**`; anything outside `llake/` |

Tool-shape restrictions are enforced at the shell level via `allowedTools`; path restrictions are enforced via the prompts.

## Category creation protocol

Only the ingest and bootstrap agents may create new categories. Rules:

1. **Prefer existing.** Only create a new category when no existing one (fixed or project-specific) is a reasonable home for a page.
2. **Align naming.** Read all existing category one-liners before naming a new one. Do not create `monitoring/` if `observability/` already exists.
3. **Name broadly.** Lowercase, singular noun. Prefer names broad enough that future related pages can live there too.
4. **Create the category index.** `wiki/<cat>/<cat>.md` must be created.
5. **Update the root index.** `llake/index.md` must be updated to add the new category.

## Naming conventions

- **File names:** lowercase, hyphen-separated. e.g., `tick-loop.md`.
- **Directories:** lowercase, singular noun. e.g., `architecture/`, `strategy/`.
- **Tags:** lowercase, hyphen-separated. Category tag first, then specifics.
- **Decision records:** `adr-NNN-short-title.md` (NNN = zero-padded sequence).
- **Discussion entries:** `YYYY-MM-DD-topic.md`.

## Index architecture (hierarchical)

LoreLake uses a two-level index for efficient context usage. Under monorepos, this extends to three levels (see "Indexing rules under nesting" above).

### Root `index.md`

Lists categories with one-line descriptions. Small (~20 lines). Cheap to inject at session start. Lets agents decide which category to drill into.

### Category index (`wiki/<cat>/<cat>.md`)

Lists all pages in that category with one-line summaries (under 100 chars each). Loaded only when needed.

## `log.md` format

```markdown
# LoreLake Activity Log

## [YYYY-MM-DD] <operation> | <brief description>

<2-3 sentence summary of what happened>

Pages affected: [[page-1]], [[page-2]]
```

Append-only. Operations include: `bootstrap`, `bootstrap-task`, `bootstrap-consistency`, `ingest`, `session-capture`, `lint`, `manual-update`.
