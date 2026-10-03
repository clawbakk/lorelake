---
title: "Append-only LoreLake files conflict on every parallel merge — hence merge=union"
description: "log.md and the four fixed-category indexes conflict on every merge/rebase of parallel branches unless llake/.gitattributes marks them merge=union; never add wiki pages to those rules"
tags: [gotchas, git, merge, gitattributes, install]
created: 2026-09-22
updated: 2026-09-22
status: current
related:
  - "[[runtime-layout]]"
  - "[[llake-doctor-skill]]"
  - "[[llake-lady-skill]]"
  - "[[frontmatter-parser]]"
  - "[[adr-slug-collisions]]"
---
# Append-only LoreLake files conflict on every parallel merge

## Overview

Five files in `llake/` are append-only: `log.md` and the four fixed-category indexes (`wiki/discussions/discussions.md`, `wiki/decisions/decisions.md`, `wiki/gotchas/gotchas.md`, `wiki/playbook/playbook.md`). Capture and ingest add an entry at the end of `log.md`, add a row at the end of an index table, and bump the index's frontmatter `updated:` line. When two branches (or two git worktrees) both capture a session, they edit the same lines from the same base. Git then reports a textual conflict on **every** merge and rebase between them, even though the two additions are completely independent.

The plugin fixes this with `templates/gitattributes`. Each project gets it as a committed `llake/.gitattributes` that marks exactly those five paths `merge=union`. This page explains the failure, the fix, and the traps around it.

## What goes wrong without the rules

- Branch A captures a session. It appends a row for its new page to `gotchas.md`, sets that index's `updated:` to its date, and appends a `## [date] session-capture | ...` entry to `log.md`.
- Branch B, cut from the same base, does the same for its own page.
- Merging B into A, or rebasing either one, conflicts in both files. Both sides appended at the same position (end of file), and both changed the `updated:` line differently.

New wiki pages never conflict, because each one is a uniquely named file. Numbered ADR slugs are the exception: they collide *without* a conflict (see [[adr-slug-collisions]]). Parallel worktrees make these conflicts routine. Each worktree has its own checkout of `llake/`, so sessions in different worktrees append to separate copies of the same files.

## The fix: git's built-in `union` driver

The rules in `templates/gitattributes` (patterns are relative to `llake/` and anchored with a leading `/`):

```
/log.md                           merge=union
/wiki/discussions/discussions.md  merge=union
/wiki/decisions/decisions.md      merge=union
/wiki/gotchas/gotchas.md          merge=union
/wiki/playbook/playbook.md        merge=union
```

`merge=union` tells git to keep the lines from both sides instead of writing conflict markers. That is the right resolution for rows and log entries that are independent of each other. The change alters no wiki content and no writer behavior. Because the file is committed, it works for teammates as well as for one person's worktrees. It was verified against real git: merge, rebase, and merge-back all complete cleanly after two branches each appended to an index and to `log.md`.

### The duplicate `updated:` line

Union keeps both sides' `updated:` lines, so a merged index can briefly have two `updated:` keys. This is harmless. `parse()` in `hooks/lib/frontmatter.py` stores keys in a dict, so the last occurrence wins, and the next writer that serializes the index collapses them back to one. Dropping the `updated:` bump from category indexes was considered and rejected, since the duplicate is only cosmetic.

### How the rules reach a project

- **New installs:** Phase 1 of the install plan (`templates/plan.md.tmpl`) copies `templates/gitattributes` verbatim, comments included, to `<project>/llake/.gitattributes`. `/llake-lady` checks up front that the template is readable. See [[llake-lady-skill]].
- **Existing installs:** `/llake-doctor` Check 2.5 compares the installed file against the template and appends only the missing rules. Doctor is the upgrade path, so nobody has to reinstall. See [[llake-doctor-skill]].

## Traps

1. **Never add a wiki page to the rules.** On a page, union would silently keep both versions of prose that two sessions rewrote. The result is a garbled page instead of a real conflict that a human should resolve. `test_rules_do_not_apply_to_ordinary_wiki_pages` checks that an ordinary page reports `merge: unspecified`.
2. **GitHub's pull-request conflict check ignores `.gitattributes`.** A PR can show conflicts in `log.md` or an index even though local git would resolve them. Sync the branch locally (rebase onto the ingest branch, or merge it in), let the union driver resolve the conflicts, then push.
3. **A new fixed category needs a new rule.** `test_rules_cover_log_and_every_fixed_category_index` builds the expected rule set from `llake.fixedCategories` in `templates/config.default.json`. Adding a fixed category without a matching line in `templates/gitattributes` fails the test suite. Once the line is added, existing installs pick it up through doctor.
4. **Only these five paths are covered.** The root `llake/index.md` and the indexes of project-specific categories (for example `wiki/hooks/hooks.md`) have no union rule. Concurrent additions there can still conflict and must be resolved by hand, usually by keeping both rows.
5. **Doctor compares fields, not raw lines.** Check 2.5 splits each rule into its `<pattern> merge=union` fields, so re-aligning the columns doesn't trigger a repair. Lines that aren't in the template belong to the user and are never touched.

## Alternatives considered

Routing all capture writes to the checkout that holds `ingest.branch` would have removed GitHub PR flags and same-page conflicts too. It was rejected because it is a real design change to the writers. The narrow union rules solve the common case without changing how any writer behaves.

## Key Points

- `log.md` and the four fixed-category indexes are append-only. Without rules, they conflict on every merge or rebase of parallel branches or worktrees.
- `templates/gitattributes` is installed as a committed `llake/.gitattributes` that marks exactly those five paths `merge=union`.
- The install plan's Phase 1 installs it. `/llake-doctor` Check 2.5 repairs and upgrades it by appending only.
- Never add a wiki page to the rules, because union would silently merge rewritten prose.
- GitHub's PR conflict check ignores `.gitattributes`. Sync the branch locally to resolve.
- A duplicate `updated:` line after a union merge is harmless: the last one wins, and the next write collapses them.
- Every new fixed category needs a new rule, and the test suite enforces it.

## Code References

- `templates/gitattributes:18-22` — the five `merge=union` rules
- `templates/gitattributes:11-13` — why wiki pages must never be added
- `templates/gitattributes:15-17` — GitHub PR conflict-check caveat
- `templates/plan.md.tmpl:29` — install plan Phase 1 copy step
- `skills/llake-doctor/SKILL.md:74` — Check 2.5 (field-wise comparison)
- `skills/llake-doctor/SKILL.md:195` — additive `.gitattributes` fix
- `skills/llake-lady/SKILL.md:52` — template readability prerequisite
- `schema/core.md:25` — `.gitattributes` in the spec's directory tree
- `hooks/lib/frontmatter.py:59-97` — `parse()`; a duplicate key overwrites the earlier one (last wins)
- `tests/lib/test_gitattributes_template.py:96` — the rule set must equal `log.md` plus every fixed-category index
- `tests/lib/test_gitattributes_template.py:113` — parallel appends merge cleanly in a real git repo
- `tests/lib/test_gitattributes_template.py:137` — ordinary wiki pages stay `unspecified`

## See Also

- [[runtime-layout]] — where `llake/.gitattributes` sits in the project tree
- [[llake-doctor-skill]] — Check 2.5 and its additive repair
- [[llake-lady-skill]] — the install flow that copies the template
- [[frontmatter-parser]] — why a duplicate `updated:` key is harmless
- [[adr-slug-collisions]] — the opposite failure: parallel branches that merge cleanly but collide
