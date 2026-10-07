#!/bin/bash
# Integration tests for hooks/post-merge.sh's v3 branch: gate rule, lock, user kill and watchdog revert.
# Uses tests/hooks/fixtures/claude-v3-stub.py as `claude`. Bash 3.2 portable.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

# shellcheck source=fixtures/mkproject.sh
source "$FIXTURES/mkproject.sh"

STUB_BIN=$(mktemp -d -t llake-v3-stub-bin.XXXXXX)
cp "$FIXTURES/claude-v3-stub.py" "$STUB_BIN/claude"
chmod +x "$STUB_BIN/claude"

PASS=0; FAIL=0; FAILED_NAMES=()
TMP_PROJECTS=()

cleanup() {
  rm -rf "$STUB_BIN"
  for d in "${TMP_PROJECTS[@]}"; do rm -rf "$d"; done
}
trap cleanup EXIT

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
       echo "  FAIL [$label]: expected '$expected', got '$actual'"; fi
}

assert_file_contains() {
  local label="$1" file="$2" needle="$3"
  if [ -f "$file" ] && grep -qF -- "$needle" "$file"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
       echo "  FAIL [$label]: $file does not contain '$needle'"; fi
}

assert_file_lacks() {
  local label="$1" file="$2" needle="$3"
  if [ -f "$file" ] && ! grep -qF -- "$needle" "$file"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
       echo "  FAIL [$label]: $file contains '$needle' (or is missing)"; fi
}

CLIENT_LINE="The client calls fetchUserData on start"
V3_BRIEF='[{"path": "llake/wiki/arch/client.md", "kind": "direct", "severity": "major", "themes": ["T1"], "reason": "renamed", "stale": [{"quote": "The client calls fetchUserData on start", "head": "src/app.py:1", "severity": "major"}]}]'

set_v3() {  # set_v3 <project> [JSON merged into ingest.v3]
  python3 - "$1/llake/config.json" "${2:-}" <<'PY'
import json, sys
path, extra = sys.argv[1], sys.argv[2]
c = json.load(open(path))
c["ingest"]["pipeline"] = "v3"
v3 = c["ingest"].setdefault("v3", {})
v3["recallPass"] = "off"
v3.update(json.loads(extra or "{}"))
json.dump(c, open(path, "w"), indent=2)
PY
}

seed_wiki() {
  local proj="$1"
  mkdir -p "$proj/llake/wiki/arch" "$proj/llake/.state"
  printf '%s\n' '---' 'title: "Client"' 'description: "The HTTP client"' 'tags: [arch]' 'created: 2026-01-01' \
    'updated: 2026-01-01' 'status: current' 'related: []' '---' '' '# Client' '' "$CLIENT_LINE." \
    > "$proj/llake/wiki/arch/client.md"
  printf '%s\n' '---' 'title: "Arch"' 'description: "Category index for arch."' 'tags: [arch]' \
    'created: 2026-01-01' 'updated: 2026-01-01' '---' '' '# Arch' '' '| Page | Description |' '|---|---|' \
    '| [[client]] | The HTTP client |' > "$proj/llake/wiki/arch/arch.md"
  printf '# LoreLake Activity Log\n' > "$proj/llake/log.md"
  printf 'llake/.state/\n' > "$proj/.gitignore"
  printf 'def fetchUserData():\n    return 1\n' > "$proj/src/app.py"
  git -C "$proj" add -A
  git -C "$proj" commit -q -m "seed wiki"
  git -C "$proj" rev-parse HEAD > "$proj/llake/last-ingest-sha"
}

rename_loader() {
  printf 'def loadProfile():\n    return 1\n' > "$1/src/app.py"
  git -C "$1" add -A
  git -C "$1" commit -q -m "rename loader"
}

owe_major_gap() {
  cat > "$1/llake/ingest-gaps.json" <<EOF
{"version": 1, "asOf": "x", "agent": "old", "date": "2026-10-01", "ranges": [],
 "gaps": [{"page": "wiki/arch/client.md", "severity": "major", "cause": "declared", "since": "s0",
           "attempts": 1, "stuck": false,
           "claims": [{"quote": "$CLIENT_LINE", "head": "src/app.py:1", "severity": "major", "source": "brief"}]}]}
EOF
}

