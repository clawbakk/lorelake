#!/bin/bash
# LoreLake post-merge hook — detects new commits pulled into the monitored
# branch and spawns a background Claude CLI agent to update the LoreLake wiki.
#
# Trigger: git post-merge hook (fires after `git pull` merges new commits)
# Output: spawns detached background process if new commits detected
#
# Project detection: uses `git rev-parse --show-toplevel` — post-merge fires
# inside a git repo by definition. Exits silently if no llake/config.json.
#
# Safety features:
#   - Budget cap: --max-budget-usd (prevents runaway spend)
#   - Timeout watchdog: kills agent after MAX_TIMEOUT_SEC
#   - Agent logs: captured to <project>/llake/.state/agents/<agent-id>/agent.log
#   - Readable agent IDs for traceability

# --- Git hook wiring ---
# This script is designed to be triggered by a git post-merge hook.
# To wire it up in your local clone, run once from the repo root:
#
#   printf '#!/bin/bash\nexec "$(git rev-parse --show-toplevel)/lorelake/hooks/post-merge.sh" "$@"\n' \
#     > "$(git rev-parse --git-common-dir)/hooks/post-merge" \
#     && chmod +x "$(git rev-parse --git-common-dir)/hooks/post-merge"
#
# The shim lives in the repo's .git/hooks/post-merge and delegates to this script.
# Worktrees share the main repo's hooks/ dir, so one install covers all worktrees.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"
PROMPTS_DIR="$SCRIPT_DIR/prompts"
TEMPLATES_DIR="$PLUGIN_ROOT/templates"
SCHEMA_DIR="$PLUGIN_ROOT/schema"

# shellcheck source=/dev/null
source "$LIB_DIR/constants.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/agent-id.sh"

# post-merge fires inside a git repo by definition.
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

# But also respect env override (testing)
if [ -n "${LLAKE_PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$LLAKE_PROJECT_ROOT"
fi

LLAKE_ROOT="$PROJECT_ROOT/$LLAKE_DIR_NAME"
WIKI_ROOT="$LLAKE_ROOT/$WIKI_DIR_NAME"
STATE_DIR="$LLAKE_ROOT/.state"
mkdir -p "$STATE_DIR"
SHA_FILE="$LLAKE_ROOT/last-ingest-sha"
AGENTS_DIR="$STATE_DIR/agents"
CONFIG_FILE="$LLAKE_ROOT/config.json"

# No LoreLake install → silent no-op
[ -f "$CONFIG_FILE" ] || exit 0

HOOK_NAME="post-merge"
LOG_FILE="$STATE_DIR/hooks.log"

# --- Logging ---
hook_start() {
  [ -f "$LOG_FILE" ] && [ -n "$(tail -c 1 "$LOG_FILE")" ] && echo " → CRASHED" >> "$LOG_FILE"
  LOG_MAX=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "logging.maxLines")
  LOG_KEEP=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "logging.rotateKeepLines")
  [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt "$LOG_MAX" ] && tail -"$LOG_KEEP" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
  printf "%s | %-13s | started" "$(date '+%Y-%m-%d %H:%M:%S')" "$HOOK_NAME" >> "$LOG_FILE"
}

hook_end() {
  echo " → $1" >> "$LOG_FILE"
}

hook_start

# Recursion guard
AGENT_ID="${LLAKE_AGENT_ID:-}"
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${AGENT_ID:-unknown}]"
  exit 0
fi

# Master toggle
INGEST_ENABLED=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.enabled")
if [ "$INGEST_ENABLED" = "false" ]; then
  hook_end "skipped: ingest disabled"
  exit 0
fi

# Read ingest settings from config
MAX_BUDGET_USD=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.maxBudgetUsd")
MAX_TIMEOUT_SEC=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.timeoutSeconds")
INGEST_BRANCH=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.branch")
INGEST_MODEL=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.model")
INGEST_EFFORT=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.effort")
ALLOWED_TOOLS_JSON=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.allowedTools")
INCLUDE_JSON=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.include")

