---
title: "LoreLake Schema — Index"
description: "Where each rule lives in the schema/ directory"
---

# LoreLake Schema

The schema is split by consumer so each writer loads only the rules it needs at runtime. Some rules (notably Standard 3 — Security) are duplicated across files because they apply to multiple writers; the alternative — cross-references — would force the writer to chase links during execution and add context-rot risk.

| File | Audience | Contents |
|---|---|---|
| [core.md](core.md) | every writer (bootstrap, ingest, capture) at runtime | Page format, write surface, category taxonomy, monorepo & nested-category layout rule, category creation protocol, naming conventions, hierarchical index architecture, `log.md` format |
| [code-content-standard.md](code-content-standard.md) | bootstrap + ingest at runtime | Standard 1 (the new-employee test); Standard 3 (security, duplicated) |
| [conversation-content-standard.md](conversation-content-standard.md) | capture at runtime | Standard 2 (immutable Key Facts); discussion entry & continuation formats; Standard 3 (security, duplicated) |
| [operations.md](operations.md) | plugin maintainers — **not loaded at runtime** | Bootstrap, ingest, capture, and lint workflow descriptions |

## Loading guide

| Writer | Files to load |
|---|---|
| Bootstrap (skill) | `core.md` + `code-content-standard.md` |
| Ingest (post-merge agent) | `core.md` + `code-content-standard.md` |
| Capture (session-end agent) | `core.md` + `conversation-content-standard.md` |
| Triage (session-end agent) | none — triage classifies, never writes |

## Adding a new rule

1. Identify the audience: who needs this rule at runtime?
2. Put it in the file(s) that audience already loads. If two audiences both need it, **duplicate** — do not cross-reference. Maintenance cost of one duplicated rule is lower than runtime cost of every writer chasing links.
3. Update this index if you create a new file or significantly change a file's scope.