set_schedule_wait() {  # batching on with thresholds a small range never reaches; clock seeded now
  python3 - "$1/llake/config.json" <<'PY'
import json, sys
p = sys.argv[1]
c = json.load(open(p))
c["ingest"]["schedule"] = {"enabled": True, "minChangedLines": 100000, "maxAgeHours": 1000}
json.dump(c, open(p, "w"), indent=2)
PY
  date +%s > "$1/llake/.state/last-ingest-at"
}

cursor() { tr -d '[:space:]' < "$1/llake/last-ingest-sha"; }

run_hook() {  # run_hook <project> [VAR=value ...]   (synchronous)
  local proj="$1"; shift
  (cd "$proj" && env PATH="$STUB_BIN:$PATH" LLAKE_POST_MERGE_SYNC=1 "$@" bash "$REPO_ROOT/hooks/post-merge.sh")
}

new_project() {  # new_project <v3|legacy> [v3 JSON]; prints the project dir
  local proj
  proj=$(mkproject "main")
  seed_wiki "$proj"
  [ "$1" = "v3" ] && set_v3 "$proj" "${2:-}"
  echo "$proj"
}

plugin_version() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$REPO_ROOT/.claude-plugin/plugin.json"
}

src_commit() {  # src_commit <project> <app.py content> <message>
  printf '%s\n' "$2" > "$1/src/app.py"
  git -C "$1" add -A
  git -C "$1" commit -q -m "$3"
}

test_v3_range_run() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  rename_loader "$proj"
  local head; head=$(git -C "$proj" rev-parse HEAD)
  run_hook "$proj" V3_STUB_BRIEF="$V3_BRIEF"
  assert_eq "range_cursor_advanced" "$head" "$(cursor "$proj")"
  assert_file_contains "range_page_written" "$proj/llake/wiki/arch/client.md" "the corrected statement"
  assert_file_contains "range_log_entry" "$proj/llake/log.md" ": v3 — 1 updated"
  assert_file_contains "range_gap_record" "$proj/llake/ingest-gaps.json" '"gaps": []'
  assert_file_contains "range_spawned" "$proj/llake/.state/hooks.log" "done: spawned v3 agent"
  assert_file_contains "range_completed" "$proj/llake/.state/hooks.log" "completed: agent"
  assert_eq "range_lock_released" "no" "$([ -d "$proj/llake/.state/post-merge.lock.d" ] && echo yes || echo no)"
}

