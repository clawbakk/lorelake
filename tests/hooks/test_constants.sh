#!/bin/bash
# Test that constants.sh exports the expected fixed names.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../../hooks/lib"

# shellcheck source=/dev/null
source "$LIB_DIR/constants.sh"

if [ "$LLAKE_DIR_NAME" != "llake" ]; then
  echo "FAIL: expected LLAKE_DIR_NAME=llake, got '$LLAKE_DIR_NAME'"
  exit 1
fi

if [ "$WIKI_DIR_NAME" != "wiki" ]; then
  echo "FAIL: expected WIKI_DIR_NAME=wiki, got '$WIKI_DIR_NAME'"
  exit 1
fi

echo "PASS: constants.sh"
