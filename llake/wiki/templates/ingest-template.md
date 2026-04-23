---
title: "Ingest Prompt Template"
description: "The post-merge ingest agent prompt that updates wiki pages from git diffs"
tags: [templates, ingest, post-merge]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[post-merge-hook]]"
  - "[[template-system]]"
  - "[[config-schema]]"
---

## Overview

The ingest template (`hooks/prompts/ingest.md.tmpl`) drives the ingest agent spawned by the `post-merge` git hook. When new commits land on the configured branch, the hook renders this template and spawns a background Claude CLI agent to understand what changed and update the wiki accordingly.

The ingest agent's scope is the entire wiki except `wiki/discussions/`. It reads git history and diffs for a commit range, identifies what those changes mean semantically, then creates or updates the relevant wiki pages to keep the knowledge base aligned with the current code state.

## Placeholders

The ingest template uses nine `{{VAR}}` placeholders plus one `{{VAR|fallback:path}}` slot:

| Placeholder | Type | Filled by | Value |
|---|---|---|---|
| `{{AGENT_ID}}` | CLI arg | `post-merge.sh` | Readable agent ID, e.g. `brave-hawk-091230` |
| `{{PROJECT_ROOT}}` | CLI arg | `post-merge.sh` | Absolute path to the project root |
| `{{LAST_SHA}}` | CLI arg | `post-merge.sh` | Previous HEAD SHA (from `llake/last-ingest-sha`) |
| `{{CURRENT_SHA}}` | CLI arg | `post-merge.sh` | Current HEAD SHA after the merge |
| `{{COMMIT_RANGE}}` | CLI arg | `post-merge.sh` | Short display form, e.g. `a1b2c3d..e4f5g6h` |
| `{{PATHSPEC_INCLUDE}}` | CLI arg | `post-merge.sh` | `-- 'src/' 'scripts/'` style pathspec built from `ingest.include` config |
| `{{LLAKE_ROOT}}` | CLI arg | `post-merge.sh` | Absolute path to `<project>/llake/` |
| `{{WIKI_ROOT}}` | CLI arg | `post-merge.sh` | Absolute path to `<project>/llake/wiki/` |
| `{{SCHEMA_DIR}}` | CLI arg | `post-merge.sh` | Absolute path to the plugin's `schema/` directory |
| `{{EXAMPLES}}` | fallback slot | config or file | Project-specific examples, or falls back to `generic-examples.md` |

The `{{EXAMPLES}}` slot is the only fallback placeholder in the template. Its resolution is described in detail below.

## The EXAMPLES Slot

```
{{EXAMPLES|fallback:generic-examples.md}}
```

This is the most important customization point in the ingest template. The slot works as follows:

1. **Config override first**: `render-prompt.py` checks `config.prompts.ingest.EXAMPLES` in the project's `config.json`. If set, that value (a string) is injected directly. This lets projects supply hand-curated examples tailored to their own commit conventions and code patterns.

