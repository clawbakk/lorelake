---
title: "Plugin vs Project Duality"
description: "The plugin repo vs a target project's llake/ install — what lives where"
tags: [architecture, plugin, project, install, duality]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[three-writer-model]]"
  - "[[runtime-layout]]"
  - "[[llake-lady-skill]]"
  - "[[llake-doctor-skill]]"
  - "[[config-schema]]"
  - "[[schema-overview]]"
---

## Overview

LoreLake has two completely separate concepts that are easy to conflate: the **plugin** (this repository, `clawbakk/lorelake`) and a **project install** (a separate user repository where the plugin has been installed). These two things look similar — both involve markdown files and a directory called `llake/` — but they serve entirely different roles and have different ownership rules.

Confusing them is the single most common source of incorrect edits. This page exists to make the distinction unambiguous.

---

## The Plugin Root (this repository)

The plugin root is the `clawbakk/lorelake` repository itself. It contains:

```
hooks/
  session-start.sh       # Claude Code SessionStart hook
  session-end.sh         # Claude Code SessionEnd hook
  post-merge.sh          # git post-merge hook
  hooks.json             # Claude Code hook declarations
  lib/                   # shared bash + python helpers
  prompts/               # agent prompt templates (*.md.tmpl)
templates/
  session-preamble.md    # injected at session start
  config.default.json    # default values for all config keys
schema/
  index.md               # schema loading guide
  *.md                   # spec files (page format, write rules, content standards)
skills/
  llake-bootstrap/SKILL.md
  llake-lady/SKILL.md
  llake-doctor/SKILL.md
tests/
  ...
```

**The plugin root is immutable to agents.** No wiki-writing agent (bootstrap, ingest, capture) may write to any file under `hooks/`, `templates/`, `schema/`, or `skills/`. This rule is enforced at two levels:

1. The prompt templates for each agent explicitly forbid writing outside `<project>/llake/`.
2. The `--allowedTools` flag passed to each `claude -p` invocation restricts the file-system operations available.

The plugin is installed as a Claude Code plugin per-user (via `/plugin install lorelake@clawbakk`). Once installed, every Claude Code project on the machine can use it — the plugin code lives in one place and is referenced by path at runtime (e.g., `${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh`). There are no per-project copies of hook scripts or prompt templates.

### Why no per-project copies?

Updates to the plugin (new prompt templates, fixed hooks, improved defaults) must reach all installs instantly without requiring each project to manually pull changes. This is only possible if every project references the plugin's files by absolute path rather than keeping local copies. The install plan (`plan.md.tmpl`) embeds `PLUGIN_ROOT` into every generated shim for exactly this reason.

### What the plugin does NOT contain

The plugin repository contains **no wiki content**. There is no `llake/wiki/` directory here. The `llake/` directory that appears in this repository (e.g., `llake/config.json`, `llake/wiki/`) exists only because this repository also serves as a development test project — a project that has had LoreLake installed into it. That content is the test project's data, not part of the plugin itself.

---

## A Project Install

A "project" is any separate repository where LoreLake has been installed. Installation is performed by `/llake-lady` (the install wizard skill), which creates the following structure inside the project:

```
<project>/llake/
  config.json            # project-specific configuration
  index.md               # wiki category catalog
  log.md                 # append-only activity log
  last-ingest-sha        # SHA cursor for ingest
  wiki/
    decisions/
    gotchas/
    discussions/
    playbook/
    <custom-categories>/
  .state/                # gitignored runtime working directory
    hooks.log
    agents/<id>/
    sessions/<id>/
```

This directory is **data-only**. It holds configuration the user has chosen, wiki content that agents have written, and runtime state. It does not contain any plugin code (no hook scripts, no prompt templates, no schema files).

The project's `llake/` directory is owned by the user and modified by the agents the plugin spawns. The user is expected to commit `config.json`, `index.md`, `log.md`, `last-ingest-sha`, and `wiki/` to version control. The `.state/` directory is gitignored.

---

## How the Plugin Hooks Are Wired Per-Project

LoreLake uses two distinct hook mechanisms:

### Claude Code hooks (SessionStart, SessionEnd)

These are declared in the plugin's `hooks/hooks.json` and registered once when the plugin is installed via `/plugin install`. They fire for every Claude Code session in every project on the machine. The hooks themselves detect whether the current working directory belongs to a LoreLake-enabled project by walking up the directory tree looking for `llake/config.json`. If no config is found, the hook exits silently — so projects that do not have LoreLake installed are not affected.

From `hooks/session-start.sh` (line 31):
```bash
PROJECT_ROOT=$(detect_project_root "${CWD:-$PWD}" 2>/dev/null) || exit 0
```

The `detect_project_root` function (in `hooks/lib/detect-project-root.sh`) performs this walk. An env override (`LLAKE_PROJECT_ROOT`) can force a specific project root, which is used in tests.

