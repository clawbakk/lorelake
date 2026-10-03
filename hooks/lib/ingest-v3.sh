#!/bin/bash
# LoreLake ingest v3 — bash glue, sourced by hooks/post-merge.sh when ingest.pipeline == "v3".
# The pipeline itself is Python: hooks/lib/ingest_v3/, entered through hooks/lib/ingest-v3.py.
#
# From the caller's scope: PROJECT_ROOT LIB_DIR LAST_SHA CURRENT_SHA V3_AGENT_DIR, and inside the
# spawned subshell agent-run.sh's contract: MY_PID AGENT_LOG HOOKS_LOG_FILE LLAKE_AGENT_ID
# CURRENT_PID_FILE MAX_TIMEOUT_SEC.
#
# Bash 3.2 portable: no $BASHPID, no wait -n, no associative arrays.

# Hard watchdog grace after the run deadline. The deadline itself is soft and handled in Python
# (in-flight writers reverted, the rest recorded as `timeout` gaps, then finalize); the watchdog only
# catches a hung process. Env override exists for tests.
LLAKE_V3_WATCHDOG_GRACE_SECONDS="${LLAKE_V3_WATCHDOG_GRACE_SECONDS:-300}"

# Run one v3 ingest. Python runs in the background and we `wait`, so a TERM/INT/USR1 trap fires at
# once instead of after the Python process exits.
run_ingest_v3() {
  local agent_id="$1" agent_dir="$2" agent_log="$3" timeout_sec="$4"
  local deadline py_pid
  deadline=$(( $(date +%s) + timeout_sec ))
  {
    echo "=== LoreLake Ingest v3 Agent: $agent_id ==="
    echo "Commits: ${LAST_SHA}..${CURRENT_SHA}"
    echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Deadline: ${timeout_sec}s (watchdog +${LLAKE_V3_WATCHDOG_GRACE_SECONDS}s)"
    echo "---"
  } > "$agent_log"
  IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$agent_id" \
    python3 "$LIB_DIR/ingest-v3.py" run --project-root "$PROJECT_ROOT" --agent-id "$agent_id" \
      --agent-dir "$agent_dir" --deadline "$deadline" >> "$agent_log" 2>&1 &
  py_pid=$!
  wait "$py_pid"
}

# Kill trap (TERM/INT = user, USR1 = watchdog): stop every process of this run, undo its writes under
# llake/ (never outside it), release the post-merge lock (_agent_cleanup clears the EXIT trap that
# would), then log the kill and exit 143. A failing revert-run is logged and never stops the trap:
# the lock is released and _agent_cleanup runs regardless (the next run's recovery retries the revert).
# `set +e` first: kill_tree and pkill return non-zero in normal use, which would end the trap early
# if a caller ever ran with errexit on (the subshell exits at the end of this trap anyway).
_ingest_v3_on_kill() {
  set +e
  local reason="$1" revert_exit=0
  trap '' TERM INT USR1
  kill_tree "$MY_PID"
  sleep 1
  pkill -KILL -P "$MY_PID" 2>/dev/null
  python3 "$LIB_DIR/ingest-v3.py" revert-run --project-root "$PROJECT_ROOT" \
    --agent-dir "$V3_AGENT_DIR" >> "$AGENT_LOG" 2>&1 || revert_exit=$?
  if [ "$revert_exit" -ne 0 ]; then
    echo "=== revert-run failed (exit $revert_exit) — run journal left for the next run's recovery ===" \
      >> "$AGENT_LOG"
  fi
  release_post_merge_lock
  _agent_cleanup "$reason"
}
