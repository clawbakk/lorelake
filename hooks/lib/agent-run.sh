#!/bin/bash
# LoreLake agent runner — shared kill-handling helpers for hooks that spawn
# background Claude CLI agents. Sourced by session-end.sh and post-merge.sh.
#
# Functions defined:
#   kill_tree <root_pid>       — recursively SIGTERM a process tree, bottom-up
#   _agent_cleanup <reason>    — trap handler; expects MY_PID, CURRENT_PID_FILE,
#                                AGENT_LOG, MAX_TIMEOUT_SEC in the caller's scope
#   setup_kill_trap            — registers TERM/INT → user, USR1 → timeout
#
# Bash portability: targets macOS /bin/bash 3.2. Do not use $BASHPID.

# Recursively walk a process tree, SIGTERM each node bottom-up.
# Skips $MY_PID (the caller's own PID) to avoid self-termination.
kill_tree() {
  local root=$1
  for child in $(pgrep -P "$root" 2>/dev/null); do
    kill_tree "$child"
  done
  [ "$root" != "$MY_PID" ] && kill -TERM "$root" 2>/dev/null
}

# Trap handler. Invoked by setup_kill_trap on TERM/INT (user) or USR1 (timeout).
# Expects these vars to be set by the caller's subshell scope:
#   MY_PID            — outer subshell PID (from `sh -c 'echo $PPID'`)
#   CURRENT_PID_FILE  — path to the pid file representing the active phase
#   AGENT_LOG         — path to agent.log (where the kill marker is appended)
#   MAX_TIMEOUT_SEC   — numeric seconds, used for timeout log line
_agent_cleanup() {
  local reason=$1
  # Prevent re-entry: ignore further signals during cleanup.
  trap '' TERM INT USR1 EXIT

  # Kill every descendant of the outer subshell.
  kill_tree "$MY_PID"
  sleep 1
  pkill -KILL -P "$MY_PID" 2>/dev/null

  # Figure out which phase was active from the pid file's basename.
  local phase=""
  [ -n "$CURRENT_PID_FILE" ] && phase=$(basename "$CURRENT_PID_FILE" .pid)

  # Delete only the pid file. Leave AGENT_LOG, AGENT_DIR, SESSION_DIR intact
  # for post-mortem inspection.
  [ -n "$CURRENT_PID_FILE" ] && rm -f "$CURRENT_PID_FILE"

  # Append a marker to the agent log explaining why we died.
  if [ -n "$AGENT_LOG" ]; then
    echo "" >> "$AGENT_LOG"
    if [ "$reason" = "timeout" ]; then
      echo "=== TIMEOUT during ${phase:-unknown} (session exceeded ${MAX_TIMEOUT_SEC}s) at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    else
      echo "=== KILLED by user during ${phase:-unknown} at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    fi
  fi

  # Also append a one-line record to hooks.log if the caller provided HOOKS_LOG_FILE.
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

# Install both traps.
#   TERM/INT → user-initiated (extension button, Ctrl+C)
#   USR1     → watchdog timeout (self-sent by the hook's own watchdog subshell)
setup_kill_trap() {
  trap '_agent_cleanup user' TERM INT
  trap '_agent_cleanup timeout' USR1
}
