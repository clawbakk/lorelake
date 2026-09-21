---
title: "Session Capture Worker"
description: "Detached worker that does all two-pass triage→capture work after session-end.sh forks it"
tags: [hooks, shell, session-capture, triage, background-agents]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[session-end-hook]]"
  - "[[triage-template]]"
  - "[[capture-template]]"
  - "[[extract-transcript]]"
  - "[[hook-log]]"
  - "[[agent-run]]"
  - "[[format-agent-log]]"
  - "[[troubleshoot-session-capture]]"
  - "[[claude-p-tools-flag]]"
  - "[[watchdog-sleep-orphans]]"
---
# Session Capture Worker

## Overview

`hooks/lib/session-capture-worker.sh` contains everything the SessionEnd hook used to do inline: config reads, transcript extraction, thin-session filtering, session locking, and the two-pass triage → capture agent run. `hooks/session-end.sh` is now only a dispatcher that forks this script with `nohup` and exits. See [[session-end-hook]].

The split exists purely for foreground latency. Around twenty sequential `python3` calls ran synchronously on every `/exit` and `/clear` — 1.5–3 seconds of visible delay — even though the agents themselves were already detached. Moving them here brought the foreground to roughly 10ms.

## Invocation

```bash
bash hooks/lib/session-capture-worker.sh <tmp-input-file>
```

The single argument is a tempfile holding the hook's stdin JSON. The worker reads it and **deletes it immediately** (`hooks/lib/session-capture-worker.sh:29-30`), so an abandoned run leaves nothing in `/tmp`. A missing or unreadable argument is a silent `exit 0` — the worker may be invoked directly in tests and must not assume a well-formed environment.

From the payload it extracts `cwd`, `session_id`, and `transcript_path`, accepting both snake_case and camelCase spellings.

## Setup and guards

The worker runs `hook_start` under its own actor name, `session-capture-worker`, so its log lines are distinguishable from the foreground's `session-end` dispatch line. See [[hook-log]].

Guards, in order: recursion guard (belt-and-suspenders — the foreground already checked, but the worker is directly invocable), `sessionCapture.enabled`, prompt templates present, and transcript locatable. If `transcript_path` is missing or stale, it falls back to searching `~/.claude/conversations` for a file matching the session ID.

## Filters before any agent runs

1. **Extraction** via [[extract-transcript]], writing `transcript.md` plus `.turns` and `.words` sidecars, which the worker reads and deletes.
2. **Turn filter** — fewer than `sessionCapture.minTurns` and the session directory is removed.
3. **Word filter** — fewer than `sessionCapture.minWords`, same.
4. **Session lock** — a `meta` file in the session directory. If present and younger than `sessionCapture.lockStalenessSeconds`, another agent owns this session and the worker exits; a stale lock is taken over.

All four run before a single token is spent. Extraction exit code 2 (no visible messages) is logged distinctly from a general extraction failure.

## The intentional triage/capture asymmetry

This is the part of the file most likely to be "fixed" by a future reader, and the code carries a comment block saying so (`hooks/lib/session-capture-worker.sh:261-268`).

**Triage runs as a synchronous foreground pipeline** — no `&`. `${PIPESTATUS[0]}` on the following line therefore correctly captures claude's exit code:

```bash
claude ... | python3 "$FORMATTER" --extract-result "$TRIAGE_RESULT_FILE" >> "$AGENT_LOG" 2>&1
_triage_pstat=("${PIPESTATUS[@]}")
TRIAGE_EXIT="${_triage_pstat[0]}"
```

**Capture runs inside a backgrounded subshell** that ends with `exit "$CLAUDE_EXIT"`, because the outer code must `wait` for it — and a bare `wait` on a pipeline returns the *formatter's* exit code, which is always 0. Wrapping makes claude's code the subshell's code.

Two reviewers independently confirmed the asymmetry is correct, hence the comment. Note also `_pstat=("${PIPESTATUS[@]}")`: `PIPESTATUS` is reset by the next assignment, so it must be copied into a plain array before anything else touches it — a bash 3.2 constraint. See [[bash-3-2-portability]].

If the watchdog's `USR1` lands mid-pipeline, the trap in `agent-run.sh` exits the outer subshell with 143 before `TRIAGE_EXIT` is ever read, so that path is unreachable rather than merely unhandled.

## Pass 1 — triage

`--tools "Read"`, `--allowedTools "Read"`, `--strict-mcp-config`, and a **hard-coded** `--max-budget-usd 0.50`. Read-only and cheap by construction. See [[claude-p-tools-flag]] for why `--tools` is the flag that matters.

The classification is the first word of `triage-result.txt`, uppercased. If the file is missing, the worker defaults to `CAPTURE` — a fail-open choice: losing a session's knowledge is worse than spending one capture budget unnecessarily.

On `SKIP`, or on a nonzero triage exit, the session directory is removed and the worker exits 0. That `rm -rf` on triage failure is a deliberate contract, pinned by a test: a failed triage leaves no residue for a later run to mistake for a live lock.