# Convert allowedTools JSON array to comma-separated string
ALLOWED_TOOLS=$(python3 -c "import json,sys; print(','.join(json.loads(sys.argv[1])))" "$ALLOWED_TOOLS_JSON" 2>/dev/null || echo "Read,Write,Edit,Glob,Grep,Bash")

# Build include path array from config (ingest.include)
INCLUDE_PATHS=()
while IFS= read -r line; do
  [ -n "$line" ] && INCLUDE_PATHS+=("$line")
done < <(python3 -c "
import json, sys
try:
    include = json.loads(sys.argv[1])
    for p in include:
        print(p)
except Exception:
    pass
" "$INCLUDE_JSON" 2>/dev/null)

INGEST_PROMPT_TMPL="$PROMPTS_DIR/ingest.md.tmpl"

if [ ! -f "$INGEST_PROMPT_TMPL" ]; then
  hook_end "skipped: missing ingest prompt file"
  exit 0
fi

# Build optional --model and --effort flags
MODEL_FLAG=""
if [ -n "$INGEST_MODEL" ]; then
  MODEL_FLAG="--model $INGEST_MODEL"
fi
EFFORT_FLAG=""
if [ -n "$INGEST_EFFORT" ]; then
  EFFORT_FLAG="--effort $INGEST_EFFORT"
fi

# Only run on configured branch
CURRENT_BRANCH=$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$CURRENT_BRANCH" != "$INGEST_BRANCH" ]; then
  hook_end "skipped: not on $INGEST_BRANCH ($CURRENT_BRANCH)"
  exit 0
fi

# Read last ingested SHA
if [ ! -f "$SHA_FILE" ]; then
  hook_end "skipped: no last-ingest-sha file"
  exit 0
fi
LAST_SHA=$(cat "$SHA_FILE" | tr -d '[:space:]')

# Get current HEAD
CURRENT_SHA=$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null)

# Nothing new if same SHA
if [ "$LAST_SHA" = "$CURRENT_SHA" ]; then
  hook_end "skipped: no new commits"
  exit 0
fi

# Verify the commit range is valid
if ! git -C "$PROJECT_ROOT" log --oneline "$LAST_SHA..$CURRENT_SHA" > /dev/null 2>&1; then
  # If the range is invalid (e.g., force push), reset to current HEAD
  echo "$CURRENT_SHA" > "$SHA_FILE"
  hook_end "skipped: invalid range, reset SHA to $CURRENT_SHA"
  exit 0
fi

# Generate unique agent ID for this run
AGENT_ID=$(generate_agent_id)
AGENT_DIR="$AGENTS_DIR/$AGENT_ID"
mkdir -p "$AGENT_DIR"
AGENT_LOG="$AGENT_DIR/agent.log"

# Spawn background Claude CLI agent for ingest with watchdog
export IS_LLAKE_AGENT=true
export LLAKE_AGENT_ID="$AGENT_ID"

COMMIT_RANGE="${LAST_SHA:0:7}..${CURRENT_SHA:0:7}"

