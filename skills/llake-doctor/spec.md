# Skill: /llake-doctor

> Use this file as input for `/skill-creator`. Source of truth: `docs/specs/2026-04-18-lorelake-plugin-design.md` — section 8.3.

## Identity

- **Slash command:** `/llake-doctor`
- **Name:** LoreLake Doctor
- **Purpose:** Diagnose and repair the LoreLake install state.
- **Frontmatter required:** `disable-model-invocation: true`.

## What it does

Idempotent, safe-to-run-anytime check + fix. Inspects every aspect of the LoreLake install and repairs missing or drifted parts in place.

## Use cases

- Fresh clone of a project that already has a `llake/` directory (the cloner doesn't have hooks wired locally)
- After `git init` on a previously git-less project (post-merge wiring deferred to here)
- Plugin upgrade introduced new files or new config keys
- Suspected drift after manual edits

Doctor is also invoked as the **final phase** of the install plan written by `/llake-lady`. This means doctor runs regardless of which session executes the plan.

## Inputs

- The user's current working directory (used to detect the project)
- Plugin's `templates/config.default.json` (used for the forward-merge of new keys)

## Checks (in order)

1. **Project root detection** — use `lib/detect-project-root.sh` (env override → marker walk → git toplevel). Fail clearly if no LoreLake project found.

2. **LoreLake structure**
   - `<project>/llake/config.json` exists, valid JSON
   - `<project>/llake/index.md`, `log.md`, `last-ingest-sha` exist
   - `<project>/llake/wiki/` exists
   - `<project>/llake/wiki/discussions/`, `decisions/`, `gotchas/`, `playbook/` exist with stub category index files
   - `<project>/llake/.state/` exists with `agents/` and `sessions/` subdirs

3. **`.gitignore`**
   - `<project>/.gitignore` includes `llake/.state/`

4. **Git post-merge hook** (only if `<project>/.git/` exists)
   - `<project>/.git/hooks/post-merge` shim exists
   - Shim contents reference the plugin's `post-merge.sh` (re-derive expected content from PLUGIN path)
   - Shim is executable

5. **Claude Code hooks**
   - SessionStart hook entry registered (in `~/.claude/settings.json` OR `<project>/.claude/settings.json`)
   - SessionEnd hook entry registered
   - Each entry's command path points at the plugin's actual `hooks/session-{start,end}.sh`

6. **Config schema version**
   - Read user's `config.json _schemaVersion`
   - Read plugin's `config.default.json _schemaVersion`
   - Compare; if user is older, schedule a forward-merge in fixes

7. **Config key coverage**
   - For each leaf key in `config.default.json`, check whether it exists in the user's `config.json`
   - List missing keys for the merge

## Fixes

For each issue found:

- **Missing files**: create from templates (`templates/config.default.json`, `templates/index.md.tmpl`, etc.)
- **Missing hooks**: write the shim, set executable, register in CC settings
- **Missing `.gitignore` entry**: append
- **Schema version mismatch + missing keys**: forward-merge — for each missing key, copy the default value from `config.default.json` into the user's `config.json` at the same path. Preserve all existing user values. Do NOT remove keys the user has that the plugin no longer has (those are user-managed leftovers).
- **Drifted shim contents**: rewrite the shim with correct content
- **Bump `_schemaVersion`** in user's config to match plugin's after merge

## Output

Print a structured report:

```
LoreLake Doctor — Report

Project: /path/to/project
Plugin: /path/to/plugin

[CHECK] LoreLake structure         : OK
[CHECK] .gitignore                 : MISSING ENTRY
[CHECK] Post-merge hook            : NOT WIRED (git repo present)
[CHECK] CC SessionStart hook       : OK
[CHECK] CC SessionEnd hook         : OK
[CHECK] Config schema version      : OK (1)
[CHECK] Config key coverage        : 2 keys missing → merging from defaults

[FIX] Appending llake/.state/ to .gitignore        : DONE
[FIX] Wiring .git/hooks/post-merge                    : DONE
[FIX] Merging missing keys: ingest.exclude, foo.bar    : DONE

Summary: 3 issues, 3 fixed. LoreLake is healthy.
```

If no issues: `Summary: 0 issues. LoreLake is healthy.`

## Allowed tools

- `Read` — for config files, hook files, settings
- `Write` — for repairs
- `Edit` — for config merges
- `Bash` — for git checks, file ops, chmod

## Behaviors out of scope

- Removing config keys the plugin no longer ships (user-managed)
- Editing the user's `CLAUDE.md` (never)
- Bootstrapping wiki content (that is `/llake-bootstrap`)
- Initial install (that is `/llake-lady`)

## Reference

Design spec section 8.3.
