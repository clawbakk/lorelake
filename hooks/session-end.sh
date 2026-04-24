#!/bin/bash
# LoreLake SessionEnd hook — foreground dispatcher.
#
# This script does the absolute minimum synchronously and hands off to the
# detached worker at hooks/lib/session-capture-worker.sh. On /exit or /clear
# the user's session closes immediately; the worker runs in the background.
#
# Foreground responsibilities (in order):
#   1. Recursion guard.
#   2. Locate the project via marker walk (pure bash).
#   3. Log "dispatched (async)" to hooks.log.
#   4. Persist stdin to a tempfile the worker consumes.
#   5. Fork the worker detached, or (if LLAKE_SESSION_END_SYNC=1) exec it.
#
# There are NO python3 calls in this script. Everything config-related,
# transcript-related, and agent-related lives in the worker.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${LLAKE_LIB_DIR_OVERRIDE:-$SCRIPT_DIR/lib}"

# shellcheck source=/dev/null
source "$LIB_DIR/constants.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/detect-project-root.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/hook-log.sh"

# --- Recursion guard (no project-root work needed) ---
if [ "${IS_LLAKE_AGENT:-}" = "true" ]; then
  exit 0
fi

# --- Project root via pure-bash marker walk ---
PROJECT_ROOT=$(detect_project_root "$PWD" 2>/dev/null) || exit 0

LLAKE_ROOT="$PROJECT_ROOT/$LLAKE_DIR_NAME"
STATE_DIR="$LLAKE_ROOT/.state"
LOG_FILE="$STATE_DIR/hooks.log"
mkdir -p "$STATE_DIR"

# --- Persist stdin for the worker ---
TMP_INPUT=$(mktemp -t llake-session-end.XXXXXX)
cat > "$TMP_INPUT"

# --- Log the dispatch ---
hook_log_line "session-end" "dispatched (async)" "$LOG_FILE"

# --- Sync mode (debugging) ---
if [ "${LLAKE_SESSION_END_SYNC:-}" = "1" ]; then
  exec bash "$LIB_DIR/session-capture-worker.sh" "$TMP_INPUT"
fi

# --- Detached dispatch ---
nohup bash "$LIB_DIR/session-capture-worker.sh" "$TMP_INPUT" </dev/null >/dev/null 2>&1 &
disown $!

exit 0
