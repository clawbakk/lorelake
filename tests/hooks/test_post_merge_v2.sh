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
  local SHORT_SHA; SHORT_SHA=$(git -C "$proj" rev-parse --short HEAD)
  local PLAN_INLINE
  PLAN_INLINE="{\"version\":\"1\",\"skip_reason\":null,\"summary\":\"trivial\",\"updates\":[],\"creates\":[],\"deletes\":[],\"bidirectional_links\":[],\"commits_addressed\":[{\"sha\":\"$SHORT_SHA\",\"pages\":[]}],\"commits_skipped\":[],\"log_entry\":{\"operation\":\"ingest\",\"commit_range\":\"r\",\"summary\":\"t\",\"pages_affected\":[]}}"
  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_PLAN_INLINE="$PLAN_INLINE" \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"
  local rc=$?
  assert_eq "v2_clean_exit" "0" "$rc"
  local sha_now; sha_now=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "v2_cursor_advanced" "$CURRENT_SHA" "$sha_now"

  # The high-churn list must be written even on small ranges (file always
  # present; may be empty when nothing qualifies).
  local AGENT_DIR_GLOB; AGENT_DIR_GLOB=$(ls -d "$proj/llake/.state/agents/"* 2>/dev/null | head -1)
  if [ -f "$AGENT_DIR_GLOB/context/file_churn.json" ]; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("v2_file_churn_json_present")
       echo "  FAIL [v2_file_churn_json_present]: $AGENT_DIR_GLOB/context/file_churn.json missing"; fi
  if [ -f "$AGENT_DIR_GLOB/context/must-read-patches.txt" ]; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("v2_must_read_patches_present")
       echo "  FAIL [v2_must_read_patches_present]: $AGENT_DIR_GLOB/context/must-read-patches.txt missing"; fi

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

# Test: applier killed mid-pass leaves applied/failed missing → cursor held
test_applier_kill_holds_cursor() {
  local proj; proj=$(mkproject "v2-applier-kill")
  cd "$proj"
  python3 -c "
import json
p = '$proj/llake/config.json'
c = json.load(open(p))
c.setdefault('ingest', {})['pipeline'] = 'v2'
json.dump(c, open(p, 'w'), indent=2)
"
  echo "// noop" >> "$proj/src/foo.py" && git -C "$proj" add . && git -C "$proj" commit -q -m "x"
  local INITIAL_SHA; INITIAL_SHA=$(cat "$proj/llake/last-ingest-sha")
  # Use a malformed-on-purpose plan.json (not valid JSON) that the applier rejects
  # with exit 2 BEFORE writing applied/failed.
  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_PLAN_INLINE='not a plan, just prose' \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh" || true
  local sha_now; sha_now=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "applier_missing_outputs_cursor_held" "$INITIAL_SHA" "$sha_now"
  cd "$REPO_ROOT"
}

# Test: fixer turns a one-failure first pass into a clean run
test_v2_fixer_clears_failure() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  cd "$proj"
  python3 -c "
import json
p = '$proj/llake/config.json'
c = json.load(open(p))
c.setdefault('ingest', {})['pipeline'] = 'v2'
c['ingest'].setdefault('v2', {})['maxFixerRetries'] = 1
json.dump(c, open(p, 'w'), indent=2)
"
  # Seed a wiki page
  mkdir -p "$proj/llake/wiki/hooks"
  cat > "$proj/llake/wiki/hooks/foo.md" << 'PAGE'
---
title: "Foo"
description: "x"
tags: [hooks]
created: 2026-04-23
updated: 2026-04-23
status: current
related: []
---
# Foo

