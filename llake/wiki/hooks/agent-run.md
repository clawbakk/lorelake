---
title: "Agent Run Library"
description: "Kill-trap helpers, timeout watchdog, and cleanup for background agent processes"
tags: [hooks, shell, agents, process-management, timeout]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[agent-id]]"
  - "[[session-end-hook]]"
  - "[[post-merge-hook]]"
  - "[[bash-3-2-portability]]"
---

## Overview

`hooks/lib/agent-run.sh` is the most important shell library in LoreLake. It provides the process management infrastructure shared by every hook that spawns a background `claude -p` agent: `session-end.sh` and `post-merge.sh`. The library solves two problems that arise when running long-lived subprocesses from Claude Code hooks:

1. **Clean termination on user interrupt** — when the user stops the hook (via the extension button or Ctrl+C), the entire agent process tree must be killed, not just the immediate child.
2. **Timeout enforcement** — agents have a configured budget (`timeoutSeconds`). A watchdog subshell sends `USR1` to the hook's own process after that duration, triggering the same cleanup path.

Both paths converge on `_agent_cleanup`, which tree-kills descendants, writes a human-readable marker to the agent log, and appends a one-liner to `hooks.log`.

## Public API

### `setup_kill_trap`

```sh
setup_kill_trap() {
  trap '_agent_cleanup user' TERM INT
  trap '_agent_cleanup timeout' USR1
}
```

Call this once near the top of any hook script that spawns an agent. It installs two signal handlers:

| Signal | Source | Meaning |
|--------|--------|---------|
| `TERM` | Claude Code extension, `kill` | User stopped the agent |
| `INT` | Ctrl+C in terminal | User interrupted |
| `USR1` | Hook's own watchdog subshell | Configured timeout elapsed |

`TERM`/`INT` both invoke `_agent_cleanup user`. `USR1` invokes `_agent_cleanup timeout`. The two paths produce different log messages but identical cleanup behavior.

### `kill_tree <root_pid>`

```sh
kill_tree() {
  local root=$1
  for child in $(pgrep -P "$root" 2>/dev/null); do
    kill_tree "$child"
  done
  [ "$root" != "$MY_PID" ] && kill -TERM "$root" 2>/dev/null
}
```

Recursively walks a process tree depth-first, sending `SIGTERM` to each node bottom-up. The self-guard (`[ "$root" != "$MY_PID" ]`) prevents the hook from terminating itself before it finishes cleanup. `MY_PID` must be set by the caller before `setup_kill_trap` is called (see "Caller Responsibilities" below).

### `_agent_cleanup <reason>`

```sh
_agent_cleanup() {
  local reason=$1
  trap '' TERM INT USR1 EXIT   # prevent re-entry

  kill_tree "$MY_PID"
  sleep 1
  pkill -KILL -P "$MY_PID" 2>/dev/null   # force-kill any survivors

  local phase=""
  [ -n "$CURRENT_PID_FILE" ] && phase=$(basename "$CURRENT_PID_FILE" .pid)

  [ -n "$CURRENT_PID_FILE" ] && rm -f "$CURRENT_PID_FILE"

  # Append kill marker to agent.log
  if [ -n "$AGENT_LOG" ]; then
    if [ "$reason" = "timeout" ]; then
      echo "=== TIMEOUT during ${phase:-unknown} (session exceeded ${MAX_TIMEOUT_SEC}s) at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    else
      echo "=== KILLED by user during ${phase:-unknown} at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    fi
  fi

  # Append one-liner to hooks.log
  if [ -n "$HOOKS_LOG_FILE" ] && [ -n "$LLAKE_AGENT_ID" ]; then
    if [ "$reason" = "timeout" ]; then
      printf "%s | %-13s | timeout: agent %s killed after %ss (phase: %s)\n" \
        "$(date '+%Y-%m-%d %H:%M:%S')" "watchdog" "$LLAKE_AGENT_ID" "$MAX_TIMEOUT_SEC" "${phase:-unknown}" >> "$HOOKS_LOG_FILE"
    else
      printf "%s | %-13s | terminated: agent %s killed by user (phase: %s)\n" \
        "$(date '+%Y-%m-%d %H:%M:%S')" "user-kill" "$LLAKE_AGENT_ID" "${phase:-unknown}" >> "$HOOKS_LOG_FILE"
    fi
  fi

  exit 143
}
```

The function:

