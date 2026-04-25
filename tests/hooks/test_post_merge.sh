#!/bin/bash
# Integration tests for hooks/post-merge.sh.
# Uses a claude stub (PATH injection) and LLAKE_POST_MERGE_SYNC=1 — no real
# `claude -p` is invoked.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

# shellcheck source=fixtures/mkproject.sh
source "$FIXTURES/mkproject.sh"

# PATH-injected claude stub.
STUB_BIN=$(mktemp -d -t llake-stub-bin.XXXXXX)
cp "$FIXTURES/claude-stub.sh" "$STUB_BIN/claude"
chmod +x "$STUB_BIN/claude"

PASS=0
FAIL=0
FAILED_NAMES=()

cleanup() {
  rm -rf "$STUB_BIN"
}
trap cleanup EXIT

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("$label")
    echo "  FAIL [$label]: expected '$expected', got '$actual'"
  fi
}

assert_log_grep() {
  local label="$1" log="$2" pattern="$3"
  if [ -f "$log" ] && grep -q "$pattern" "$log"; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("$label")
    echo "  FAIL [$label]: log '$log' does not match '$pattern'"
    [ -f "$log" ] && sed 's/^/    /' "$log"
  fi
}

assert_log_no_grep() {
  local label="$1" log="$2" pattern="$3"
  if [ ! -f "$log" ] || ! grep -q "$pattern" "$log"; then
    PASS=$((PASS+1))
  else
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("$label")
    echo "  FAIL [$label]: log '$log' unexpectedly matches '$pattern'"
    sed 's/^/    /' "$log"
  fi
}

run_post_merge() {
  local project="$1" ; shift
  (
    cd "$project" || exit 1
    PATH="$STUB_BIN:$PATH" \
      LLAKE_POST_MERGE_SYNC=1 \
      STUB_EXIT_CODE="${STUB_EXIT_CODE:-0}" \
      STUB_STREAM_FILE="${STUB_STREAM_FILE:-}" \
      STUB_RECORD_FILE="${STUB_RECORD_FILE:-}" \
      "$@" \
      bash "$REPO_ROOT/hooks/post-merge.sh"
  )
}

run_test() {
  local name="$1" ; shift
  echo "-- $name"
  "$@"
}

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

test_no_new_commits() {
  local proj; proj=$(mkproject)
  run_post_merge "$proj"
  assert_log_grep "no-new-commits:log" "$proj/llake/.state/hooks.log" "no new commits"
  local sha_before sha_after
  sha_before=$(git -C "$proj" rev-parse HEAD)
  sha_after=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "no-new-commits:sha-unchanged" "$sha_before" "$sha_after"
  rm -rf "$proj"
}

test_no_relevant_files() {
  local proj; proj=$(mkproject)
  add_nonsrc_commit "$proj"
  run_post_merge "$proj"
  assert_log_grep "no-relevant-files:log" "$proj/llake/.state/hooks.log" "no relevant file changes"
  local head; head=$(git -C "$proj" rev-parse HEAD)
  assert_eq "no-relevant-files:sha-advanced" "$head" "$(cat "$proj/llake/last-ingest-sha")"
  rm -rf "$proj"
}

test_wrong_branch() {
  local proj; proj=$(mkproject)
  (cd "$proj" && git checkout -q -b feature)
  run_post_merge "$proj"
  assert_log_grep "wrong-branch:log" "$proj/llake/.state/hooks.log" "not on main"
  rm -rf "$proj"
}

test_ingest_disabled() {
  local proj; proj=$(mkproject)
  python3 -c "
import json, sys
p = sys.argv[1]
with open(p) as f: d = json.load(f)
d['ingest']['enabled'] = False
with open(p, 'w') as f: json.dump(d, f)
" "$proj/llake/config.json"
  add_src_commit "$proj"
  run_post_merge "$proj"
  assert_log_grep "ingest-disabled:log" "$proj/llake/.state/hooks.log" "ingest disabled"
  rm -rf "$proj"
}