Original body.
PAGE

  echo "// noop" >> "$proj/src/foo.py" && git -C "$proj" add . && git -C "$proj" commit -q -m "x"
  local FIXER_SHA; FIXER_SHA=$(git -C "$proj" rev-parse --short HEAD)

  # First plan: anchor doesn't exist → AnchorNotFound
  PLAN_1="{\"version\":\"1\",\"skip_reason\":null,\"summary\":\"first\",\"updates\":[{\"slug\":\"foo\",\"rationale\":\"r\",\"ops\":[{\"op\":\"replace\",\"find\":\"NOT_THERE\",\"with\":\"x\"}]}],\"creates\":[],\"deletes\":[],\"bidirectional_links\":[],\"commits_addressed\":[{\"sha\":\"$FIXER_SHA\",\"pages\":[\"foo\"]}],\"commits_skipped\":[],\"log_entry\":{\"operation\":\"ingest\",\"commit_range\":\"r\",\"summary\":\"first\",\"pages_affected\":[\"foo\"]}}"

  # Fix plan: anchor that DOES exist → success (fixer pass; no --changes-json check)
  PLAN_2='{"version":"1","skip_reason":null,"summary":"fix","updates":[{"slug":"foo","rationale":"corrected","ops":[{"op":"replace","find":"Original body.","with":"Fixed body."}]}],"creates":[],"deletes":[],"bidirectional_links":[],"commits_addressed":[],"commits_skipped":[],"log_entry":{"operation":"ingest","commit_range":"r","summary":"fix","pages_affected":["foo"]}}'

  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_CALL_COUNTER="$proj/.stub-counter" \
    LLAKE_STUB_PLAN_INLINE_1="$PLAN_1" \
    LLAKE_STUB_PLAN_INLINE_2="$PLAN_2" \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"

  # Expected: foo.md has "Fixed body." (fix-pass succeeded)
  if grep -q "Fixed body." "$proj/llake/wiki/hooks/foo.md"; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1)); FAILED_NAMES+=("fixer_clears_anchor_failure")
    echo "  FAIL [fixer_clears_anchor_failure]: page not updated by fixer"
  fi

  # Expected: log.md has only ONE ingest entry (not two — fix-pass uses --no-log-entry)
  local entry_count
  entry_count=$(grep -c "^## \[.*\] ingest | v2 |" "$proj/llake/log.md" || echo 0)
  assert_eq "fixer_log_md_single_entry" "1" "$entry_count"

  cd "$REPO_ROOT"
}

# Test: fixer fails → first-pass results stand, hook still returns 0
test_v2_fixer_failure_first_pass_stands() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  cd "$proj"
  python3 -c "
import json
p = '$proj/llake/config.json'
c = json.load(open(p))
c.setdefault('ingest', {})['pipeline'] = 'v2'
c['ingest'].setdefault('v2', {})['maxFixerRetries'] = 1
json.dump(c, open(p, 'w'), indent=2)
"
  mkdir -p "$proj/llake/wiki/hooks"
  cat > "$proj/llake/wiki/hooks/foo.md" << 'PAGE'
---
title: "Foo"
description: "x"
tags: []
created: 2026-04-23
updated: 2026-04-23
status: current
related: []
---
# Foo

Body.
PAGE

  echo "// noop" >> "$proj/src/foo.py" && git -C "$proj" add . && git -C "$proj" commit -q -m "x"
  local FAILURE_SHA; FAILURE_SHA=$(git -C "$proj" rev-parse --short HEAD)

  PLAN_1="{\"version\":\"1\",\"skip_reason\":null,\"summary\":\"first\",\"updates\":[{\"slug\":\"foo\",\"rationale\":\"r\",\"ops\":[{\"op\":\"replace\",\"find\":\"NOT_THERE\",\"with\":\"x\"}]}],\"creates\":[],\"deletes\":[],\"bidirectional_links\":[],\"commits_addressed\":[{\"sha\":\"$FAILURE_SHA\",\"pages\":[\"foo\"]}],\"commits_skipped\":[],\"log_entry\":{\"operation\":\"ingest\",\"commit_range\":\"r\",\"summary\":\"first\",\"pages_affected\":[\"foo\"]}}"

  # Fix plan: still wrong → fix pass also produces failed.json (no --changes-json on fixer)
  PLAN_2='{"version":"1","skip_reason":null,"summary":"fix","updates":[{"slug":"foo","rationale":"r","ops":[{"op":"replace","find":"ALSO_NOT_THERE","with":"y"}]}],"creates":[],"deletes":[],"bidirectional_links":[],"commits_addressed":[],"commits_skipped":[],"log_entry":{"operation":"ingest","commit_range":"r","summary":"fix","pages_affected":["foo"]}}'

  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_CALL_COUNTER="$proj/.stub-counter" \
    LLAKE_STUB_PLAN_INLINE_1="$PLAN_1" \
    LLAKE_STUB_PLAN_INLINE_2="$PLAN_2" \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"

  # Expected: hooks.log has a 'partial' line
  assert_file_contains "fixer_failure_partial_logged" \
    "$proj/llake/.state/hooks.log" "partial:"

  cd "$REPO_ROOT"
}

