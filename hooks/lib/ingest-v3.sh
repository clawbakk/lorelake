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

# On a kill the Python run gets this long after its own SIGTERM to stop its agents and revert before the
# tree kill. Its agents run in their own session: once Python is gone, a tree kill can no longer find them.
LLAKE_V3_KILL_GRACE_SECONDS="${LLAKE_V3_KILL_GRACE_SECONDS:-20}"

# Run one v3 ingest. Python runs in the background and we `wait`, so a TERM/INT/USR1 trap fires at
# once instead of after the Python process exits.
run_ingest_v3() {
  local agent_id="$1" agent_dir="$2" agent_log="$3" timeout_sec="$4"
  local deadline
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
  V3_PY_PID=$!
  wait "$V3_PY_PID"
}

# `ingest-v3.py run` exit code for "a split or skip finalized short of HEAD" (run.EXIT_CONTINUE).
V3_EXIT_CONTINUE=3

# One v3 run under the hard watchdog, inside post-merge.sh's run subshell, for the current agent globals
# (V3_AGENT_ID V3_AGENT_DIR V3_AGENT_LOG V3_PID_FILE). Returns the run's exit code.
run_ingest_v3_watched() {
  local rc=0
  (
    sleep "$V3_WATCHDOG"
    if kill -0 "$MY_PID" 2>/dev/null; then kill -USR1 "$MY_PID" 2>/dev/null; fi
  ) &
  WATCHDOG_PID=$!
  run_ingest_v3 "$V3_AGENT_ID" "$V3_AGENT_DIR" "$V3_AGENT_LOG" "$V3_TIMEOUT" || rc=$?
  kill_tree "$WATCHDOG_PID"
  wait "$WATCHDOG_PID" 2>/dev/null
  rm -f "$V3_PID_FILE"
  return "$rc"
}

# Point the run subshell at a fresh agent for the continuation run: new ID, dir, log and pid file, plus the
# agent-run.sh globals the kill traps read (AGENT_LOG, CURRENT_PID_FILE, LLAKE_AGENT_ID), so a kill reverts
# and logs the continuation, not the finished run before it.
next_v3_agent() {
  V3_AGENT_ID=$(generate_agent_id)
  V3_AGENT_DIR="$AGENTS_DIR/$V3_AGENT_ID"
  mkdir -p "$V3_AGENT_DIR"
  V3_AGENT_LOG="$V3_AGENT_DIR/agent.log"
  V3_PID_FILE="$V3_AGENT_DIR/orchestrator.pid"
  echo "$MY_PID" > "$V3_PID_FILE"
  AGENT_LOG="$V3_AGENT_LOG"
  CURRENT_PID_FILE="$V3_PID_FILE"
  LLAKE_AGENT_ID="$V3_AGENT_ID"
}

# The post-merge lock under v3. acquire_post_merge_lock records `$$`, which inside the `( … ) &` run
# subshell is the hook's PID — and the hook exits at once, so the lock would name a dead owner and turn
# reclaimable as stale an hour in, while the run (deadline up to ingest.v3.timeoutSeconds + grace) is
# still working. claim_v3_lock re-records the owner as the run subshell itself ($MY_PID), live for the
# whole run; release_v3_lock removes the lock only while this run holds it: owner $MY_PID, or the hook's
# $$ that acquire_post_merge_lock wrote just before claim_v3_lock (a kill can land in between). $$ names
# this hook invocation only; any other holder's lock is left alone.
claim_v3_lock() {
  echo "$MY_PID" > "$(_llake_lock_dir)/owner.pid"
}

release_v3_lock() {
  local lockdir owner
  lockdir=$(_llake_lock_dir)
  [ -f "$lockdir/owner.pid" ] || return 0
  owner=$(cat "$lockdir/owner.pid" 2>/dev/null)
  if [ -n "$owner" ] && { [ "$owner" = "$MY_PID" ] || [ "$owner" = "$$" ]; }; then
    rm -rf "$lockdir"
  fi
}

# Kill trap (TERM/INT = user, USR1 = watchdog): SIGTERM the Python run first and give it
# LLAKE_V3_KILL_GRACE_SECONDS to kill its agents and revert (it raises on the signal), then stop every
# process left in this run's tree, undo its writes under llake/ (never outside it; a no-op when Python
# already reverted), release the run's post-merge lock (_agent_cleanup clears the EXIT trap that
# would), then log the kill and exit 143. A failing revert-run is logged and never stops the trap:
# the lock is released and _agent_cleanup runs regardless (the next run's recovery retries the revert).
# `set +e` first: kill_tree and pkill return non-zero in normal use, which would end the trap early
# if a caller ever ran with errexit on (the subshell exits at the end of this trap anyway).
_ingest_v3_on_kill() {
  set +e
  local reason="$1" revert_exit=0 ticks=0
  trap '' TERM INT USR1
  if [ -n "${V3_PY_PID:-}" ] && kill -0 "$V3_PY_PID" 2>/dev/null; then
    kill -TERM "$V3_PY_PID" 2>/dev/null
    while kill -0 "$V3_PY_PID" 2>/dev/null && [ "$ticks" -lt $((LLAKE_V3_KILL_GRACE_SECONDS * 5)) ]; do
      sleep 0.2
      ticks=$((ticks + 1))
    done
  fi
  kill_tree "$MY_PID"
  sleep 1
  pkill -KILL -P "$MY_PID" 2>/dev/null
  python3 "$LIB_DIR/ingest-v3.py" revert-run --project-root "$PROJECT_ROOT" \
    --agent-dir "$V3_AGENT_DIR" >> "$AGENT_LOG" 2>&1 || revert_exit=$?
  if [ "$revert_exit" -ne 0 ]; then
    echo "=== revert-run failed (exit $revert_exit) — run journal left for the next run's recovery ===" \
      >> "$AGENT_LOG"
  fi
  release_v3_lock
  _agent_cleanup "$reason"
}
