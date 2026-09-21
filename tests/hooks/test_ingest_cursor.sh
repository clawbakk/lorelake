#!/bin/bash
# Unit tests for hooks/lib/ingest-cursor.sh.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../hooks/lib/ingest-cursor.sh
source "$REPO_ROOT/hooks/lib/ingest-cursor.sh"

PASS=0
FAIL=0
FAILED_NAMES=()

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

mkstate() {
  local dir
  dir=$(mktemp -d -t llake-cursor-test.XXXXXX)
  mkdir -p "$dir/llake/.state"
  echo "$dir"
}

# --- Test 1: writes both files ---
test_writes_both_files() {
  local d; d=$(mkstate)
  LLAKE_ROOT="$d/llake" STATE_DIR="$d/llake/.state" advance_ingest_cursor "abc1234"
  assert_eq "both:sha" "abc1234" "$(cat "$d/llake/last-ingest-sha")"
  local ts; ts=$(cat "$d/llake/.state/last-ingest-at")
  case "$ts" in
    ''|*[!0-9]*) assert_eq "both:ts-numeric" "numeric" "not-numeric ($ts)" ;;
    *)           assert_eq "both:ts-numeric" "numeric" "numeric" ;;
  esac
  local now; now=$(date +%s)
  if [ "$((now - ts))" -le 5 ]; then
    assert_eq "both:ts-recent" "recent" "recent"
  else
    assert_eq "both:ts-recent" "recent" "stale (now=$now ts=$ts)"
  fi
  rm -rf "$d"
}

# --- Test 2: creates .state/ when missing ---
test_creates_state_dir() {
  local d; d=$(mkstate)
  rm -rf "$d/llake/.state"
  LLAKE_ROOT="$d/llake" STATE_DIR="$d/llake/.state" advance_ingest_cursor "def5678"
  if [ -f "$d/llake/.state/last-ingest-at" ]; then
    assert_eq "mkdir:ts-exists" "yes" "yes"
  else
    assert_eq "mkdir:ts-exists" "yes" "no"
  fi
  rm -rf "$d"
}

# --- Test 3: empty sha is rejected, nothing is written ---
test_empty_sha_rejected() {
  local d; d=$(mkstate)
  echo "original" > "$d/llake/last-ingest-sha"
  LLAKE_ROOT="$d/llake" STATE_DIR="$d/llake/.state" advance_ingest_cursor ""
  assert_eq "empty:rc" "1" "$?"
  assert_eq "empty:sha-untouched" "original" "$(cat "$d/llake/last-ingest-sha")"
  if [ -f "$d/llake/.state/last-ingest-at" ]; then
    assert_eq "empty:no-ts" "absent" "present"
  else
    assert_eq "empty:no-ts" "absent" "absent"
  fi
  rm -rf "$d"
}

# --- Test 4: ensure_ingest_clock seeds a missing clock ---
test_ensure_clock_seeds() {
  local d; d=$(mkstate)
  LLAKE_ROOT="$d/llake" STATE_DIR="$d/llake/.state" ensure_ingest_clock
  local ts; ts=$(cat "$d/llake/.state/last-ingest-at" 2>/dev/null || echo "")
  case "$ts" in
    ''|*[!0-9]*) assert_eq "seed:numeric" "numeric" "not-numeric ($ts)" ;;
    *)           assert_eq "seed:numeric" "numeric" "numeric" ;;
  esac
  rm -rf "$d"
}

# --- Test 5: ensure_ingest_clock never overwrites an existing clock ---
test_ensure_clock_preserves() {
  local d; d=$(mkstate)
  echo "12345" > "$d/llake/.state/last-ingest-at"
  LLAKE_ROOT="$d/llake" STATE_DIR="$d/llake/.state" ensure_ingest_clock
  assert_eq "preserve:unchanged" "12345" "$(cat "$d/llake/.state/last-ingest-at")"
  rm -rf "$d"
}

test_writes_both_files
test_creates_state_dir
test_empty_sha_rejected
test_ensure_clock_seeds
test_ensure_clock_preserves

echo ""
echo "PASS=$PASS FAIL=$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed: ${FAILED_NAMES[*]}"
  exit 1
fi
