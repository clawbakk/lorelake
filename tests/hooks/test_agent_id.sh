#!/bin/bash
# Test that generate_agent_id returns a string of expected form.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASS=0
FAIL=0
FAILED_NAMES=()

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test_id_format() {
  source "$REPO_ROOT/hooks/lib/agent-id.sh"
  local id
  id=$(generate_agent_id)
  if [[ ! "$id" =~ ^[a-z]+-[a-z]+-[0-9]{6}-[0-9a-f]{4}$ ]]; then
    FAIL=$((FAIL+1)); FAILED_NAMES+=("agent_id_format")
    echo "  FAIL [agent_id_format]: id '$id' does not match expected pattern <adj>-<noun>-HHMMSS-xxxx"
  else
    PASS=$((PASS+1))
  fi
}

test_id_differs_across_calls() {
  source "$REPO_ROOT/hooks/lib/agent-id.sh"
  local id1 id2
  id1=$(generate_agent_id)
  sleep 1
  id2=$(generate_agent_id)
  if [ "$id1" = "$id2" ]; then
    FAIL=$((FAIL+1)); FAILED_NAMES+=("agent_id_differs_across_calls")
    echo "  FAIL [agent_id_differs_across_calls]: two agent ids identical after 1s sleep: $id1"
  else
    PASS=$((PASS+1))
  fi
}

test_id_suffix_unique_in_same_second() {
  source "$REPO_ROOT/hooks/lib/agent-id.sh"
  local id1 id2
  id1=$(generate_agent_id)
  id2=$(generate_agent_id)
  if [ "$id1" = "$id2" ]; then
    FAIL=$((FAIL+1)); FAILED_NAMES+=("agent_id_unique")
    echo "  FAIL [agent_id_unique]: same id twice in same second: $id1"
  else
    PASS=$((PASS+1))
  fi
}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

test_id_format
test_id_differs_across_calls
test_id_suffix_unique_in_same_second

echo
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failing assertions:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