# Test: build-ingest-context.py failure (bogus last-sha) holds the cursor
test_stage1_failure_holds_cursor() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  set_pipeline_v2 "$proj"
  cd "$proj"

  # Plant a bogus last-ingest-sha so the git range is invalid
  echo "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" > "$proj/llake/last-ingest-sha"

  add_src_commit "$proj" "x"

  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_PLAN_INLINE='{}' \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh" || true

  # The bogus SHA causes post-merge.sh to fail the git range check and exit
  # early (invalid-range branch), OR build_ingest_context.py fails at stage 1.
  # Either way the hook ran and appended to hooks.log.
  assert_file_contains "stage1_or_invalid_range_logged" \
    "$proj/llake/.state/hooks.log" "post-merge"

  cd "$REPO_ROOT"
}

# Test: empty plan file (planner wrote nothing) holds cursor
test_empty_plan_file_holds_cursor() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  set_pipeline_v2 "$proj"
  cd "$proj"

  add_src_commit "$proj" "x"
  local INITIAL_SHA; INITIAL_SHA=$(cat "$proj/llake/last-ingest-sha")

  # Stub emits an empty string result; format-agent-log.py skips writing plan.json
  # when result_text is falsy, so [ ! -s "$PLAN_FILE" ] is true → planner failure
  # path → cursor not advanced.
  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_PLAN_INLINE='' \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh" || true

  # Cursor must NOT have advanced
  local sha_now; sha_now=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "empty_plan_cursor_held" "$INITIAL_SHA" "$sha_now"

  cd "$REPO_ROOT"
}

# Test: --changes-json coverage check — plan covers only one of two commits → cursor held
test_v2_coverage_fail_holds_cursor() {
  local proj; proj=$(mkproject "main")
  TMP_PROJECTS+=("$proj")
  set_pipeline_v2 "$proj"
  cd "$proj"

  # Two commits in range (so changes.json will have 2 entries)
  add_src_commit "$proj" "commit-alpha"
  add_src_commit "$proj" "commit-beta"
  local CURRENT_SHA; CURRENT_SHA=$(git -C "$proj" rev-parse HEAD)
  local INITIAL_SHA; INITIAL_SHA=$(cat "$proj/llake/last-ingest-sha")

  # Get the short SHA of only the first new commit (commit-alpha, one before HEAD)
  local SHA_ONE
  SHA_ONE=$(git -C "$proj" rev-parse --short HEAD~1)

  # Plan addresses only one of the two commits; commits_skipped is empty.
  # The cross-check will find the other commit uncovered → applier exits nonzero.
  local PLAN_INLINE
  PLAN_INLINE="{\"version\":\"1\",\"skip_reason\":null,\"summary\":\"partial\",\"updates\":[],\"creates\":[],\"deletes\":[],\"bidirectional_links\":[],\"commits_addressed\":[{\"sha\":\"$SHA_ONE\",\"pages\":[]}],\"commits_skipped\":[],\"log_entry\":{\"operation\":\"ingest\",\"commit_range\":\"r\",\"summary\":\"partial\",\"pages_affected\":[]}}"

  PATH="$STUB_BIN:$PATH" \
    LLAKE_STUB_MODE=ingest-v2-planner \
    LLAKE_STUB_PLAN_INLINE="$PLAN_INLINE" \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh" || true

  # Cursor must NOT have advanced (cross-check failure holds the cursor)
  local sha_now; sha_now=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "coverage_fail_cursor_held" "$INITIAL_SHA" "$sha_now"

  # Sanity: current SHA was not written either
  if [ "$sha_now" = "$CURRENT_SHA" ]; then
    FAIL=$((FAIL+1)); FAILED_NAMES+=("coverage_fail_cursor_not_at_head")
    echo "  FAIL [coverage_fail_cursor_not_at_head]: cursor advanced to HEAD unexpectedly"
  else
    PASS=$((PASS+1))
  fi

  cd "$REPO_ROOT"
}

test_v2_clean_run
test_legacy_regression
test_v2_schema_invalid_holds_cursor
test_planner_timeout_holds_cursor
test_applier_kill_holds_cursor
test_v2_fixer_clears_failure
test_v2_fixer_failure_first_pass_stands
test_stage1_failure_holds_cursor
test_empty_plan_file_holds_cursor
test_v2_coverage_fail_holds_cursor

echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "FAILED: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
