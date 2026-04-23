---
title: "/llake-doctor — Diagnose and Repair"
description: "Idempotent health checker that diagnoses and repairs LoreLake install drift"
tags: [skills, maintenance, repair]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[llake-lady-skill]]"
  - "[[llake-bootstrap-skill]]"
  - "[[llake-lint-skill]]"
  - "[[runtime-layout]]"
  - "[[config-schema]]"
---

# /llake-doctor — Diagnose and Repair

## Overview

`/llake-doctor` inspects every piece of LoreLake install state, repairs drift in place, and prints a structured report. Every operation is idempotent — running doctor twice produces the same report and the same final state as running it once. Doctor is deliberately a *diagnostician*, not an installer: if no `llake/` directory exists, it stops and directs you to `/llake-lady`.

The most common triggers are a fresh clone (where `.git/hooks/` is empty), a plugin upgrade that introduces new config keys, or suspected drift after manual edits to the `llake/` structure. Doctor is also invoked automatically as **Phase 4 of the `/llake-lady` install plan** — the same checks run whether the wizard or a resumed plan session triggers them.

---

## When to use it

| Situation | Run doctor? |
|---|---|
| Fresh clone of a project that already has `llake/` | Yes — rewires the post-merge hook |
| After `git init` on a non-git project | Yes — wires the post-merge hook that was deferred at install |
| After a plugin upgrade | Yes — forward-merges any new config keys |
| After manual edits to `llake/` structure | Yes — finds and repairs structural drift |
| At the end of `/llake-lady` install | Automatic — Phase 4 of the install plan |
| No `llake/` directory exists at all | No — use `/llake-lady` instead |

---

## Preconditions

- A `llake/` directory must exist at the detected project root. Doctor resolves the project root the same way the hooks do: environment variable `$LLAKE_PROJECT_ROOT`, then marker walk (ascending from `pwd` looking for `llake/config.json`), then git toplevel fallback if `<toplevel>/llake/` exists.
- If none of the above yields a project with `llake/`, doctor stops with: "No LoreLake install found near `<pwd>`. Run `/llake-lady` to install."

---

## What it checks (Phase 1 — Diagnose)

Doctor collects all findings before applying any fixes, so the final report shows the pre-repair state clearly.

### Check 1 — LoreLake structure
Verifies every file and directory the install plan creates:
- `llake/config.json` (exists and is valid JSON)
- `llake/index.md`, `llake/log.md`, `llake/last-ingest-sha`
- `llake/wiki/` directory
- Four fixed-category stub indexes: `wiki/discussions/discussions.md`, `wiki/decisions/decisions.md`, `wiki/gotchas/gotchas.md`, `wiki/playbook/playbook.md`
- `.state/`, `.state/agents/`, `.state/sessions/`

An invalid `config.json` (present but not parseable JSON) is a distinct issue — a broken config silently disables every hook.

### Check 2 — `.gitignore` entry
Confirms `llake/.state/` appears on its own line in `<project>/.gitignore`. The trailing slash matters (it scopes the ignore to the directory). Only this line is touched; the rest of the file is untouched.

### Check 3 — Git post-merge hook
Only runs if `.git/` exists. Verifies:
1. `<project>/.git/hooks/post-merge` exists.
2. Its contents are exactly the expected shim: `#!/bin/bash\nexec "<PLUGIN_ROOT>/hooks/post-merge.sh" "$@"` with the absolute plugin path substituted literally.
3. The shim is executable.

Separate issues are recorded for "missing", "drifted content" (stale path from a prior plugin location), and "not executable".

If `.git/` is absent, doctor records a **warning** (not an issue): the hook will be wired on the next doctor run after `git init`.

### Check 4 — Plugin manifest integrity
Verifies `hooks/hooks.json` in the plugin root is valid JSON and declares both `SessionStart` (pointing to `hooks/session-start.sh`) and `SessionEnd` (pointing to `hooks/session-end.sh`). A corrupted manifest is not auto-repaired (it is immutable plugin code, not user data) — the report instructs the user to reinstall the plugin.

### Check 5 — Stale manual hook entries
Before the plugin manifest was introduced, the installer wrote hook entries directly into `~/.claude/settings.json` and `<project>/.claude/settings.json`. These stale entries fire the hook a second time per session alongside the manifest's entry. Doctor finds and removes them. It never creates new hook entries anywhere — that is the manifest's job.

### Check 6 — Config schema version
Compares `_schemaVersion` in the user's `config.json` against the plugin's `templates/config.default.json`. A user version older than the plugin's is a "needs upgrade" issue (paired with Check 7). A user version newer than the plugin's produces a warning and no repair — doctor never downgrades configs.

