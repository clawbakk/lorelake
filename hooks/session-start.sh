#!/bin/bash
# LoreLake plugin — SessionStart hook.
# Injects the operating-manual preamble + LoreLake index as additionalContext.
# Pure context injection — performs no state checks, diagnostics, or repairs.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"
TEMPLATES_DIR="$PLUGIN_ROOT/templates"
PREAMBLE_FILE="$TEMPLATES_DIR/session-preamble.md"

# shellcheck source=/dev/null
source "$LIB_DIR/constants.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/detect-project-root.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/hook-log.sh"

# Read stdin JSON for cwd
INPUT=$(cat)
CWD=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('cwd', ''))
except:
    print('')
" 2>/dev/null)

# Detect project root (env override → marker walk)
PROJECT_ROOT=$(detect_project_root "${CWD:-$PWD}" 2>/dev/null) || exit 0

LLAKE_ROOT="$PROJECT_ROOT/$LLAKE_DIR_NAME"
INDEX_FILE="$LLAKE_ROOT/index.md"

STATE_DIR="$LLAKE_ROOT/.state"
LOG_FILE="$STATE_DIR/hooks.log"
CONFIG_FILE="$LLAKE_ROOT/config.json"
mkdir -p "$STATE_DIR"

HOOK_NAME="session-start"
hook_start "$HOOK_NAME" "$LOG_FILE" "$CONFIG_FILE" "$LIB_DIR"

PREAMBLE=""
[ -f "$PREAMBLE_FILE" ] && PREAMBLE=$(cat "$PREAMBLE_FILE")

INDEX=""
[ -f "$INDEX_FILE" ] && INDEX=$(cat "$INDEX_FILE")

if [ -z "$PREAMBLE" ] && [ -z "$INDEX" ]; then
  hook_end "skipped: no preamble or index" "$LOG_FILE"
  exit 0
fi

COMBINED="$PREAMBLE"
[ -n "$INDEX" ] && COMBINED="$COMBINED

---

$INDEX"

TMP_CONTEXT=$(mktemp)
trap 'rm -f "$TMP_CONTEXT"' EXIT
printf "%s" "$COMBINED" > "$TMP_CONTEXT"

python3 - "$TMP_CONTEXT" << 'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    content = f.read()
output = {
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': content
    }
}
print(json.dumps(output))
PYEOF

hook_end "context injected" "$LOG_FILE"