1. **Blanks all traps** immediately to prevent recursive invocation if another signal arrives during cleanup.
2. **Tree-kills all descendants** with `kill_tree`, then waits one second and force-kills survivors with `SIGKILL`.
3. **Identifies the active phase** from the basename of `CURRENT_PID_FILE` (e.g., `triage.pid` → phase `triage`). This lets the log show exactly which step was interrupted.
4. **Removes the pid file** but preserves everything else (`AGENT_LOG`, `AGENT_DIR`, `SESSION_DIR`) for post-mortem inspection.
5. **Writes a marker** to `agent.log` with the reason, phase, and timestamp.
6. **Appends a one-liner** to `hooks.log` (the hook-level audit trail).
7. **Exits 143** (128 + SIGTERM's 15), the conventional exit code for SIGTERM-terminated processes.

## Caller Responsibilities

Before calling `setup_kill_trap`, the hook script must set these variables in scope:

| Variable | Required | Description |
|----------|----------|-------------|
| `MY_PID` | yes | The outer subshell's PID. **Must** be obtained via `sh -c 'echo $PPID'` — not `$$`, which varies with subshell depth, and not `$BASHPID`, which requires bash 4+ |
| `CURRENT_PID_FILE` | yes | Path to the `.pid` file for the currently running phase |
| `AGENT_LOG` | yes | Path to `agent.log` inside the agent working directory |
| `MAX_TIMEOUT_SEC` | yes | Numeric timeout in seconds, used in the timeout log message |
| `HOOKS_LOG_FILE` | optional | Path to `hooks.log`; if unset, the one-liner is skipped |
| `LLAKE_AGENT_ID` | optional | Agent ID from `agent-id.sh`; if unset, hooks.log one-liner is skipped |

## Why USR1 for Timeout, Not TERM?

Using `TERM` for the watchdog would be ambiguous — both the user and the watchdog would produce identical log entries. By reserving `USR1` for the self-timeout signal, `_agent_cleanup` can log a precise reason (`timeout` vs. `user`) without extra state. It also avoids any race where a real `TERM` from Claude Code arrives at the same moment the watchdog fires.

The watchdog pattern looks like this in hook scripts:

```sh
# Watchdog subshell — sends USR1 to the outer hook process after timeout.
(sleep "$MAX_TIMEOUT_SEC" && kill -USR1 "$$") &
```

The subshell sleeps for the configured duration, then sends `USR1` to the hook process. If the agent finishes first, the hook kills the watchdog subshell as part of normal exit.

## Bash 3.2 Portability: `MY_PID` and `$$`

The comment in `agent-run.sh` is explicit:

> **Do not use `$BASHPID`.**

`$BASHPID` was introduced in bash 4.0 and reliably reports the current subshell's PID. In bash 3.2 (macOS default), `$$` always returns the PID of the *top-level shell*, not the current subshell. This means inside a subshell, `$$` gives the wrong PID for `kill_tree`.

The solution is to capture the PID via a child process that reports its parent:

```sh
MY_PID=$(sh -c 'echo $PPID')
```

This works in bash 3.2 because `$PPID` is a POSIX variable that always reflects the parent PID of the current process. See [[bash-3-2-portability]] for related constraints.

## Agent Working Directory Structure

Each agent run gets an isolated working directory under the project's `.state/` tree:

```
<project>/llake/.state/
  hooks.log                    # rolled audit log, one line per lifecycle event
  agents/<id>/
    agent.log                  # full stream-json output from claude -p, formatted
    triage.pid                 # (session-end only) PID of the triage agent phase
    capture.pid                # (session-end only) PID of the capture agent phase
    ingest.pid                 # (post-merge only) PID of the ingest agent phase
  sessions/<id>/
    transcript.md              # extracted session transcript
    transcript.md.lock         # lock marker (prevents double-capture)
```

`_agent_cleanup` removes the active `.pid` file but leaves everything else. The agent log and transcript are preserved so a developer can inspect what the agent had done up to the point it was killed.

## Key Points

- `setup_kill_trap` must be called before spawning any subprocess; if a signal arrives before the trap is installed, cleanup will not run.
- `_agent_cleanup` is re-entry safe: the first thing it does is blank all traps.
- The one-second `sleep 1` between `kill_tree` (SIGTERM) and `pkill -KILL` gives the Claude CLI time to flush its output before being force-killed.
- Exit code 143 is intentional — it signals to the parent process (Claude Code's hook runner) that the hook was terminated rather than completing successfully.
- The library never reads or writes config; it is purely a process-management utility.

## Code References

- `hooks/lib/agent-run.sh:1-77` — full implementation
- `hooks/lib/agent-run.sh:15-21` — `kill_tree` recursive walk
- `hooks/lib/agent-run.sh:29-69` — `_agent_cleanup` trap handler
- `hooks/lib/agent-run.sh:74-77` — `setup_kill_trap` signal registration

## See Also

- [[agent-id]] — generates the `LLAKE_AGENT_ID` referenced in hooks.log entries
- [[session-end-hook]] — sources this library; two-phase triage/capture agent
- [[post-merge-hook]] — sources this library; single-phase ingest agent
- [[bash-3-2-portability]] — why `$BASHPID` is forbidden and how to work around it
