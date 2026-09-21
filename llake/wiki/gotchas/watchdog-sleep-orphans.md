---
title: "Killing a watchdog subshell orphans its sleep"
description: "`kill $WATCHDOG_PID` kills only the watchdog subshell; its `sleep` child lingers for the full timeout. Reap watchdogs with `kill_tree`."
tags: [gotchas, bash, process-management, watchdog, hooks]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[agent-run]]"
  - "[[post-merge-hook]]"
  - "[[session-capture-worker]]"
  - "[[bash-3-2-portability]]"
---
# Killing a watchdog subshell orphans its `sleep`

## What goes wrong

Every hook that runs a background agent enforces its timeout with a watchdog subshell:

```bash
(
  sleep "$V2_TIMEOUT"
  if kill -0 "$MY_PID" 2>/dev/null; then kill -USR1 "$MY_PID" 2>/dev/null; fi
) &
WATCHDOG_PID=$!
```

`$WATCHDOG_PID` is the PID of the **subshell**, not of `sleep`. `sleep` is a separate child process that the subshell is waiting on.

When the agent finished before the timeout, the hooks used to stop the watchdog with `kill "$WATCHDOG_PID"`. That terminates the subshell, but the `sleep` it was waiting on is reparented and keeps running until the full timeout elapses. That can be up to `ingest.timeoutSeconds` / `ingest.v2.timeoutSeconds` (1200s by default) or `sessionCapture.timeoutSeconds` (600s). Every agent run leaked one such process. The orphan never fires `USR1`, because the subshell that would have sent it is gone. But it lingers in the process table, and any file descriptors it inherited from the hook stay open for that whole time.

The bug is easy to miss. Nothing fails, logs look normal, and the stray `sleep` processes only show up in `ps`.

## The fix

Reap the watchdog with `kill_tree` from `hooks/lib/agent-run.sh`, then `wait` for it:

```bash
kill_tree "$WATCHDOG_PID"
wait "$WATCHDOG_PID" 2>/dev/null
```

`kill_tree` walks the tree with `pgrep -P` and sends `SIGTERM` bottom-up, so `sleep` dies before its parent subshell. It is always available at these sites: every watchdog is created inside a subshell that has already sourced `agent-run.sh` for `setup_kill_trap`. `kill_tree`'s self-guard (`root != MY_PID`) is irrelevant here, because the watchdog is never `MY_PID`. See [[agent-run]].

## Where it applies

- `hooks/post-merge.sh:275`: v2 branch, after `run_ingest_v2` returns
- `hooks/post-merge.sh:395`: legacy branch, after the agent finishes
- `hooks/lib/session-capture-worker.sh:244`, `:292`, `:314`, `:341`, `:388`: triage render failure, triage failure, triage SKIP, capture render failure, and normal completion

## Rule

Whenever you background a subshell that runs a child command, stop it with `kill_tree`, not `kill`. `_agent_cleanup` already follows this rule for the agent tree (`kill_tree "$MY_PID"`). Any new watchdog or helper subshell should do the same.

## Key Points

- `$!` after `( ... ) &` is the subshell's PID. Its children are separate processes.
- `kill <subshell>` orphans a foreground `sleep` for the rest of its duration.
- Use `kill_tree "$WATCHDOG_PID"` followed by `wait`. All current watchdog sites do.
- The symptom is silent: stray `sleep` processes that live up to the configured timeout, one per agent run.

## Code References

- `hooks/lib/agent-run.sh:15-21` — `kill_tree`
- `hooks/post-merge.sh:269-276` — v2 watchdog creation and reap
- `hooks/post-merge.sh:395` — legacy watchdog reap
- `hooks/lib/session-capture-worker.sh:388` — capture-completion watchdog reap

## See Also

- [[agent-run]] — `kill_tree`, `setup_kill_trap`, and the watchdog pattern
- [[post-merge-hook]] — the ingest watchdogs
- [[session-capture-worker]] — the session-capture watchdog
- [[bash-3-2-portability]] — why `MY_PID` is derived via `sh -c 'echo $PPID'`