2. **File fallback**: if no config override is present, the renderer reads `generic-examples.md` from the `--templates-dir` (the plugin's `templates/` directory). The generic examples cover broadly applicable cases: a gotcha-worthy bug fix, a trivial fix that is not a gotcha, a multi-category feature commit, implementing an existing decision, and a refactor with no new pages.

**Why examples are a config slot rather than hard-coded:** Ingest fidelity depends heavily on the quality of examples. Projects with unusual commit conventions, domain-specific code patterns, or heavy use of monorepo structures benefit enormously from examples that match their actual diff shapes. A project with a clean ADR history needs different examples than a project starting from scratch. Making this a config slot lets each project tune the agent's classification instincts without modifying the plugin.

## Write Surface

The ingest template is explicit about what the agent may and may not write:

**Allowed:**
- `{{WIKI_ROOT}}/**` — any category except `wiki/discussions/`
- `{{LLAKE_ROOT}}/index.md` — only when adding a new category
- `{{LLAKE_ROOT}}/log.md` — append-only

**Forbidden:**
- `{{WIKI_ROOT}}/discussions/**` — owned by the session-capture agent
- `{{SCHEMA_DIR}}/**` — schema files are immutable to agents
- `{{LLAKE_ROOT}}/config.json`
- `{{LLAKE_ROOT}}/last-ingest-sha` — managed by the shell hook, not the agent
- `{{LLAKE_ROOT}}/.state/**`
- Any file outside `{{LLAKE_ROOT}}/`

The `allowedTools` restriction in `post-merge.sh` (from `ingest.allowedTools` in config) provides shell-level enforcement on top of the prompt-level write surface rules.

## Six-Phase Workflow

**Phase 1 — Read the schema and current wiki.** Load `schema/core.md`, `schema/code-content-standard.md` (Standard 1 — the new-employee test; Standard 3 — no sensitive data), and `llake/index.md`. The agent is required to load these before writing anything.

**Phase 2 — Understand the commits.** Run `git log --stat {{LAST_SHA}}..{{CURRENT_SHA}} {{PATHSPEC_INCLUDE}}` and `git diff {{LAST_SHA}}..{{CURRENT_SHA}} {{PATHSPEC_INCLUDE}}`. The agent reads both the commit messages (intent) and the diffs (truth). It derives the **semantic value** of each change: what does this do to the system's behavior, structure, or invariants? What does a future reader need to know?

The template lists classification labels (bug fix, feature, refactor, etc.) as shorthand only — they are not the goal. The goal is determining what knowledge the change produces and where it belongs.

**Phase 3 — Locate affected knowledge.** Read category index files to find the right home(s). Check `wiki/decisions/decisions.md` for prior ADRs that these commits may implement. Apply the gotcha bar (non-obvious, repeatable, required debugging or reveals domain knowledge — not syntax errors or typos). Consider whether new ADRs are warranted.

**Phase 4 — Read existing pages before writing.** For every page planned for update, read the current content first.

**Phase 5 — Write pages and maintain cross-links.** Update content to reflect the new code state per Standard 1. Update `updated:` frontmatter. Link pages to relevant decisions via `[[wikilinks]]` and the `related:` field. Linking is bidirectional.

**Phase 6 — Update indexes and log.** Update category index files, update `llake/index.md` only if a new category was created, and append to `llake/log.md`.

## Content Standards

**Standard 1 — Code-content completeness (new-employee test):** every page must be sufficient to onboard a contributor without reading the source. Brevity is not a virtue if it forces the reader back to code. Loaded from `schema/code-content-standard.md`.

**Standard 3 — Security:** no credentials, connection strings, internal hostnames/IPs, or personal data. Abstract rather than capture. Overrides all other content guidance.

## Template Excerpt

```markdown
## Goal

Keep LoreLake semantically aligned with the code state after {{COMMIT_RANGE}}. Understand what changed
and why, then update the wiki to reflect the new reality. The wiki must not contain redundant or
contradictory pages. Prefer updating existing pages over creating new ones.

### Phase 2 — Understand the commits
1. Run: `git log --stat {{LAST_SHA}}..{{CURRENT_SHA}} {{PATHSPEC_INCLUDE}}`
2. Run: `git diff {{LAST_SHA}}..{{CURRENT_SHA}} {{PATHSPEC_INCLUDE}}`

## Examples

{{EXAMPLES|fallback:generic-examples.md}}
```

## Key Points

- The ingest agent never touches `wiki/discussions/` — that is exclusively the capture agent's territory.
- `last-ingest-sha` is managed by the shell hook, not the agent: the hook updates it after a successful run. The agent is not permitted to write this file.
- `{{PATHSPEC_INCLUDE}}` is built from `ingest.include` in config — it scopes the git commands to the paths the project owner has declared as wiki-relevant. An empty include means all files.
- The EXAMPLES slot is the primary tuning mechanism for ingest quality. A project that has invested in writing good examples will see significantly better wiki output.
- The agent is instructed to prefer updating existing pages over creating new ones. LoreLake is not a changelog — trivial changes should not produce new pages.
- Cross-linking is bidirectional: if a commit implements an ADR, both the implementation page and the ADR page should reference each other.

## Code References

- `hooks/prompts/ingest.md.tmpl` — the full template
- `hooks/prompts/ingest.md.tmpl:111` — `{{EXAMPLES|fallback:generic-examples.md}}` slot
- `templates/generic-examples.md` — the default examples loaded when no config override is present
- `hooks/post-merge.sh:209-221` — ingest prompt rendering (all 9 KEY=value args)
- `hooks/post-merge.sh:242-246` — ingest agent spawn with `--allowedTools` and `--max-budget-usd`
- `hooks/post-merge.sh:97-98` — `ALLOWED_TOOLS_JSON` and `INCLUDE_JSON` read from config

## See Also

- [[post-merge-hook]] — the shell hook that renders this template and spawns the agent
- [[template-system]] — how `{{VAR}}` and `{{VAR|fallback:path}}` are resolved
- [[config-schema]] — `ingest.*` config keys: model, effort, budget, branch, include, allowedTools
- [[add-new-prompt-placeholder]] — playbook for adding a new `{{VAR}}` to this template
