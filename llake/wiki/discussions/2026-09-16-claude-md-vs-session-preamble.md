---
title: "Session: Why No Skill Writes to CLAUDE.md"
description: "Clarified that LoreLake never edits CLAUDE.md — context injection is the SessionStart hook's job, and it snapshots the wiki at session start"
tags: [discussion, session-start, bootstrap, claude-md]
created: 2026-09-16
updated: 2026-09-16
status: immutable
related:
  - "[[session-start-hook]]"
  - "[[session-preamble-snapshot-timing]]"
  - "[[debug-hook-failures]]"
---

# Session: Why No Skill Writes to CLAUDE.md

## Key Facts (immutable)

- The user installed LoreLake into a separate project, ran `/llake-bootstrap`, and found no instructions in that project's `CLAUDE.md` about what LoreLake is or how to use it, and asked whether some skill is supposed to add them.
- Confirmed: **no LoreLake skill writes to `CLAUDE.md`, by design.** `skills/llake-lady/SKILL.md`, `skills/llake-doctor/SKILL.md`, and `skills/llake-bootstrap/SKILL.md` each explicitly list editing `CLAUDE.md` as out of scope, citing the same reasoning: "The SessionStart hook handles operating-context injection for every session."
- The actual mechanism is `hooks/session-start.sh`, wired to Claude Code's `SessionStart` event, which concatenates `templates/session-preamble.md` with the project's `llake/index.md` and returns the result as `additionalContext`. This is invisible in the terminal transcript — there is no on-screen confirmation that it happened.
- Root cause of the user's specific symptom: the session in which `/llake-bootstrap` runs already had its `SessionStart` fire *before* the wiki existed, so that session's injected context is the pre-bootstrap (empty) snapshot. A new session is needed to see the populated catalog. Captured as [[session-preamble-snapshot-timing]].
- Diagnostic path when context still seems missing after a restart: check `llake/.state/hooks.log` for a `session-start ... context injected` line; its absence or a `skipped: no preamble or index` message points to a `detect_project_root` failure or a disabled plugin, which `/llake-doctor` checks for.
- The session was cut off mid-discussion on an open follow-up question — "should LoreLake also write to `CLAUDE.md`?" — no decision was reached in this transcript.

## Summary

The user asked why a fresh LoreLake install showed no CLAUDE.md instructions after bootstrap. The assistant clarified that this is intentional: LoreLake injects operating context live via the `SessionStart` hook rather than editing `CLAUDE.md`, and pointed out the three skills that explicitly declare this out of scope. The likely explanation for the user's specific case was that the bootstrap session's own context snapshot predates the wiki it just built, so a session restart should surface the populated index. The transcript ends before the open question of whether to *also* write to `CLAUDE.md` was resolved.

## Pages Created/Updated

- [[session-preamble-snapshot-timing]] — created (gotcha: SessionStart snapshots index.md once, before bootstrap can populate it)
- [[debug-hook-failures]] — updated (added a bootstrap-stale-context common cause and symptom)
