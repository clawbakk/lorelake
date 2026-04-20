# Skill: /llake-lady

> Use this file as input for `/skill-creator`. Source of truth: `docs/specs/2026-04-18-lorelake-plugin-design.md` — section 8.2.

## Identity

- **Slash command:** `/llake-lady`
- **Name:** LoreLake Lady (Lady of the Lake reference — she hands you LoreLake)
- **Purpose:** Wizard that installs LoreLake into the current project.
- **Frontmatter required:** `disable-model-invocation: true`. Claude must NOT auto-trigger this skill — only explicit user invocation.

## What it does

The skill bootstraps a complete LoreLake setup in the user's current project. After running, the project has:
- `<project>/llake/` directory with config, index, log, and empty wiki
- Fixed-category dirs (`discussions/`, `decisions/`, `gotchas/`, `playbook/`) with stub index files
- `.gitignore` updated
- `.git/hooks/post-merge` wired (if git)
- Claude Code SessionStart/SessionEnd hooks registered globally
- An install plan written to `<project>/llake/install-plan.md`

The wizard does NOT bootstrap wiki content. That is `/llake-bootstrap`'s job (run separately when the user is ready).

## Inputs

- The user's current working directory (a project root, ideally containing `CLAUDE.md`)
- Plugin's `templates/config.default.json` (read for defaults menu)
- Plugin's `templates/plan.md.tmpl` (template for the install plan output)
- Plugin's `templates/index.md.tmpl` (template for the project's initial index.md)

## Flow (eight phases)

### Phase 1 — Prerequisites check

Verify before proceeding:

- Working directory is recognizable as a project root (any of: `.git/`, `CLAUDE.md`, `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`)
- Project is git-initialized (warn if not — explain post-merge ingest will not work until `git init` + `/llake-doctor`)
- Plugin's `templates/` dir is readable
- No existing `<project>/llake/` directory (if present → offer to run `/llake-doctor` instead)

On any failure: emit a clear message naming the missing prerequisite and the fix command. Do NOT auto-install missing tools.

### Phase 2 — Discovery

Read available signals without asking:

- `<project>/CLAUDE.md` (project description, conventions, branch hints, project type) - should be already injected in the session
- `<project>/package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle` (project type, dependencies)
- `<project>/README.md` (purpose)
- `git rev-parse --abbrev-ref HEAD` (default branch guess)

Note: project-specific categories are NOT discovered or pre-suggested. Only the four fixed categories are created at install. Project-specific categories emerge during `/llake-bootstrap` and ongoing ingest.

### Phase 3 — Mode selection

Ask the user one question:

> Auto (recommended) — apply defaults and discoveries silently, write everything. Or interactive — answer questions for each section of `config.default.json`.

### Phase 4 — Render config (Auto mode)

Render `<project>/llake/config.json` from `templates/config.default.json` with:
- Defaults from the template
- Discovered values applied (e.g., `ingest.branch` from git's current branch)
- `prompts.ingest.EXAMPLES` filled from CLAUDE.md if it provides enough domain signal; otherwise empty (generic fallback will render)
- `_comment` annotations preserved on each section

The wizard writes the FULL config (not minimal) — pedagogy beats minimalism, users see every knob immediately.

### Phase 4-alt — Interactive mode

Walk through each section of `config.default.json`. For each key:
- Show current default
- Show discovered value if any
- Accept user override or accept default
- Move to next key

Then render the resulting config to `<project>/llake/config.json`.

### Phase 5 — Plan generation

Render `templates/plan.md.tmpl` filled with:
- `{{PROJECT_NAME}}` — derived from directory name or package manifest
- `{{DATE}}` — today
- `{{PLUGIN_PATH}}` — absolute path to the plugin install
- `{{PROJECT_ROOT}}` — absolute path to the project
- `{{EMBEDDED_CONFIG}}` — the config.json content rendered in Phase 4 (for restore-from-plan)

Write to `<project>/llake/install-plan.md`.

### Phase 6 — User review (interactive mode only)

**Skipped in auto mode.** The point of auto mode is "trust the defaults, just do it" — pausing for plan review after that is friction without value, and the user has the executor's progress + the embedded plan to inspect afterward.

In **interactive mode**, display the absolute path of the plan and pause:

> Install plan written to `<path>`. Please review it before execution. When ready, type "execute" to proceed (or any other response to abort).

### Phase 7 — Execute

**In auto mode:** print a single-line announcement of what will happen, then proceed directly without pausing for input:

> Executing install plan (`<path>`). Will create `<project>/llake/`, append to `.gitignore`, wire `.git/hooks/post-merge`, and register Claude Code SessionStart/SessionEnd hooks in `~/.claude/settings.json`.

Then spawn the executor subagent.

**In interactive mode:** if the user approved in Phase 6, spawn the executor subagent. If they declined or interrupted, print:
> Plan saved at `<path>`. To resume later, in any Claude Code session run: "execute this plan: <path>"

The executor subagent walks through each checkbox sequentially in either mode, appending to `log.md` as it goes.

### Phase 8 — Completion summary

After execution (or on user-decline), print:
- Path to the install plan
- Path to the config
- Next recommended action: `/llake-bootstrap` (when the user is ready to populate the wiki)
- Note that doctor was already run as the plan's final phase

## Allowed tools

- `Read` — for templates, CLAUDE.md, package manifests, README
- `Write` — for config.json, install-plan.md
- `Bash` — for git commands and project detection
- `Agent` (or `Task`) — for spawning the executor subagent in Phase 7

## Behaviors out of scope

- Editing the project's `CLAUDE.md` (the SessionStart hook handles operating-context injection at session start)
- Bootstrapping wiki content (that is `/llake-bootstrap`)
- Suggesting project-specific categories (those emerge during bootstrap/ingest)
- Diagnosing existing installs (that is `/llake-doctor`)
- Auto-installing git or any other tool

## Reference

Design spec section 8.2 — `../../docs/specs/2026-04-18-lorelake-plugin-design.md`