# A live run's lock must never read as stale, however old the lock dir is: the hook that took it
# exits at once (owner.pid must name the run's own subshell, which lives for the whole run).
test_v3_live_lock_not_reclaimed_when_aged() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  rename_loader "$proj"
  local lockdir="$proj/llake/.state/post-merge.lock.d"
  local mark="$proj/writer-started"
  (cd "$proj" && env PATH="$STUB_BIN:$PATH" V3_STUB_BRIEF="$V3_BRIEF" V3_STUB_SLEEP="writer-b01:60" \
     V3_STUB_MARK="$mark" bash "$REPO_ROOT/hooks/post-merge.sh")
  local i=0
  while [ ! -f "$mark" ] && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i+1)); done
  assert_eq "aged_writer_started" "yes" "$([ -f "$mark" ] && echo yes || echo no)"
  local pidfile; pidfile=$(ls "$proj"/llake/.state/agents/*/orchestrator.pid 2>/dev/null | head -1)
  local pid; pid=$(cat "$pidfile" 2>/dev/null)
  assert_eq "aged_owner_is_run" "$pid" "$(cat "$lockdir/owner.pid" 2>/dev/null)"
  # Age the lock dir two hours, past post-merge-lock.sh's one-hour stale threshold.
  python3 -c 'import os, sys, time; t = time.time() - 7200; os.utime(sys.argv[1], (t, t))' "$lockdir"
  add_src_commit "$proj" "another change"
  run_hook "$proj"
  assert_file_contains "aged_second_abandoned" "$proj/llake/.state/hooks.log" "v3 agent abandoned"
  assert_file_lacks "aged_not_reclaimed" "$proj/llake/.state/hooks.log" "reclaiming stale lock"
  assert_eq "aged_lock_kept" "$pid" "$(cat "$lockdir/owner.pid" 2>/dev/null)"
  kill -TERM "$pid" 2>/dev/null
  i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i+1)); done
  assert_eq "aged_lock_released_on_kill" "no" "$([ -d "$lockdir" ] && echo yes || echo no)"
}

test_v3_empty_with_owed_major_runs_gap_only() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  owe_major_gap "$proj"
  add_nonsrc_commit "$proj"
  local head; head=$(git -C "$proj" rev-parse HEAD)
  run_hook "$proj"
  assert_eq "gaponly_cursor_advanced" "$head" "$(cursor "$proj")"
  assert_file_contains "gaponly_gate" "$proj/llake/.state/hooks.log" "reason=gaps-only"
  assert_file_contains "gaponly_log" "$proj/llake/log.md" "gap-only at"
  assert_file_contains "gaponly_page" "$proj/llake/wiki/arch/client.md" "the corrected statement"
}

test_v3_empty_without_owed_advances_without_agent() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  add_nonsrc_commit "$proj"
  local head; head=$(git -C "$proj" rev-parse HEAD)
  run_hook "$proj"
  assert_eq "empty_cursor_advanced" "$head" "$(cursor "$proj")"
  assert_file_contains "empty_skipped" "$proj/llake/.state/hooks.log" "skipped: no relevant file changes"
  assert_eq "empty_no_agent_dir" "0" "$(ls "$proj/llake/.state/agents" 2>/dev/null | wc -l | tr -d ' ')"
}

test_v3_wait_with_owed_major_runs() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  set_schedule_wait "$proj"
  owe_major_gap "$proj"
  rename_loader "$proj"
  run_hook "$proj" V3_STUB_BRIEF="$V3_BRIEF"
  assert_file_contains "wait_owed_reason" "$proj/llake/.state/hooks.log" "reason=gaps"
  assert_file_contains "wait_owed_spawned" "$proj/llake/.state/hooks.log" "done: spawned v3 agent"
}

test_v3_wait_without_owed_defers() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  set_schedule_wait "$proj"
  rename_loader "$proj"
  local before; before=$(cursor "$proj")
  run_hook "$proj"
  assert_file_contains "wait_deferred" "$proj/llake/.state/hooks.log" "deferred:"
  assert_file_lacks "wait_not_spawned" "$proj/llake/.state/hooks.log" "spawned v3 agent"
  assert_eq "wait_cursor_held" "$before" "$(cursor "$proj")"
}

test_legacy_ignores_gap_record() {
  local proj; proj=$(new_project legacy); TMP_PROJECTS+=("$proj")
  set_schedule_wait "$proj"
  owe_major_gap "$proj"
  rename_loader "$proj"
  run_hook "$proj"
  assert_file_contains "legacy_still_defers" "$proj/llake/.state/hooks.log" "deferred:"
  assert_file_lacks "legacy_no_gaps_reason" "$proj/llake/.state/hooks.log" "reason=gaps"
}

test_v3_lock_held_abandons() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  rename_loader "$proj"
  mkdir -p "$proj/llake/.state/post-merge.lock.d"
  echo $$ > "$proj/llake/.state/post-merge.lock.d/owner.pid"
  local before; before=$(cursor "$proj")
  run_hook "$proj" V3_STUB_BRIEF="$V3_BRIEF"
  assert_file_contains "lock_abandoned" "$proj/llake/.state/hooks.log" "v3 agent abandoned"
  assert_eq "lock_cursor_held" "$before" "$(cursor "$proj")"
}

test_v3_user_kill_reverts_and_holds() {
  local proj; proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  rename_loader "$proj"
  local before; before=$(cursor "$proj")
  local mark="$proj/writer-started"
  (cd "$proj" && env PATH="$STUB_BIN:$PATH" V3_STUB_BRIEF="$V3_BRIEF" V3_STUB_SLEEP="writer-b01:60" \
     V3_STUB_MARK="$mark" bash "$REPO_ROOT/hooks/post-merge.sh")
  local i=0
  while [ ! -f "$mark" ] && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i+1)); done
  assert_eq "kill_writer_started" "yes" "$([ -f "$mark" ] && echo yes || echo no)"
  local pidfile; pidfile=$(ls "$proj"/llake/.state/agents/*/orchestrator.pid 2>/dev/null | head -1)
  local pid; pid=$(cat "$pidfile" 2>/dev/null)
  kill -TERM "$pid" 2>/dev/null
  i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 150 ]; do sleep 0.2; i=$((i+1)); done
  assert_file_contains "kill_page_reverted" "$proj/llake/wiki/arch/client.md" "$CLIENT_LINE"
  assert_file_lacks "kill_no_partial_edit" "$proj/llake/wiki/arch/client.md" "the corrected statement"
  assert_eq "kill_cursor_held" "$before" "$(cursor "$proj")"
  assert_file_contains "kill_logged" "$proj/llake/.state/hooks.log" "terminated: agent"
  assert_eq "kill_lock_released" "no" "$([ -d "$proj/llake/.state/post-merge.lock.d" ] && echo yes || echo no)"
  assert_eq "kill_journal_aborted" "True" \
    "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["aborted"])' "$(dirname "$pidfile")/run.json")"
}