## Pass 2 — capture

Runs only on `CAPTURE` or `PARTIAL`, with the configured model, effort, tool set, and budget. The prompt receives `TRIAGE_CLASSIFICATION` and `TRIAGE_REASON` among its slots, so the capture agent knows *why* it was invoked. See [[capture-template]].

## Render failures

Both passes route render failures through `log_render_failure` and `render_err_summary` (from [[hook-log]]), writing a labelled block to `agent.log` and a sanitized one-liner to `hooks.log`. Renderer stderr goes to `$AGENT_DIR/triage-render.err` / `capture-render.err` — per-agent rather than `/tmp`, so it survives a `SIGKILL` for post-mortem and never accumulates.

## Completion dispatch

A `formatter-exit` sidecar is checked before the normal exit-code chain, exactly as in [[post-merge-hook]]:

| Condition | `hooks.log` |
|---|---|
| Formatter crashed | `failed: formatter crashed (...) — see agent.log` |
| Exit 0 | `completed: agent <id> finished (exit 0)` |
| Exit 137/143 without the trap firing | `killed: agent <id> (exit N, external)` |
| Other nonzero | `failed: agent <id> (exit N)` |

Session capture has no SHA cursor, so a formatter crash costs the capture rather than holding anything; the distinct log line still tells the operator it was a formatter bug and not an external kill.

The session directory is removed after capture. On a **watchdog timeout** it is deliberately left intact for post-mortem — lock staleness handles takeover by the next agent.

Every exit path that stops the timeout watchdog reaps it with `kill_tree "$WATCHDOG_PID"` followed by `wait`, not a plain `kill`. There are five such paths: triage render failure, triage failure, triage `SKIP`, capture render failure, and normal capture completion. A plain `kill` only terminated the watchdog subshell and orphaned its `sleep` for the rest of `sessionCapture.timeoutSeconds`. See [[watchdog-sleep-orphans]].

`LLAKE_SESSION_CAPTURE_SYNC=1` makes the worker wait for its background subshell instead of disowning it.

## Key Points

- Holds all the logic SessionEnd used to run inline; the hook is now a ~10ms dispatcher.
- Deletes its stdin tempfile immediately on read.
- Re-checks the recursion guard, because it can be invoked directly.
- Four filters — extraction, turns, words, session lock — run before any agent starts.
- Triage is a synchronous pipeline; capture is a backgrounded subshell. The asymmetry is required by how `PIPESTATUS` and `wait` interact, and is documented in the source.
- `PIPESTATUS` must be copied into a plain array immediately (bash 3.2 resets it).
- Triage is `Read`-only with a hard-coded $0.50 cap and fails open to `CAPTURE` if its result file is missing.
- A failed triage `rm -rf`s the session directory; a watchdog timeout deliberately does not.
- Renderer stderr lives in the agent directory, not `/tmp`.
- `LLAKE_SESSION_CAPTURE_SYNC=1` forces synchronous execution for tests.
- The watchdog is always reaped with `kill_tree`, never a bare `kill`.

## Code References

- `hooks/lib/session-capture-worker.sh:24-34` — tempfile consumption and payload parsing
- `hooks/lib/session-capture-worker.sh:49-55` — `hook_start` and the recursion guard
- `hooks/lib/session-capture-worker.sh:131-156` — transcript extraction and sidecar reads
- `hooks/lib/session-capture-worker.sh:158-170` — turn and word filters
- `hooks/lib/session-capture-worker.sh:173-191` — session lock and staleness takeover
- `hooks/lib/session-capture-worker.sh:261-283` — the asymmetry comment, triage invocation, `PIPESTATUS` copy
- `hooks/lib/session-capture-worker.sh:287-296` — triage-failure path and session-dir removal
- `hooks/lib/session-capture-worker.sh:299-318` — classification parse, fail-open default, SKIP path
- `hooks/lib/session-capture-worker.sh:361-383` — capture subshell and `wait`
- `hooks/lib/session-capture-worker.sh:394-423` — formatter-crash detection and completion dispatch
- `hooks/lib/session-capture-worker.sh:244`, `:292`, `:314`, `:341`, `:388` — watchdog reaped with `kill_tree`
- `tests/hooks/test_session_capture_worker.sh` — happy path, watchdog timeout, triage residue, render failures, flag assertions

## See Also

- [[session-end-hook]] — the foreground dispatcher that forks this worker
- [[triage-template]] / [[capture-template]] — the two prompts
- [[extract-transcript]] — produces `transcript.md` and the sidecars
- [[hook-log]] — logging and render-failure helpers
- [[agent-run]] — `setup_kill_trap` and the watchdog
- [[claude-p-tools-flag]] — why both `--tools` and `--strict-mcp-config` are passed
- [[adr-002-two-pass-triage]] — why two passes
- [[troubleshoot-session-capture]] — decision tree when captures do not appear
