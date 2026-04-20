#!/bin/bash
# Test detect_project_root: env override, marker walk, git toplevel.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../../hooks/lib"

# shellcheck source=/dev/null
source "$LIB_DIR/detect-project-root.sh"

# Set up an isolated temp area
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# --- Test 1: env override wins ---
mkdir -p "$TMP/proj1/llake"
touch "$TMP/proj1/llake/config.json"
LLAKE_PROJECT_ROOT="$TMP/proj1" RESULT=$(detect_project_root "/some/random/cwd")
if [ "$RESULT" != "$TMP/proj1" ]; then
  echo "FAIL: env override expected '$TMP/proj1', got '$RESULT'"
  exit 1
fi

# --- Test 2: marker walk finds llake/config.json upward ---
mkdir -p "$TMP/proj2/llake"
touch "$TMP/proj2/llake/config.json"
mkdir -p "$TMP/proj2/src/deep/nested"
unset LLAKE_PROJECT_ROOT
RESULT=$(detect_project_root "$TMP/proj2/src/deep/nested")
if [ "$RESULT" != "$TMP/proj2" ]; then
  echo "FAIL: marker walk expected '$TMP/proj2', got '$RESULT'"
  exit 1
fi

# --- Test 3: marker walk returns nonzero when no marker found ---
mkdir -p "$TMP/proj3/src"
unset LLAKE_PROJECT_ROOT
if RESULT=$(detect_project_root "$TMP/proj3/src" 2>/dev/null); then
  echo "FAIL: expected nonzero exit when no marker, got '$RESULT'"
  exit 1
fi

echo "PASS: detect-project-root.sh"
