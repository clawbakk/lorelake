---
title: "Schema Overview"
description: "The schema/ directory and how it governs all wiki writers — immutable spec, not code"
tags: [schema, architecture, spec]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[three-writer-model]]"
  - "[[page-format]]"
  - "[[content-standards]]"
---

# Schema Overview

## Overview

The `schema/` directory is the authoritative specification for everything a LoreLake writer is allowed to do: how pages must be formatted, what content must be present, how categories are named and structured, and what data must never appear in the wiki. It is the spec; the hook scripts and prompt templates in `hooks/` and `skills/` implement it.

The schema is **immutable to agents**. No writer — bootstrap, ingest, or capture — may write to any file under `schema/`. This is enforced at two levels: the shell-level `allowedTools` restrictions in each hook, and explicit path rules in every prompt template and skill spec. If you believe a schema rule is wrong, change it yourself as a maintainer — then update the prompt templates that load it.

## Role in the system

LoreLake has three writers, each triggered by a different event. See [[three-writer-model]] for the full picture. All three writers share a common foundation: before doing anything else, each loads the relevant schema files and treats those rules as non-negotiable constraints on what it writes.

The schema is **not documentation about the wiki**. It is the operational rulebook that runs inside each agent's context window. Every sentence in a schema file is there because an agent needs to read it at runtime. That is why the schema is kept minimal and split by consumer: each writer loads only what it needs.

### Code as truth

The schema files define intent. The hook scripts and prompt templates define behavior. When the two disagree — for example, the schema says a placeholder is named `{{SCHEMA_DIR}}` but the hook passes a different name — **the code is truth**. The schema file should be flagged as stale and updated to match. Never assume a schema file accurately reflects the current behavior of the hooks; verify against the source when in doubt.

## Schema files

| File | Audience | Loaded at runtime? | Contents |
|---|---|---|---|
| `schema/index.md` | Maintainers | No | Loading guide — which writer loads which files. The entry point for navigating the schema. |
| `schema/core.md` | Every writer (bootstrap, ingest, capture) | Yes | Page format and frontmatter spec; write-surface rules; category taxonomy (fixed vs project-specific); monorepo detection and nested wiki layout; category creation protocol; naming conventions; hierarchical index architecture; `log.md` format. |
| `schema/code-content-standard.md` | Bootstrap and ingest | Yes | Standard 1 (new-employee test); Standard 3 (security, duplicated here because bootstrap and ingest need it). |
| `schema/conversation-content-standard.md` | Capture | Yes | Standard 2 (immutable Key Facts block); discussion entry format; continuation format; Standard 3 (security, duplicated here because capture needs it). |
| `schema/operations.md` | Plugin maintainers | No | High-level workflow descriptions for bootstrap, ingest, capture, and lint. Written for humans reading the system; not loaded by any agent. |

### Why Standard 3 is duplicated

Standard 3 (no sensitive data) applies to every writer. Rather than having writers cross-reference another file mid-execution, the rule is duplicated into both `code-content-standard.md` and `conversation-content-standard.md`. The maintenance cost of one duplicated rule is lower than the runtime cost — and context-rot risk — of chasing cross-references inside an agent's context window.

## Loading guide

Each writer loads exactly the files it needs:

| Writer | Files loaded |
|---|---|
| Bootstrap (skill) | `core.md` + `code-content-standard.md` |
| Ingest (post-merge agent) | `core.md` + `code-content-standard.md` |
| Capture (session-end agent) | `core.md` + `conversation-content-standard.md` |
| Triage (session-end, first pass) | None — triage classifies sessions, never writes |

The files reach the agents via the `{{SCHEMA_DIR}}` placeholder substituted into each prompt template by `hooks/lib/render-prompt.py`. The absolute path to the plugin's `schema/` directory is injected at hook invocation time.

## Adding or changing a rule

1. Identify which writers need the new rule at runtime.
2. Edit the schema file(s) those writers already load. If two audiences both need the rule, duplicate it — do not cross-reference.
3. Update `schema/index.md` if you are creating a new file or significantly changing a file's scope.
4. Update the prompt templates that render the schema into agent context, if the placeholder path has changed.
5. Run the full test suite (`python3 -m pytest tests/lib/ -q`) to confirm nothing downstream broke.

## Key Points

- The `schema/` directory is the spec; agents implement it but never write to it.
- Schema is split by consumer so each agent loads only what it needs, keeping context windows small.
- When schema and code disagree, the code is truth — flag the schema file as stale.
- Standard 3 (security) is intentionally duplicated across content-standard files rather than cross-referenced.
- `schema/operations.md` is human-only reference material and is never loaded at runtime.
- The `{{SCHEMA_DIR}}` placeholder is how prompt templates reference schema files at runtime.

## Code References

- `schema/index.md` — loading guide, file-to-audience mapping
- `schema/core.md` — universal rules (page format, write surface, taxonomy, layout)
- `schema/code-content-standard.md` — Standards 1 and 3 for bootstrap/ingest
- `schema/conversation-content-standard.md` — Standards 2 and 3 for capture
- `schema/operations.md` — maintainer-facing workflow descriptions (not runtime)
- `hooks/lib/render-prompt.py` — substitutes `{{SCHEMA_DIR}}` and other placeholders into prompt templates

## See Also

- [[three-writer-model]] — the bootstrap, ingest, and capture writers that consume this schema
- [[page-format]] — detailed specification of page structure derived from `schema/core.md`
- [[content-standards]] — detailed treatment of Standards 1, 2, and 3
- [[runtime-layout]] — where schema files live relative to the project llake/ directory