# Build include-based pathspec for agent's git commands (e.g., -- 'src/' 'scripts/' 'package.json')
if [ ${#INCLUDE_PATHS[@]} -gt 0 ]; then
  PATHSPEC_INCLUDE="-- $(printf "'%s' " "${INCLUDE_PATHS[@]}")"
else
  PATHSPEC_INCLUDE=""
fi

# Pre-flight: skip agent if no changes touch included paths
if [ ${#INCLUDE_PATHS[@]} -gt 0 ]; then
  RELEVANT_FILES=$(git -C "$PROJECT_ROOT" diff --name-only "$LAST_SHA".."$CURRENT_SHA" -- "${INCLUDE_PATHS[@]}" 2>/dev/null | head -1)
else
  # No include filter — check all files
  RELEVANT_FILES=$(git -C "$PROJECT_ROOT" diff --name-only "$LAST_SHA".."$CURRENT_SHA" 2>/dev/null | head -1)
fi

if [ -z "$RELEVANT_FILES" ]; then
  echo "$CURRENT_SHA" > "$SHA_FILE"
  hook_end "skipped: no relevant file changes ($COMMIT_RANGE)"
  exit 0
fi

# Log agent launch metadata
cat > "$AGENT_LOG" << EOF
=== LoreLake Ingest Agent: $AGENT_ID ===
Commits: ${LAST_SHA}..${CURRENT_SHA}
Started: $(date '+%Y-%m-%d %H:%M:%S')
Timeout: ${MAX_TIMEOUT_SEC}s
Budget:  \$${MAX_BUDGET_USD}
---
EOF

# Build the ingest prompt via the shared template renderer.
INGEST_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$INGEST_PROMPT_TMPL" \
  "$CONFIG_FILE" \
  "AGENT_ID=$AGENT_ID" \
  "PROJECT_ROOT=$PROJECT_ROOT" \
  "LAST_SHA=$LAST_SHA" \
  "CURRENT_SHA=$CURRENT_SHA" \
  "COMMIT_RANGE=$COMMIT_RANGE" \
  "PATHSPEC_INCLUDE=$PATHSPEC_INCLUDE" \
  "LLAKE_ROOT=$LLAKE_ROOT" \
  "WIKI_ROOT=$WIKI_ROOT" \
  "SCHEMA_DIR=$SCHEMA_DIR")

(
  source "$LIB_DIR/agent-run.sh"
  MY_PID=$(sh -c 'echo $PPID')
  HOOKS_LOG_FILE="$LOG_FILE"
  CURRENT_PID_FILE="$AGENT_DIR/ingest.pid"
  echo "$MY_PID" > "$CURRENT_PID_FILE"
  setup_kill_trap

  # Single watchdog: sends USR1 to the outer subshell, triggering the trap's
  # timeout path for a full tree-kill.
  (
    sleep "$MAX_TIMEOUT_SEC"
    if kill -0 "$MY_PID" 2>/dev/null; then
      kill -USR1 "$MY_PID" 2>/dev/null
    fi
  ) &
  WATCHDOG_PID=$!

  # Start the agent
  claude $MODEL_FLAG $EFFORT_FLAG -p "$INGEST_PROMPT" \
  --allowedTools "$ALLOWED_TOOLS" \
  --max-budget-usd "$MAX_BUDGET_USD" \
  --output-format stream-json --verbose 2>&1 \
  | python3 "$LIB_DIR/format-agent-log.py" >> "$AGENT_LOG" &
  CLAUDE_PID=$!

  # Wait for agent to finish (or be killed)
  wait "$CLAUDE_PID" 2>/dev/null
  EXIT_CODE=$?

  rm -f "$CURRENT_PID_FILE"

  # Kill watchdog if agent finished naturally
  kill "$WATCHDOG_PID" 2>/dev/null
  wait "$WATCHDOG_PID" 2>/dev/null

  # Log completion
  echo "" >> "$AGENT_LOG"
  if [ "$EXIT_CODE" -eq 0 ]; then
    echo "=== COMPLETED: exit 0 at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | completed: agent %s finished (exit 0, commits: %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$COMMIT_RANGE" >> "$LOG_FILE"
  elif [ "$EXIT_CODE" -eq 137 ] || [ "$EXIT_CODE" -eq 143 ]; then
    echo "=== KILLED: exit $EXIT_CODE at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    # Timeout already logged by watchdog
  else
    echo "=== FAILED: exit $EXIT_CODE at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | failed: agent %s (exit %s, commits: %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$EXIT_CODE" "$COMMIT_RANGE" >> "$LOG_FILE"
  fi
) &
disown

hook_end "done: spawned agent $AGENT_ID (commits: $COMMIT_RANGE, timeout: ${MAX_TIMEOUT_SEC}s)"
exit 0
