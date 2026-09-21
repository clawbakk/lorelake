#!/bin/bash
# Integration tests for the ingest batching gate in hooks/post-merge.sh.
# Uses a claude stub (PATH injection) and LLAKE_POST_MERGE_SYNC=1.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

# shellcheck source=fixtures/mkproject.sh
source "$FIXTURES/mkproject.sh"

STUB_BIN=$(mktemp -d -t llake-stub-bin.XXXXXX)
cp "$FIXTURES/claude-stub.sh" "$STUB_BIN/claude"
chmod +x "$STUB_BIN/claude"

PASS=0
FAIL=0
FAILED_NAMES=()

cleanup() { rm -rf "$STUB_BIN"; }
trap cleanup EXIT

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
    echo "  FAIL [$label]: expected '$expected', got '$actual'"
  fi
}

assert_log_grep() {
  local label="$1" log="$2" pattern="$3"
  if [ -f "$log" ] && grep -q "$pattern" "$log"; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
    echo "  FAIL [$label]: log '$log' does not match '$pattern'"
    [ -f "$log" ] && sed 's/^/    /' "$log"
  fi
}

assert_log_no_grep() {
  local label="$1" log="$2" pattern="$3"
  if [ ! -f "$log" ] || ! grep -q "$pattern" "$log"; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
    echo "  FAIL [$label]: log '$log' unexpectedly matches '$pattern'"
    sed 's/^/    /' "$log"
  fi
}

run_post_merge() {
  local project="$1"; shift
  (
    cd "$project" || exit 1
    # `env` (not a literal assignment-word prefix) is required here: extra
    # KEY=VALUE args arrive via "$@" parameter expansion, and bash only
    # recognizes assignment-word prefixes written literally at parse time,
    # not ones produced by expansion.
    PATH="$STUB_BIN:$PATH" env LLAKE_POST_MERGE_SYNC=1 "$@" \
      bash "$REPO_ROOT/hooks/post-merge.sh"
  )
}

# Patch ingest.schedule keys into a project's config.json.
set_schedule() {
  local proj="$1" enabled="$2" min_lines="$3" max_age="$4"
  python3 - "$proj/llake/config.json" "$enabled" "$min_lines" "$max_age" <<'PY'
import json, sys
path, enabled, min_lines, max_age = sys.argv[1:5]
with open(path) as f:
    c = json.load(f)
c.setdefault("ingest", {})["schedule"] = {
    "enabled": enabled == "true",
    "minChangedLines": int(min_lines),
    "maxAgeHours": float(max_age),
}
with open(path, "w") as f:
    json.dump(c, f, indent=2)
PY
}

seed_timestamp() {
  local proj="$1" seconds_ago="${2:-0}"
  mkdir -p "$proj/llake/.state"
  echo $(( $(date +%s) - seconds_ago )) > "$proj/llake/.state/last-ingest-at"
}

# --- Test 1: below both arms → deferred, no spawn, cursor HELD ---
test_below_thresholds_defers() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" true 100000 24
  seed_timestamp "$proj" 0
  add_src_commit "$proj"
  local before; before=$(cat "$proj/llake/last-ingest-sha")

  run_post_merge "$proj" >/dev/null 2>&1

  assert_log_grep "defer:log" "$proj/llake/.state/hooks.log" "deferred:"
  assert_log_no_grep "defer:no-spawn" "$proj/llake/.state/hooks.log" "spawned agent"
  assert_eq "defer:cursor-held" "$before" "$(cat "$proj/llake/last-ingest-sha")"
  rm -rf "$proj"
}

# --- Test 2: lines arm trips → spawns ---
test_lines_arm_spawns() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" true 1 24
  seed_timestamp "$proj" 0
  add_src_commit "$proj"

  run_post_merge "$proj" >/dev/null 2>&1

  assert_log_grep "lines:spawn" "$proj/llake/.state/hooks.log" "spawned agent"
  rm -rf "$proj"
}

