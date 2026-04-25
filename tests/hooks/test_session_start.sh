#!/bin/bash
# Integration tests for hooks/session-start.sh.
# Asserts: recursion guard, normal context-injection, no-preamble/no-index skip.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

# shellcheck source=fixtures/mkproject.sh
source "$FIXTURES/mkproject.sh"

PASS=0 ; FAIL=0 ; FAILED_NAMES=()

assert_contains() {
  local label="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -q "$needle"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label"); echo "  FAIL [$label]: '$needle' not found in output"
  fi
}
assert_not_contains() {
  local label="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -q "$needle"; then
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$label"); echo "  FAIL [$label]: unexpected '$needle' in output"
  else PASS=$((PASS+1)); fi
}
assert_log_grep() {
  local label="$1" log="$2" pattern="$3"
  if [ -f "$log" ] && grep -q "$pattern" "$log"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label"); echo "  FAIL [$label]: '$pattern' not in $log"; [ -f "$log" ] && sed 's/^/    /' "$log"
  fi
}

run_session_start() {
  local proj="$1"; shift
  echo '{"cwd":"'"$proj"'"}' | "$@" "$REPO_ROOT/hooks/session-start.sh"
}

# ---------------------------------------------------------------------------

test_normal_injection() {
  local proj; proj=$(mkproject)
  echo "# Project Wiki Index" > "$proj/llake/index.md"

  local out
  out=$(run_session_start "$proj")

  assert_contains "normal:hook-event" "$out" "SessionStart"
  assert_contains "normal:additional-context" "$out" "additionalContext"
  assert_contains "normal:index-content" "$out" "Project Wiki Index"
  assert_log_grep "normal:logged" "$proj/llake/.state/hooks.log" "context injected"

  rm -rf "$proj"
}

test_recursion_guard() {
  local proj; proj=$(mkproject)
  echo "# index" > "$proj/llake/index.md"

  local out
  out=$(run_session_start "$proj" env IS_LLAKE_AGENT=true LLAKE_AGENT_ID=test-agent-123)

  assert_not_contains "guard:no-additional-context" "$out" "additionalContext"
  assert_log_grep "guard:logged" "$proj/llake/.state/hooks.log" "recursion guard"
  assert_log_grep "guard:agent-id" "$proj/llake/.state/hooks.log" "test-agent-123"

  rm -rf "$proj"
}

test_skip_when_no_preamble_or_index() {
  local proj; proj=$(mkproject)
  rm -f "$proj/llake/index.md"

  # Shim the plugin so templates/session-preamble.md is absent.
  local plugin_shim; plugin_shim=$(mktemp -d -t llake-shim.XXXXXX)
  cp -R "$REPO_ROOT/hooks" "$plugin_shim/"
  mkdir -p "$plugin_shim/templates"
  # No session-preamble.md in shim.

  local out
  out=$(echo '{"cwd":"'"$proj"'"}' | "$plugin_shim/hooks/session-start.sh")

  assert_not_contains "no-content:no-additional-context" "$out" "additionalContext"
  assert_log_grep "no-content:logged" "$proj/llake/.state/hooks.log" "no preamble or index"

  rm -rf "$plugin_shim" "$proj"
}

# ---------------------------------------------------------------------------

echo "-- normal context injection";    test_normal_injection
echo "-- recursion guard";              test_recursion_guard
echo "-- skip when no preamble/index";  test_skip_when_no_preamble_or_index

echo
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failing assertions:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
