#!/bin/bash
# Test that generate_agent_id returns a string of form <adj>-<noun>-<6digits>.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../../hooks/lib"

# shellcheck source=/dev/null
source "$LIB_DIR/agent-id.sh"

ID=$(generate_agent_id)
if [[ ! "$ID" =~ ^[a-z]+-[a-z]+-[0-9]{6}$ ]]; then
  echo "FAIL: agent id '$ID' does not match expected pattern"
  exit 1
fi

# Two consecutive calls should differ at least in the time portion or random portion
ID1=$(generate_agent_id)
sleep 1
ID2=$(generate_agent_id)
if [ "$ID1" = "$ID2" ]; then
  echo "FAIL: two agent ids identical (no randomness or time component)"
  exit 1
fi

echo "PASS: agent-id.sh"