# --- Test 3: age arm trips → spawns despite a tiny pile ---
test_age_arm_spawns() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" true 100000 24
  seed_timestamp "$proj" 108000   # 30h ago
  add_src_commit "$proj"

  run_post_merge "$proj" >/dev/null 2>&1

  assert_log_grep "age:spawn" "$proj/llake/.state/hooks.log" "spawned agent"
  rm -rf "$proj"
}

# --- Test 4: empty pile → cursor AND timestamp advance, no spawn ---
test_empty_pile_advances_cursor_and_clock() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" true 100000 24
  seed_timestamp "$proj" 0
  add_nonsrc_commit "$proj"
  local head; head=$(cd "$proj" && git rev-parse HEAD)

  run_post_merge "$proj" >/dev/null 2>&1

  assert_log_grep "empty:log" "$proj/llake/.state/hooks.log" "no relevant file changes"
  assert_log_no_grep "empty:no-spawn" "$proj/llake/.state/hooks.log" "spawned agent"
  assert_eq "empty:cursor-advanced" "$head" "$(cat "$proj/llake/last-ingest-sha")"
  if [ -f "$proj/llake/.state/last-ingest-at" ]; then
    assert_eq "empty:clock-written" "yes" "yes"
  else
    assert_eq "empty:clock-written" "yes" "no"
  fi
  rm -rf "$proj"
}

# --- Test 5: LLAKE_IGNORE_SCHEDULE forces a run past a WAIT ---
test_env_override_forces_spawn() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" true 100000 24
  seed_timestamp "$proj" 0
  add_src_commit "$proj"

  run_post_merge "$proj" LLAKE_IGNORE_SCHEDULE=1 >/dev/null 2>&1

  assert_log_grep "forced:spawn" "$proj/llake/.state/hooks.log" "spawned agent"
  rm -rf "$proj"
}

# --- Test 6: schedule disabled → spawns on a tiny pile ---
test_schedule_disabled_spawns() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" false 100000 24
  seed_timestamp "$proj" 0
  add_src_commit "$proj"

  run_post_merge "$proj" >/dev/null 2>&1

  assert_log_grep "disabled:spawn" "$proj/llake/.state/hooks.log" "spawned agent"
  rm -rf "$proj"
}

# --- Test 7: schedule disabled still skips an empty pile ---
test_schedule_disabled_still_skips_empty() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" false 100000 24
  seed_timestamp "$proj" 0
  add_nonsrc_commit "$proj"

  run_post_merge "$proj" >/dev/null 2>&1

  assert_log_grep "disabled-empty:log" "$proj/llake/.state/hooks.log" "no relevant file changes"
  assert_log_no_grep "disabled-empty:no-spawn" "$proj/llake/.state/hooks.log" "spawned agent"
  rm -rf "$proj"
}

# --- Test 8: a fresh clone (no clock) seeds one and defers, rather than force-running ---
test_missing_clock_seeds_and_defers() {
  local proj; proj=$(mkproject "main")
  set_schedule "$proj" true 100000 24
  rm -f "$proj/llake/.state/last-ingest-at"
  add_src_commit "$proj"

  run_post_merge "$proj" >/dev/null 2>&1

  assert_log_grep "seed:deferred" "$proj/llake/.state/hooks.log" "deferred:"
  if [ -f "$proj/llake/.state/last-ingest-at" ]; then
    assert_eq "seed:clock-created" "yes" "yes"
  else
    assert_eq "seed:clock-created" "yes" "no"
  fi
  rm -rf "$proj"
}

test_below_thresholds_defers
test_lines_arm_spawns
test_age_arm_spawns
test_empty_pile_advances_cursor_and_clock
test_env_override_forces_spawn
test_schedule_disabled_spawns
test_schedule_disabled_still_skips_empty
test_missing_clock_seeds_and_defers

echo ""
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed: ${FAILED_NAMES[*]}"
  exit 1
fi