### The git post-merge hook

The git hook is project-local. `/llake-lady` installs a shim at `.git/hooks/post-merge` inside each project that delegates to the plugin's `post-merge.sh`:

```bash
#!/bin/bash
exec "${PLUGIN_ROOT}/hooks/post-merge.sh" "$@"
```

The shim contains the absolute path to the plugin. Because `post-merge.sh` checks for `llake/config.json` before doing anything (`[ -f "$CONFIG_FILE" ] || exit 0`), the hook is already safe if run in a project without LoreLake, but the shim itself is intentionally project-local — it is only installed in projects where the user has run `/llake-lady`.

Git worktrees share the main repo's `.git/hooks/` directory, so one install covers all worktrees automatically.

---

## What Can and Cannot Be Modified Where

| Location | Who modifies it | Agents may write? |
|---|---|---|
| `hooks/`, `templates/`, `schema/`, `skills/` (plugin root) | Plugin developers only | Never |
| `<project>/llake/config.json` | User + `/llake-doctor` | Read-only for wiki agents; write by install/repair skills |
| `<project>/llake/index.md` | Bootstrap + ingest + `/llake-doctor` | Yes (bootstrap, ingest) |
| `<project>/llake/log.md` | All hooks (append-only) | Append only |
| `<project>/llake/last-ingest-sha` | `post-merge.sh` (shell script, before agent spawns) | No — written by hook script, not agent |
| `<project>/llake/wiki/**` | Bootstrap + ingest + capture | Yes, within write-surface rules |
| `<project>/llake/.state/**` | Hook scripts + agents | Yes (runtime state) |

A critical subtlety: `last-ingest-sha` is written by the `post-merge.sh` shell script **before** the agent is spawned, not by the agent itself. The hook script advances the cursor (or resets it on force-push) as part of pre-flight. The agent never touches it.

---

## Common Confusion Points

**"This repo has a `llake/` directory — so it IS a LoreLake install."**
Yes and no. The plugin repo is also used as a development test project. The `llake/` directory you see here is the test project's data. The plugin code itself is in `hooks/`, `templates/`, `schema/`, and `skills/`.

**"I need to edit the prompt templates for my project."**
Prompt templates live in `hooks/prompts/` in the plugin repo. Per-project prompt customization is done via `config.prompts.<template-name>.<KEY>` in `llake/config.json`, which the prompt renderer checks before applying defaults. You should never copy prompt templates into your project.

**"I cloned a fresh project — why does nothing in `llake/` exist yet?"**
The `llake/` directory is created by `/llake-lady` (the install wizard). A fresh clone of a project that uses LoreLake will have `llake/` committed to the repo (config, index, log, wiki pages), but the `.state/` directory is gitignored and will be empty. Run `/llake-doctor` after a fresh clone to verify the install is healthy.

**"I updated the plugin — do I need to re-run `/llake-lady` in every project?"**
No. Hook scripts and prompt templates are referenced by absolute path from the plugin installation. Updates to the plugin take effect immediately in all projects. The only exception is if the install plan itself changes in a way that requires re-wiring git hooks or regenerating `config.json` — in that case `/llake-doctor` will detect and repair the drift.

---

## Key Points

- The plugin repo (`clawbakk/lorelake`) holds code only: hooks, templates, schema, skills. It is immutable to wiki-writing agents.
- A project install gets its own `llake/` directory (data only: config, wiki, runtime state). This directory is owned by the user's project.
- The Claude Code hooks (SessionStart, SessionEnd) are global — they fire for every session and use project-root detection to find the right `llake/` directory.
- The git `post-merge` hook is project-local — a shim installed by `/llake-lady` in each project's `.git/hooks/`.
- There are no per-project copies of hook scripts or prompt templates. Plugin updates reach all installs automatically.
- `last-ingest-sha` is written by the hook shell script, not by the ingest agent.

---

## Code References

- `hooks/hooks.json` — Claude Code hook declarations; references `${CLAUDE_PLUGIN_ROOT}`
- `hooks/session-start.sh:31` — project-root detection via marker walk
- `hooks/post-merge.sh:41-57` — project-root detection (git + env override) and config-existence check
- `hooks/lib/detect-project-root.sh` — shared marker-walk library
- `templates/config.default.json` — canonical source of default config values
- `schema/index.md` — schema loading guide (spec, not plugin code)

---

## See Also

- [[three-writer-model]] — how bootstrap, ingest, and capture use the two-layer separation
- [[runtime-layout]] — full directory structure of a project install
- [[llake-lady-skill]] — the install wizard that creates the project-side structure
- [[llake-doctor-skill]] — drift detection and repair for project installs
- [[config-schema]] — all configurable keys and their defaults
- [[schema-overview]] — the schema directory and how agents consume it