test_render_fails_no_ghost_agent() {
  local proj; proj=$(mkproject)
  local bad_tmpl="$proj/llake/.state/bad-ingest.md.tmpl"
  mkdir -p "$(dirname "$bad_tmpl")"
  echo 'Needs {{DEFINITELY_NOT_WIRED}}' > "$bad_tmpl"

  add_src_commit "$proj"
  (
    cd "$proj"
    PROMPTS_SHIM=$(mktemp -d)
    for f in "$REPO_ROOT/hooks/prompts"/*; do
      ln -s "$f" "$PROMPTS_SHIM/$(basename "$f")"
    done
    rm -f "$PROMPTS_SHIM/ingest.md.tmpl"
    cp "$bad_tmpl" "$PROMPTS_SHIM/ingest.md.tmpl"

    HOOK_SHIM=$(mktemp -d)
    cp -R "$REPO_ROOT/hooks/lib" "$HOOK_SHIM/"
    cp "$REPO_ROOT/hooks/post-merge.sh" "$HOOK_SHIM/"
    mv "$PROMPTS_SHIM" "$HOOK_SHIM/prompts"

    PATH="$STUB_BIN:$PATH" LLAKE_POST_MERGE_SYNC=1 bash "$HOOK_SHIM/post-merge.sh"
    RC=$?
    rm -rf "$HOOK_SHIM"
    exit "$RC"
  )

  assert_log_grep "render-fails:log" "$proj/llake/.state/hooks.log" "render-prompt failed"
  assert_log_no_grep "render-fails:no-completed" "$proj/llake/.state/hooks.log" "completed: agent"

  local sha_before; sha_before=$(git -C "$proj" rev-parse 'HEAD^')
  local sha_file;   sha_file=$(cat "$proj/llake/last-ingest-sha")
  assert_eq "render-fails:sha-unchanged" "$sha_before" "$sha_file"

  rm -rf "$proj"
}

test_agent_exit_0_advances_sha() {
  local proj; proj=$(mkproject)
  add_src_commit "$proj" "feat: add feature"
  STUB_EXIT_CODE=0 run_post_merge "$proj"

  local head; head=$(git -C "$proj" rev-parse HEAD)
  assert_eq "exit0:sha-advanced" "$head" "$(cat "$proj/llake/last-ingest-sha")"
  assert_log_grep "exit0:log" "$proj/llake/.state/hooks.log" "completed: agent .*exit 0"
  assert_log_grep "exit0:log-sha" "$proj/llake/.state/hooks.log" "sha: advanced to"
  rm -rf "$proj"
}

test_agent_exit_1_no_advance() {
  local proj; proj=$(mkproject)
  add_src_commit "$proj"
  local sha_before; sha_before=$(git -C "$proj" rev-parse 'HEAD^')
  STUB_EXIT_CODE=1 run_post_merge "$proj"

  assert_eq "exit1:sha-unchanged" "$sha_before" "$(cat "$proj/llake/last-ingest-sha")"
  assert_log_grep "exit1:log" "$proj/llake/.state/hooks.log" "failed: agent .*exit 1"
  assert_log_no_grep "exit1:no-advance" "$proj/llake/.state/hooks.log" "sha: advanced"
  rm -rf "$proj"
}

test_agent_exit_137_external_kill() {
  local proj; proj=$(mkproject)
  add_src_commit "$proj"
  local sha_before; sha_before=$(git -C "$proj" rev-parse 'HEAD^')
  STUB_EXIT_CODE=137 run_post_merge "$proj"

  assert_eq "exit137:sha-unchanged" "$sha_before" "$(cat "$proj/llake/last-ingest-sha")"
  assert_log_grep "exit137:log" "$proj/llake/.state/hooks.log" "killed: agent .*exit 137.*external"
  rm -rf "$proj"
}

test_claude_invocation_flags() {
  local proj; proj=$(mkproject)
  add_src_commit "$proj"
  local record; record=$(mktemp -t llake-claude-args.XXXXXX)

  STUB_EXIT_CODE=0 STUB_RECORD_FILE="$record" run_post_merge "$proj"

  # --tools must be present with the config's allowedTools list.
  if ! grep -q -- "--tools" "$record"; then
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("flags:tools-flag-present")
    echo "  FAIL [flags:tools-flag-present]: --tools not found in invocation"
    sed 's/^/    /' "$record"
  else
    PASS=$((PASS+1))
  fi

  # The tool list from mkproject's config is ["Read","Write"].
  if ! grep -qE -- "--tools[[:space:]]+['\"]?Read,Write" "$record"; then
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("flags:tools-value")
    echo "  FAIL [flags:tools-value]: --tools value missing expected Read,Write"
    sed 's/^/    /' "$record"
  else
    PASS=$((PASS+1))
  fi

  # --strict-mcp-config must be present.
  if ! grep -q -- "--strict-mcp-config" "$record"; then
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("flags:strict-mcp-config-present")
    echo "  FAIL [flags:strict-mcp-config-present]: --strict-mcp-config missing"
    sed 's/^/    /' "$record"
  else
    PASS=$((PASS+1))
  fi

  # --allowedTools must ALSO be present and match --tools. --tools restricts
  # the available surface; --allowedTools auto-approves those tools so they
  # don't trigger the permission prompt in headless -p mode.
  if ! grep -q -- "--allowedTools" "$record"; then
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("flags:allowedTools-present")
    echo "  FAIL [flags:allowedTools-present]: --allowedTools missing"
    sed 's/^/    /' "$record"
  else
    PASS=$((PASS+1))
  fi

  if ! grep -qE -- "--allowedTools[[:space:]]+['\"]?Read,Write" "$record"; then
    FAIL=$((FAIL+1))
    FAILED_NAMES+=("flags:allowedTools-value")
    echo "  FAIL [flags:allowedTools-value]: --allowedTools value missing expected Read,Write"
    sed 's/^/    /' "$record"
  else
    PASS=$((PASS+1))
  fi

  rm -f "$record"
  rm -rf "$proj"
}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

run_test "no new commits"           test_no_new_commits
run_test "no relevant files"        test_no_relevant_files
run_test "wrong branch"             test_wrong_branch
run_test "ingest disabled"          test_ingest_disabled
run_test "render fails — no ghost"  test_render_fails_no_ghost_agent
run_test "exit 0 advances SHA"      test_agent_exit_0_advances_sha
run_test "exit 1 does not advance"  test_agent_exit_1_no_advance
run_test "exit 137 external kill"   test_agent_exit_137_external_kill
run_test "claude invocation flags"  test_claude_invocation_flags

echo
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failing assertions:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
