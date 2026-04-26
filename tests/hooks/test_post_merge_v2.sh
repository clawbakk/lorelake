#!/bin/bash
# Integration tests for hooks/post-merge.sh's v2 pipeline branch.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

# shellcheck source=fixtures/mkproject.sh
source "$FIXTURES/mkproject.sh"

STUB_BIN=$(mktemp -d -t llake-stub-bin.XXXXXX)
cp "$FIXTURES/claude-stub.sh" "$STUB_BIN/claude"
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
  if [ -f "$file" ] && grep -qF "$needle" "$file"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
       echo "  FAIL [$label]: $file does not contain '$needle'"; fi
}

set_pipeline_v2() {
  local proj="$1"
  python3 -c "
import json
p = '$proj/llake/config.json'
c = json.load(open(p))
c.setdefault('ingest', {})['pipeline'] = 'v2'
c['ingest'].setdefault('v2', {'plannerModel':'opus','plannerEffort':'high','plannerBudgetUsd':5.0,'plannerAllowedTools':['Read','Glob','Grep'],'fixerModel':'opus','fixerEffort':'medium','fixerBudgetUsd':2.0,'fixerAllowedTools':['Read','Glob','Grep'],'maxFixerRetries':0,'diffChunkBytes':2000,'timeoutSeconds':30})
json.dump(c, open(p, 'w'), indent=2)
"
}

# Test 1: clean v2 ingest happy path
test_v2_clean_run() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  set_pipeline_v2 "$proj"
  cd "$proj"
  add_src_commit "$proj" "tweak"
  local CURRENT_SHA; CURRENT_SHA=$(git -C "$proj" rev-parse HEAD)
  local PLAN_INLINE
  PLAN_INLINE='{"version":"1","skip_reason":null,"summary":"trivial","updates":[],"creates":[],"deletes":[],"bidirectional_links":[],"log_entry":{"operation":"ingest","commit_range":"r","summary":"t","pages_affected":[]}}'
  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_PLAN_INLINE="$PLAN_INLINE" \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"
  local rc=$?
  assert_eq "v2_clean_exit" "0" "$rc"
  local sha_now; sha_now=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "v2_cursor_advanced" "$CURRENT_SHA" "$sha_now"
  cd "$REPO_ROOT"
}

# Test 2: legacy regression — pipeline unset still uses old path
test_legacy_regression() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  cd "$proj"
  add_src_commit "$proj" "x"
  PATH="$STUB_BIN:$PATH" LLAKE_POST_MERGE_SYNC=1 bash "$REPO_ROOT/hooks/post-merge.sh"
  assert_file_contains "legacy_log_present" "$proj/llake/.state/hooks.log" "post-merge"
  cd "$REPO_ROOT"
}

# Test 3: schema-invalid plan holds the cursor
test_v2_schema_invalid_holds_cursor() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  set_pipeline_v2 "$proj"
  cd "$proj"
  add_src_commit "$proj" "x"
  local INITIAL_SHA; INITIAL_SHA=$(cat "$proj/llake/last-ingest-sha")
  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_PLAN_INLINE='{"bad":"plan"}' \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"
  local sha_now; sha_now=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "schema_invalid_cursor_held" "$INITIAL_SHA" "$sha_now"
  cd "$REPO_ROOT"
}

# Test 4: planner timeout holds the cursor (watchdog kill-trap path)
test_planner_timeout_holds_cursor() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  set_pipeline_v2 "$proj"
  python3 -c "
import json
p = '$proj/llake/config.json'
c = json.load(open(p))
c['ingest'].setdefault('v2', {})['timeoutSeconds'] = 1
json.dump(c, open(p, 'w'), indent=2)
"
  cd "$proj"
  add_src_commit "$proj" "x"
  local INITIAL_SHA; INITIAL_SHA=$(cat "$proj/llake/last-ingest-sha")
  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_SLEEP_SECONDS=5 \
    LLAKE_STUB_PLAN_INLINE='{"version":"1","skip_reason":null,"summary":"x","updates":[],"creates":[],"deletes":[],"bidirectional_links":[],"log_entry":{"operation":"ingest","commit_range":"r","summary":"t","pages_affected":[]}}' \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh" || true
  local sha_now; sha_now=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "planner_timeout_cursor_held" "$INITIAL_SHA" "$sha_now"
  if [ -f "$proj/llake/log.md" ]; then
    if grep -q "ingest | v2" "$proj/llake/log.md"; then
      FAIL=$((FAIL+1)); FAILED_NAMES+=("planner_timeout_no_log_entry")
      echo "  FAIL [planner_timeout_no_log_entry]: log.md unexpectedly contains an ingest entry"
    else
      PASS=$((PASS+1))
    fi
  else
    PASS=$((PASS+1))
  fi
  cd "$REPO_ROOT"
}

test_v2_clean_run
test_legacy_regression
test_v2_schema_invalid_holds_cursor
test_planner_timeout_holds_cursor

echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "FAILED: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