test_v3_watchdog_reverts_and_holds() {
  local proj; proj=$(new_project v3 '{"timeoutSeconds": 4}'); TMP_PROJECTS+=("$proj")
  rename_loader "$proj"
  local before; before=$(cursor "$proj")
  run_hook "$proj" V3_STUB_BRIEF="$V3_BRIEF" V3_STUB_SLEEP="writer-b01:60" V3_STUB_IGNORE_TERM=1 \
    LLAKE_V3_WATCHDOG_GRACE_SECONDS=2
  assert_file_contains "watchdog_logged" "$proj/llake/.state/hooks.log" "timeout: agent"
  assert_file_contains "watchdog_page_reverted" "$proj/llake/wiki/arch/client.md" "$CLIENT_LINE"
  assert_eq "watchdog_cursor_held" "$before" "$(cursor "$proj")"
}

# A TERM right after acquire_post_merge_lock (owner.pid still the hook's $$, not yet the run subshell)
# must still release the lock; a lock another process owns is never removed.
test_v3_release_covers_the_acquire_to_claim_gap() {
  local dir; dir=$(mktemp -d -t llake-v3-lock.XXXXXX); TMP_PROJECTS+=("$dir")
  (
    STATE_DIR="$dir"; LOG_FILE="$dir/hooks.log"; HOOK_NAME="post-merge"
    source "$REPO_ROOT/hooks/lib/post-merge-lock.sh"
    source "$REPO_ROOT/hooks/lib/ingest-v3.sh"
    MY_PID=$(sh -c 'echo $PPID')
    acquire_post_merge_lock
    release_v3_lock
  )
  assert_eq "gap_release_removes_own_lock" "no" "$([ -d "$dir/post-merge.lock.d" ] && echo yes || echo no)"
  mkdir -p "$dir/post-merge.lock.d"
  echo 1 > "$dir/post-merge.lock.d/owner.pid"
  (
    STATE_DIR="$dir"; LOG_FILE="$dir/hooks.log"; HOOK_NAME="post-merge"
    source "$REPO_ROOT/hooks/lib/post-merge-lock.sh"
    source "$REPO_ROOT/hooks/lib/ingest-v3.sh"
    MY_PID=$(sh -c 'echo $PPID')
    release_v3_lock
  )
  assert_eq "gap_release_keeps_foreign_lock" "yes" "$([ -d "$dir/post-merge.lock.d" ] && echo yes || echo no)"
}

