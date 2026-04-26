#!/bin/bash
# Tests for the post-merge lock. v2 and legacy both serialize through the
# same lock dir; second-arriving invocation skips with a clear log line.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

# shellcheck source=fixtures/mkproject.sh
source "$FIXTURES/mkproject.sh"

# PATH-injected claude stub so the legacy ingest agent exits cleanly
# (otherwise the watchdog kill-trap clears the EXIT trap that releases
# the lock — that path is correct in production because the 1h staleness
# fallback reclaims it, but it would fail this test's clean-release check).
STUB_BIN=$(mktemp -d -t llake-stub-bin.XXXXXX)
cp "$FIXTURES/claude-stub.sh" "$STUB_BIN/claude"
chmod +x "$STUB_BIN/claude"

cleanup() {
  rm -rf "$STUB_BIN"
}
trap cleanup EXIT

PASS=0; FAIL=0; FAILED_NAMES=()

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
       echo "  FAIL [$label]: expected '$expected', got '$actual'"; fi
}

assert_file_contains() {
  local label="$1" file="$2" needle="$3"
  if grep -qF "$needle" "$file"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label")
       echo "  FAIL [$label]: $file does not contain '$needle'"; fi
}

# Test 1: lock acquired and released cleanly on a normal run
test_lock_acquire_release() {
  local proj; proj=$(mkproject "main")
  cd "$proj"
  echo "// noop" >> "$proj/src/foo.py"
  git -C "$proj" add . && git -C "$proj" commit -q -m "x"
  PATH="$STUB_BIN:$PATH" \
    STUB_EXIT_CODE=0 \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"
  # Lock dir should NOT exist after the run completes
  if [ -d "$proj/llake/.state/post-merge.lock.d" ]; then
    FAIL=$((FAIL+1)); FAILED_NAMES+=("lock_released")
    echo "  FAIL [lock_released]: lock dir still exists after run"
  else
    PASS=$((PASS+1))
  fi
  rm -rf "$proj"
  cd "$REPO_ROOT"
}

# Test 2: second invocation skips when the lock is held
test_second_invocation_skips() {
  local proj; proj=$(mkproject "main")
  cd "$proj"
  # Manually create the lock dir with a live PID
  mkdir -p "$proj/llake/.state/post-merge.lock.d"
  echo $$ > "$proj/llake/.state/post-merge.lock.d/owner.pid"

  echo "// noop" >> "$proj/src/foo.py"
  git -C "$proj" add . && git -C "$proj" commit -q -m "x"
  PATH="$STUB_BIN:$PATH" \
    STUB_EXIT_CODE=0 \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"
  # The hook should have logged a "skipped: lock held" message
  assert_file_contains "lock_skip_logged" \
    "$proj/llake/.state/hooks.log" "lock held"
  # Clean up lock
  rm -rf "$proj/llake/.state/post-merge.lock.d"
  rm -rf "$proj"
  cd "$REPO_ROOT"
}

# Test 3: stale lock (>1 hour old, PID dead) is reclaimed
test_stale_lock_reclaimed() {
  local proj; proj=$(mkproject "main")
  cd "$proj"
  # Create lock with a PID that doesn't exist (use a high implausible value)
  mkdir -p "$proj/llake/.state/post-merge.lock.d"
  echo "999999" > "$proj/llake/.state/post-merge.lock.d/owner.pid"
  # Backdate the lock dir to 2 hours ago
  touch -t "$(date -v-2H +%Y%m%d%H%M)" "$proj/llake/.state/post-merge.lock.d" 2>/dev/null \
    || touch -d "2 hours ago" "$proj/llake/.state/post-merge.lock.d" 2>/dev/null \
    || true

  echo "// noop" >> "$proj/src/foo.py"
  git -C "$proj" add . && git -C "$proj" commit -q -m "x"
  PATH="$STUB_BIN:$PATH" \
    STUB_EXIT_CODE=0 \
    LLAKE_POST_MERGE_SYNC=1 \
    bash "$REPO_ROOT/hooks/post-merge.sh"
  # Hook should have reclaimed and proceeded — look for the reclaim log line
  assert_file_contains "stale_lock_reclaimed" \
    "$proj/llake/.state/hooks.log" "stale lock"
  rm -rf "$proj"
  cd "$REPO_ROOT"
}

test_lock_acquire_release
test_second_invocation_skips
test_stale_lock_reclaimed

echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "FAILED: ${FAILED_NAMES[*]}"
  exit 1
fi
exit 0
