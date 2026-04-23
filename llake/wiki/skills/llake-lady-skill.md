---
title: "/llake-lady — Install Wizard"
description: "Sets up llake/ in a project, wires the post-merge hook, and runs doctor to verify"
tags: [skills, install, setup]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[llake-doctor-skill]]"
  - "[[llake-bootstrap-skill]]"
  - "[[plugin-project-duality]]"
  - "[[runtime-layout]]"
---

# /llake-lady — Install Wizard

## Overview

`/llake-lady` is the one-time install wizard that sets up LoreLake in a project. It creates the `llake/` directory tree, writes `config.json`, generates an install plan, and spawns an executor subagent to wire the git `post-merge` hook. The final phase of the install plan runs `/llake-doctor` automatically to verify the install. After `/llake-lady` completes, the project is wired for ongoing ingest and the user can proceed to `/llake-bootstrap` to populate the wiki.

Run it once, from the project root, in a Claude Code session. Do not run it against a project that already has an `llake/` directory — use `/llake-doctor` for drift repair and upgrades.

---

## When to use it

- First-time LoreLake install in a project.
- The project does not yet have an `llake/` directory.

Do **not** use it if `llake/` already exists — it will refuse with a message pointing to `/llake-doctor`. See [[llake-doctor-skill]] for repair and upgrade scenarios.

---

## Preconditions

Before invoking, confirm:

