---
title: "Hook Log Library"
description: "Shared hooks.log helpers: paired start/end lines, rotation, crash marking, and sanitized render-failure reporting"
tags: [hooks, shell, logging, observability]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[post-merge-hook]]"
  - "[[session-start-hook]]"
  - "[[session-capture-worker]]"
  - "[[debug-hook-failures]]"
  - "[[ingest-v2-orchestrator]]"
  - "[[session-end-hook]]"
  - "[[ingest-gate]]"
---
# Hook Log Library

## Overview

`hooks/lib/hook-log.sh` owns the format and lifecycle of `<project>/llake/.state/hooks.log`. Every LoreLake hook sources it, so one file defines what the audit log looks like.

It was extracted because `post-merge.sh` and `session-end.sh` carried byte-identical inline copies of the same helpers, which had already begun to drift. `session-start.sh` — previously silent — was then wired in, so all three hooks now log uniformly.

It is bash 3.2 portable. See [[bash-3-2-portability]].

## The log format

```
2026-04-23 10:14:00 | post-merge    | started → done: spawned agent calm-owl-101400-1f3a (commits: a1b2..e4f5)
2026-04-23 10:14:02 | session-start | started → context injected
2026-04-23 10:19:42 | agent-done    | completed: agent calm-owl-101400-1f3a finished (exit 0)
```

Three pipe-separated fields: timestamp, a 13-character left-padded actor name, and the message. The fixed-width actor column is what makes the log skimmable by eye — which matters, because this file is the first thing anyone opens when a hook misbehaves. See [[debug-hook-failures]].

## The five functions

### `hook_start <name> <log_file> <config_file> <lib_dir>`

Opens a line. Three things happen in order:

1. **Crash marking.** If the file's last byte is not a newline, a previous writer died between `hook_start` and `hook_end`. The dangling line is closed with ` → CRASHED` before the new one begins. This is how a hard kill — system shutdown, `SIGKILL` — leaves evidence in the log rather than silently merging two runs onto one line.
2. **Rotation.** If the line count exceeds `logging.maxLines`, the file is truncated to its last `logging.rotateKeepLines` lines via a tempfile and `mv`.
3. **The open line.** `printf "%s | %-13s | started"` with **no trailing newline**.

### `hook_end <outcome> <log_file>`

Appends ` → <outcome>\n`, completing the open line. Every `hook_start` needs exactly one `hook_end` on every exit path — an early `exit` that skips it is what produces a `CRASHED` marker on the *next* run.

### `hook_log_line <name> <outcome> <log_file>`

A complete one-shot line with no pairing. Used where there is no meaningful start/end split — notably the SessionEnd foreground, which only records `dispatched (async)` before forking the worker (the worker then opens its own paired line).

### `log_render_failure <label> <exit_code> <err_text> <agent_log>`

Writes a multi-line block into the **agent** log:

```
=== TRIAGE RENDER FAILED: exit 1 at 2026-04-23 14:05:01 ===
render-prompt: unresolved placeholder {{FOO}}
```

The label is empty for legacy ingest and `TRIAGE`, `CAPTURE`, `PLANNER`, or `FIXER` elsewhere. An empty `err_text` renders as `(renderer produced empty prompt with exit N)`, so "the renderer said nothing" and "the renderer was never run" stay distinguishable.

### `render_err_summary <err_text>`

Returns a one-line summary safe for `hooks.log`: first line only, `\r`, `\n`, `\t` and **`|` stripped**, capped at 200 characters, defaulting to `empty prompt`.

Stripping the pipe is a security fix, not cosmetics. Renderer stderr can echo content from `config.json` — a per-prompt custom slot value, for instance. A crafted slot containing `|` or a newline could forge additional log fields or entire log lines, making a hostile change look like a routine `completed` entry. Sanitizing at the single point where untrusted text enters the log closes that off.

## Usage pattern

```bash
source "$LIB_DIR/hook-log.sh"
hook_start "$HOOK_NAME" "$LOG_FILE" "$CONFIG_FILE" "$LIB_DIR"

if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${LLAKE_AGENT_ID:-unknown}]" "$LOG_FILE"
  exit 0
fi
# ...
hook_end "done: spawned agent $AGENT_ID" "$LOG_FILE"
```

Background subshells do **not** use these helpers for their completion lines — they `printf` directly with the `agent-done` / `triage-done` actor name, because by then the foreground's paired line is long closed and a second `hook_start` would interleave badly.

## Key Points

- One file defines the `hooks.log` format for all three hooks plus the capture worker.
- `hook_start` marks a previous unterminated line ` → CRASHED`, so hard kills leave evidence.
- Rotation is driven by `logging.maxLines` / `logging.rotateKeepLines` and happens on `hook_start`.
- Every `hook_start` needs a matching `hook_end` on every exit path.
- `hook_log_line` is for unpaired one-shot entries, such as the SessionEnd async dispatch.
- `render_err_summary` strips `|`, CR, LF and tab — a log-injection fix, since renderer stderr can echo config content.
- Background subshells write completion lines with raw `printf` under `agent-done`, not via these helpers.

## Code References

- `hooks/lib/hook-log.sh:16-35` — `hook_start`: crash marking, rotation, open line
- `hooks/lib/hook-log.sh:23` — the `CRASHED` detection (`tail -c 1`)
- `hooks/lib/hook-log.sh:30-32` — rotation
- `hooks/lib/hook-log.sh:37-41` — `hook_end`
- `hooks/lib/hook-log.sh:43-48` — `hook_log_line`
- `hooks/lib/hook-log.sh:58-76` — `log_render_failure`
- `hooks/lib/hook-log.sh:86-92` — `render_err_summary`
- `hooks/post-merge.sh:66` — `hook_start` in the ingest hook
- `hooks/session-start.sh:44` — `hook_start` in the context-injection hook
- `hooks/session-end.sh:49` — `hook_log_line` for the async dispatch
- `tests/hooks/test_hook_log.sh` — unit tests

## See Also

- [[debug-hook-failures]] — how to read this log when something goes wrong
- [[post-merge-hook]], [[session-start-hook]], [[session-capture-worker]] — the callers
- [[bash-3-2-portability]] — the portability constraints this file observes
- [[config-schema]] — the `logging.*` keys
