#!/bin/bash
# Test hook-log.sh: start/end pairing, crash detection, rotation, one-shot line.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$SCRIPT_DIR/../../hooks/lib"

# shellcheck source=/dev/null
source "$LIB_DIR/hook-log.sh"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# --- Test 1: hook_start writes a timestamped "started" line ---
LOG_FILE="$TMP/hooks.log"
CONFIG_FILE="$TMP/config.json"
LIB_DIR_VAR="$LIB_DIR"
cat > "$CONFIG_FILE" <<EOF
{"logging":{"maxLines":1000,"rotateKeepLines":500}}
EOF
hook_start "post-merge" "$LOG_FILE" "$CONFIG_FILE" "$LIB_DIR_VAR"
LINE=$(cat "$LOG_FILE")
if ! echo "$LINE" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} \| post-merge[[:space:]]+\| started$'; then
  echo "FAIL: hook_start line shape wrong: '$LINE'"
  exit 1
fi

# --- Test 2: hook_end appends outcome on same line ---
hook_end "done: example" "$LOG_FILE"
LINE=$(cat "$LOG_FILE")
if ! echo "$LINE" | grep -q ' → done: example$'; then
  echo "FAIL: hook_end outcome missing: '$LINE'"
  exit 1
fi

# --- Test 3: hook_start detects crash on file with no trailing newline ---
LOG2="$TMP/hooks2.log"
printf '2026-04-23 12:00:00 | session-end   | started' > "$LOG2"  # no newline — simulated crash
hook_start "post-merge" "$LOG2" "$CONFIG_FILE" "$LIB_DIR_VAR"
if ! grep -q ' → CRASHED' "$LOG2"; then
  echo "FAIL: hook_start did not mark prior line CRASHED: $(cat "$LOG2")"
  exit 1
fi

# --- Test 4: hook_log_line writes a single complete line ---
LOG3="$TMP/hooks3.log"
hook_log_line "session-end" "dispatched (async)" "$LOG3"
LINE=$(cat "$LOG3")
if ! echo "$LINE" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} \| session-end[[:space:]]+\| dispatched \(async\)$'; then
  echo "FAIL: hook_log_line shape wrong: '$LINE'"
  exit 1
fi

# --- Test 5: rotation trims to rotateKeepLines when over maxLines ---
LOG4="$TMP/hooks4.log"
cat > "$CONFIG_FILE" <<EOF
{"logging":{"maxLines":3,"rotateKeepLines":2}}
EOF
printf 'line1\nline2\nline3\nline4\n' > "$LOG4"
hook_start "post-merge" "$LOG4" "$CONFIG_FILE" "$LIB_DIR_VAR"
COUNT=$(wc -l < "$LOG4")
# After rotation: keep 2 lines + the new started line (no newline yet) = 3 lines
if [ "$COUNT" -ne 2 ]; then
  echo "FAIL: expected 2 full lines after rotation, got $COUNT"
  cat "$LOG4"
  exit 1
fi

echo "PASS: hook-log.sh"
