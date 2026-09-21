---
title: "Session Preamble Is Snapshotted at Session Start, Not Live"
description: "The bootstrap session's own SessionStart already fired before the wiki existed — so it never sees the wiki it just built"
tags: [gotchas, session-start, bootstrap, context-injection]
created: 2026-09-16
updated: 2026-09-16
status: current
related:
  - "[[session-start-hook]]"
  - "[[debug-hook-failures]]"
---

# Session Preamble Is Snapshotted at Session Start, Not Live

## What It Is

`hooks/session-start.sh` reads `<project>/llake/index.md` exactly once, at the moment `SessionStart` fires, and returns it as `additionalContext`. It is not re-read later in the same session. If `/llake-bootstrap` is run inside a session and populates `llake/wiki/` and `index.md` for the first time, **that same session's injected context is still the pre-bootstrap snapshot** — empty or missing index. The assistant that just finished bootstrapping has no live awareness of what it built.

## Why It's Surprising

Users running `/llake-bootstrap` for the first time expect the assistant to immediately "know" the wiki it just created, since the files are sitting right there in the project. Nothing in the terminal output signals that the context injection already happened and won't happen again until a new session starts — the preamble is invisible in the transcript by design (see [[session-start-hook]]), so there is no visual cue that it's stale.

## Symptoms

- Right after `/llake-bootstrap` completes, LoreLake seems to have no effect — the assistant doesn't reference the wiki it just wrote.
- The user checks `llake/index.md` and sees it's populated, which makes the missing context feel like a bug rather than expected snapshot timing.

## The Fix

Restart the Claude Code session (or start a new one) after `/llake-bootstrap` finishes. The new session's `SessionStart` fires against the now-populated `index.md` and injects the real catalog. This is not something bootstrap itself can work around — it cannot alter the `additionalContext` of the session it's already running in.

If restarting doesn't help, check `llake/.state/hooks.log` for a `session-start ... context injected` line. Its absence, or a `skipped: no preamble or index` message, points to a `detect_project_root` failure or a disabled plugin rather than the snapshot-timing issue — see [[debug-hook-failures]].

## Key Points

- `session-start.sh` reads `index.md` once per session, at `SessionStart` time — never again mid-session.
- The session that runs `/llake-bootstrap` cannot see the wiki it just created; a fresh session is required.
- No terminal output confirms injection happened, so a stale snapshot looks identical to a broken install.

## Code References

- `hooks/session-start.sh` — reads and emits `additionalContext` once, at hook invocation time

## See Also

- [[session-start-hook]] — full reference for the context-injection hook
- [[debug-hook-failures]] — how to distinguish this from a genuinely broken install via `hooks.log`