# The v3 kill traps are installed before the lock is taken (as v2's EXIT release is armed right after),
# so no TERM between taking the lock and arming the traps can strand it for the stale-lock reclaim.
test_v3_kill_traps_armed_before_the_lock() {
  local block trap_line lock_line
  block=$(awk '/^if \[ "\$USE_INGEST_V3" = "1" \]; then/,/^fi$/' "$REPO_ROOT/hooks/post-merge.sh")
  trap_line=$(printf '%s\n' "$block" | grep -n "trap '_ingest_v3_on_kill user' TERM INT" | head -1 | cut -d: -f1)
  lock_line=$(printf '%s\n' "$block" | grep -n "acquire_post_merge_lock" | head -1 | cut -d: -f1)
  assert_eq "v3_block_found" "yes" "$([ -n "$trap_line" ] && [ -n "$lock_line" ] && echo yes || echo no)"
  assert_eq "v3_traps_before_lock" "yes" "$([ "${trap_line:-0}" -lt "${lock_line:-0}" ] && echo yes || echo no)"
}

# LOR-25: a finalized split leaves the rest of the range to a second run in the same invocation.
test_v3_split_continues_with_the_remainder() {
  local proj base head
  proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  base=$(cursor "$proj")
  src_commit "$proj" 'def loadProfile():' "c1"
  src_commit "$proj" 'def loadProfile(): pass' "c2"
  src_commit "$proj" 'def loadProfile(): return 1' "c3"
  src_commit "$proj" 'def loadProfile(): return 2' "c4"
  head=$(git -C "$proj" rev-parse HEAD)
  printf '{"base": "%s", "count": 2, "lastHead": "%s", "plugin": "%s"}\n' "$base" "$head" "$(plugin_version)" \
    > "$proj/llake/.state/ingest-failures.json"
  run_hook "$proj" V3_STUB_BRIEF="$V3_BRIEF"
  assert_eq "continue_cursor_at_head" "$head" "$(cursor "$proj")"
  assert_file_contains "continue_split_completed" "$proj/llake/.state/hooks.log" "v3 split"
  assert_file_contains "continue_logged" "$proj/llake/.state/hooks.log" "takes the rest of the range"
  assert_file_contains "continue_range_completed" "$proj/llake/.state/hooks.log" "v3 range"
  assert_eq "continue_two_agent_dirs" "2" "$(ls "$proj/llake/.state/agents" | wc -l | tr -d ' ')"
  assert_eq "continue_lock_released" "no" "$([ -d "$proj/llake/.state/post-merge.lock.d" ] && echo yes || echo no)"
}

# The kill trap and _agent_cleanup read these globals: a continuation must repoint all of them.
test_v3_next_agent_repoints_trap_globals() {
  local out
  out=$(
    tmp=$(mktemp -d -t llake-v3-next.XXXXXX)
    generate_agent_id() { echo "next-agent-1"; }
    AGENTS_DIR="$tmp"; MY_PID=4242
    # shellcheck source=../../hooks/lib/ingest-v3.sh
    source "$REPO_ROOT/hooks/lib/ingest-v3.sh"
    V3_PY_PID=999  # the previous run's (exited, maybe reused) python PID
    next_v3_agent
    echo "$V3_AGENT_ID|$V3_AGENT_DIR|$V3_AGENT_LOG|$AGENT_LOG|$CURRENT_PID_FILE|$LLAKE_AGENT_ID|$(cat "$V3_PID_FILE")|py=${V3_PY_PID:-}|$tmp"
    rm -rf "$tmp"
  )
  local tmp="${out##*|}"
  assert_eq "next_agent_globals" \
    "next-agent-1|$tmp/next-agent-1|$tmp/next-agent-1/agent.log|$tmp/next-agent-1/agent.log|$tmp/next-agent-1/orchestrator.pid|next-agent-1|4242|py=|$tmp" \
    "$out"
}

test_v3_range_run
test_v3_empty_with_owed_major_runs_gap_only
test_v3_empty_without_owed_advances_without_agent
test_v3_wait_with_owed_major_runs
test_v3_wait_without_owed_defers
test_legacy_ignores_gap_record
test_v3_lock_held_abandons
test_v3_user_kill_reverts_and_holds
test_v3_watchdog_reverts_and_holds
test_v3_live_lock_not_reclaimed_when_aged
test_v3_release_covers_the_acquire_to_claim_gap
test_v3_kill_traps_armed_before_the_lock
test_v3_split_continues_with_the_remainder
test_v3_next_agent_repoints_trap_globals

echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "FAILED: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