### Check 7 — Config key coverage
For each leaf key in `templates/config.default.json`, checks whether the same dot-path exists in the user's `config.json`. Missing dot-paths become the forward-merge list for Phase 2. Keys present in the user's config but absent from defaults are user-managed leftovers — they are never deleted.

---

## What it repairs (Phase 2 — Repair)

Fixes are applied in a fixed order. Each fix is idempotent — if the target state already matches, it is skipped.

| Issue | Auto-repair |
|---|---|
| Missing directories | `mkdir -p` |
| Missing `config.json` | Copy `templates/config.default.json` verbatim (user edits lost — noted in report) |
| Invalid `config.json` JSON | Overwrite with `templates/config.default.json` — broken config is worse than lost edits |
| Missing `index.md` | Render from `templates/index.md.tmpl` with project name and today's date |
| Missing `log.md` | Create empty |
| Missing `last-ingest-sha` | Write current `git rev-parse HEAD`, or empty for non-git projects |
| Missing category stub index | Write minimal stub with YAML frontmatter and "No entries yet." placeholder |
| `.gitignore` line missing | Append `llake/.state/` (ensures file ends with newline first) |
| Post-merge hook missing, drifted, or not executable | Write correct shim and `chmod +x` in one operation |
| Plugin manifest issues | Report only — instructs user to reinstall (manifest is plugin code, not user data) |
| Stale manual `settings.json` entries | Remove matching entries from `~/.claude/settings.json` and `<project>/.claude/settings.json`; leave unrelated entries untouched |
| Missing config keys | Forward-merge from defaults: copy missing dot-paths, preserve user values, bring `_comment` annotations for new sections, update `_schemaVersion` |

**What doctor never repairs:**
- Retired config keys the plugin no longer ships — left in place (user decides to clean up).
- Downgrading a config whose `_schemaVersion` is newer than the plugin's.
- Wiki content — stub indexes are the maximum; populating pages is `/llake-bootstrap`.
- `<project>/CLAUDE.md` — never modified by doctor.

---

## The report (Phase 3)

Doctor prints a structured report. `[CHECK]` lines reflect state *before* any fix; `[FIX]` lines reflect what Phase 2 actually did. Re-running doctor immediately after a successful run should show the same `[CHECK]` list with all issues now `OK`.

Example format:
```
LoreLake Doctor — Report

Project: /absolute/path/to/project
Plugin:  /absolute/path/to/plugin

[CHECK] LoreLake structure         : OK
[CHECK] .gitignore                 : MISSING ENTRY
[CHECK] Post-merge hook            : NOT WIRED (git repo present)
[CHECK] Plugin manifest            : OK
[CHECK] Stale manual entries       : NONE
[CHECK] Config schema version      : OK (1)
[CHECK] Config key coverage        : 2 keys missing → merging from defaults

[FIX] Appending llake/.state/ to .gitignore           : DONE
[FIX] Wiring .git/hooks/post-merge                    : DONE
[FIX] Merging missing keys: ingest.exclude, foo.bar   : DONE

Summary: 3 issues, 3 fixed. LoreLake is healthy.
```

The summary line always appears last. Partial outcomes append counts for failed fixes. Non-git-repo state produces a `Note:` line after the summary rather than an issue count.

---

## Key Points

- Doctor is fully idempotent — safe to run any number of times.
- It resolves the project root the same way the hooks do (env override → marker walk → git toplevel), so you can invoke it from any subdirectory.
- It never creates a new LoreLake install from nothing — that is `/llake-lady`'s job.
- It never creates hook entries in any `settings.json` file; it only removes stale ones left by the pre-manifest installer.
- A broken `config.json` is treated as more dangerous than the loss of user edits — it is overwritten from defaults, which silently disables every hook if left in place.
- The `[CHECK]` section of the report shows pre-repair state, making before/after comparison easy.

---

## Code References

- `skills/llake-doctor/SKILL.md` — full doctor spec (all seven checks, all repairs, report format)
- `hooks/lib/detect-project-root.sh` — project root detection contract doctor follows
- `templates/config.default.json` — source of truth for forward-merge and schema version
- `templates/plan.md.tmpl` — install plan (Phase 4 of which invokes doctor)
- `templates/index.md.tmpl` — template used when `index.md` is missing
- `schema/core.md` — category stub frontmatter requirements

---

## See Also

- [[llake-lady-skill]] — initial install; run this if `llake/` does not exist
- [[llake-bootstrap-skill]] — populate wiki content after a healthy install
- [[runtime-layout]] — full directory tree doctor verifies
- [[config-schema]] — config keys, defaults, and `_schemaVersion` semantics
- [[post-merge-hook]] — the hook doctor wires via the `.git/hooks/post-merge` shim