1. **You are in the project root.** The skill uses `pwd` as the project root. A wrong working directory produces a wrong install path. The skill echoes both resolved paths before Phase 1 — check them.
2. **The project looks like a real project root.** At least one of `.git/`, `CLAUDE.md`, or a common manifest (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`) must be present.
3. **Git is initialized (soft requirement).** A non-git project is allowed — the skill emits a warning and continues, but the post-merge ingest hook will not be wired until you run `git init` followed by `/llake-doctor`.
4. **Plugin templates are intact.** `templates/config.default.json`, `templates/plan.md.tmpl`, and `templates/index.md.tmpl` must be readable from the plugin root. If any is missing, reinstall the plugin.
5. **No existing `llake/` directory.** The skill stops if one is found.

---

## What the skill does

The wizard runs eight phases, with a clear boundary between deciding (the wizard) and acting (the executor subagent):

### Phase 1 — Prerequisites check
Verifies the five preconditions listed above. Fails fast with a clear message on any failure; never auto-installs tools.

### Phase 2 — Discovery
Silently reads `CLAUDE.md`, `README.md`, project manifests, and the current git branch. Extracts the project name and default branch. Does **not** attempt to infer wiki categories — those emerge during `/llake-bootstrap`.

### Phase 3 — Mode selection
Asks one question: **Auto** (apply defaults, skip prompts) or **Interactive** (confirm each config key, pause for plan review). This is the only user prompt in auto mode.

### Phase 4 — Render config
Writes `<project>/llake/config.json` as a full copy of `templates/config.default.json` with discovered values applied (`ingest.branch` and, optionally, `prompts.ingest.EXAMPLES` when `CLAUDE.md` supplies enough domain signal). All `_comment` fields are preserved. In interactive mode, each config key is confirmed before writing.

The wizard creates only `llake/` itself here — the executor handles the rest of the directory tree.

### Phase 5 — Plan generation
Reads `templates/plan.md.tmpl` and substitutes:
- `{{PROJECT_NAME}}` — from manifest `name` field or directory basename.
- `{{DATE}}` — today (`YYYY-MM-DD`).
- `{{PLUGIN_PATH}}` — absolute plugin root (not a symlink).
- `{{PROJECT_ROOT}}` — absolute project path.
- `{{EMBEDDED_CONFIG}}` — the just-written config, with `_comment` fields, making the plan self-contained.

Writes the filled plan to `<project>/llake/install-plan.md`. This is the last file the wizard writes.

### Phase 6 — User review (interactive mode only)
In auto mode this phase is skipped entirely. In interactive mode, the user reviews the plan and either types `execute` to proceed or any other response to abort. An aborted plan is saved and can be resumed later from any Claude Code session by asking the model to execute the plan file.

### Phase 7 — Execute
Spawns an executor subagent (`Agent` tool, `general-purpose`) with a self-contained prompt. The executor:
- Walks the plan's checkboxes in order.
- Checks idempotently before each step — safe to re-run after an interrupt.
- Creates the full `llake/` directory tree.
- Appends `llake/.state/` to `.gitignore`.
- Writes and `chmod +x`s `.git/hooks/post-merge` with a shim that calls the plugin's `hooks/post-merge.sh`.
- Invokes `/llake-doctor` as Phase 4 of the plan to verify the install.
- Appends a phase-complete log line to `llake/log.md` after each plan phase.

The executor does **not** run `/llake-bootstrap` — that is the user's next step.

### Phase 8 — Completion summary
Reports the install plan path, config path, log path, the doctor report, and the recommended next step (`/llake-bootstrap`).

---

## What it creates

| Path | Description |
|---|---|
| `<project>/llake/config.json` | Full project config (all knobs, all `_comment` fields) |
| `<project>/llake/install-plan.md` | Self-contained plan; resume-safe across sessions |
| `<project>/llake/index.md` | Category catalog (populated by bootstrap) |
| `<project>/llake/log.md` | Append-only activity log |
| `<project>/llake/last-ingest-sha` | Ingest cursor (empty until bootstrap runs) |
| `<project>/llake/wiki/discussions/`, `decisions/`, `gotchas/`, `playbook/` | Four fixed-category stub indexes |
| `<project>/llake/.state/agents/`, `.state/sessions/` | Runtime working dirs (gitignored) |
| `<project>/.git/hooks/post-merge` | Shim that calls `<plugin>/hooks/post-merge.sh` |
| `<project>/.gitignore` (modified) | `llake/.state/` appended |

The wizard itself writes only `llake/config.json` and `llake/install-plan.md`. Everything else is the executor subagent's work.

---

## The install plan and the wizard-executor split

The plan is deliberately self-contained. Any future Claude Code session can resume or re-execute it by asking the model to "execute this plan: `<path>`" — the wizard is not required after Phase 5. The executor reads `log.md` on entry, finds the last "Phase N complete" line, and resumes from there. Keeping *deciding* (wizard) separate from *acting* (executor) is what makes idempotent resumption possible.

---

## What to do when it fails

| Symptom | Likely cause | Fix |
|---|---|---|
| "The current directory does not look like a project root" | Wrong CWD at invocation | `cd` into the project root and re-run |
| "Plugin install appears incomplete (missing `<file>`)" | Plugin files deleted or corrupted | Reinstall the plugin |
| "LoreLake is already installed" | `llake/` exists | Run `/llake-doctor` to repair, or manually remove `llake/` to start over |
| Executor stops mid-run | Interrupted or subagent error | Re-invoke: the executor resumes from `log.md`'s last completed phase |
| Doctor reports issues at the end | Hook not wired, config drift, missing files | Run `/llake-doctor` directly — the same checks it ran are idempotent |
| Non-git project warning | No `.git/` at install time | After `git init`, run `/llake-doctor` to finish wiring the post-merge hook |

---

## When to re-run vs. when to use `/llake-doctor`

| Scenario | Use |
|---|---|
| No `llake/` exists yet | `/llake-lady` |
| `llake/` exists but hooks are unwired (fresh clone, git init after install) | `/llake-doctor` |
| Config has missing keys after a plugin upgrade | `/llake-doctor` (forward-merge) |
| Suspected drift after manual edits | `/llake-doctor` |
| Want to start over from scratch | Delete `llake/`, then `/llake-lady` |

---

## Key Points

- The wizard writes exactly two files: `llake/config.json` and `llake/install-plan.md`. The executor writes everything else.
- The install plan is self-contained and resume-safe — any future session can re-execute it without re-running the wizard.
- Auto mode is the default and skips all prompts except the initial mode question. Interactive mode confirms each config key and pauses for plan review.
- `/llake-doctor` runs automatically as the final phase of the install plan; no need to run it manually after a successful install.
- After install, the next step is `/llake-bootstrap` to populate the wiki.
- The skill never auto-installs git, python, or any other tool. If a prerequisite is missing, it stops and tells you exactly what to fix.

---

## Code References

- `skills/llake-lady/SKILL.md` — full wizard spec (all eight phases)
- `templates/plan.md.tmpl` — install plan template with placeholder definitions
- `templates/config.default.json` — canonical config defaults and `_comment` annotations
- `templates/index.md.tmpl` — `llake/index.md` template used by the executor
- `hooks/post-merge.sh` — the hook the executor shim calls
- `hooks/lib/detect-project-root.sh` — project root detection contract

---

## See Also

- [[llake-doctor-skill]] — diagnose and repair drift after install, fresh clone, or upgrade
- [[llake-bootstrap-skill]] — populate the wiki after install
- [[plugin-project-duality]] — what the plugin root vs. the project's `llake/` contain
- [[runtime-layout]] — full directory tree created at install
- [[post-merge-hook]] — what the wired hook does after each merge
