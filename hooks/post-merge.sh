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
# shellcheck source=/dev/null
source "$LIB_DIR/hook-log.sh"

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

hook_start "$HOOK_NAME" "$LOG_FILE" "$CONFIG_FILE" "$LIB_DIR"

# Recursion guard
AGENT_ID="${LLAKE_AGENT_ID:-}"
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${AGENT_ID:-unknown}]" "$LOG_FILE"
  exit 0
fi

# Master toggle
INGEST_ENABLED=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "ingest.enabled")
if [ "$INGEST_ENABLED" = "false" ]; then
  hook_end "skipped: ingest disabled" "$LOG_FILE"
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
  hook_end "skipped: missing ingest prompt file" "$LOG_FILE"
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
  hook_end "skipped: not on $INGEST_BRANCH ($CURRENT_BRANCH)" "$LOG_FILE"
  exit 0
fi

# Read last ingested SHA
if [ ! -f "$SHA_FILE" ]; then
  hook_end "skipped: no last-ingest-sha file" "$LOG_FILE"
  exit 0
fi
LAST_SHA=$(cat "$SHA_FILE" | tr -d '[:space:]')

# Get current HEAD
CURRENT_SHA=$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null)

# Nothing new if same SHA
if [ "$LAST_SHA" = "$CURRENT_SHA" ]; then
  hook_end "skipped: no new commits" "$LOG_FILE"
  exit 0
fi

# Verify the commit range is valid
if ! git -C "$PROJECT_ROOT" log --oneline "$LAST_SHA..$CURRENT_SHA" > /dev/null 2>&1; then
  # If the range is invalid (e.g., force push), reset to current HEAD
  echo "$CURRENT_SHA" > "$SHA_FILE"
  hook_end "skipped: invalid range, reset SHA to $CURRENT_SHA" "$LOG_FILE"
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
  hook_end "skipped: no relevant file changes ($COMMIT_RANGE)" "$LOG_FILE"
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
# Capture stderr separately so we can surface render failures clearly.
RENDER_STDERR_FILE="$AGENT_DIR/render-stderr.tmp"
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
  "SCHEMA_DIR=$SCHEMA_DIR" 2>"$RENDER_STDERR_FILE")
RENDER_EXIT=$?

if [ "$RENDER_EXIT" -ne 0 ] || [ -z "$INGEST_PROMPT" ]; then
  RENDER_ERR=$(cat "$RENDER_STDERR_FILE" 2>/dev/null)
  rm -f "$RENDER_STDERR_FILE"
  log_render_failure "" "$RENDER_EXIT" "$RENDER_ERR" "$AGENT_LOG"
  ERR_SUMMARY=$(render_err_summary "$RENDER_ERR")
  hook_end "skipped: render-prompt failed (agent $AGENT_ID, exit $RENDER_EXIT): $ERR_SUMMARY" "$LOG_FILE"
  exit 0
fi
rm -f "$RENDER_STDERR_FILE"

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

  # Start the agent. Wrap in a subshell so that PIPESTATUS[0] (claude's
  # exit code) is the subshell's exit code — `wait "$CLAUDE_PID"` would
  # otherwise return the formatter's exit code (always 0).
  (
    claude $MODEL_FLAG $EFFORT_FLAG -p "$INGEST_PROMPT" \
    --tools "$ALLOWED_TOOLS" \
    --strict-mcp-config \
    --max-budget-usd "$MAX_BUDGET_USD" \
    --output-format stream-json --verbose 2>&1 \
    | python3 "$LIB_DIR/format-agent-log.py" >> "$AGENT_LOG" 2>&1
    _pstat=("${PIPESTATUS[@]}")
    CLAUDE_EXIT="${_pstat[0]}"
    FORMATTER_EXIT="${_pstat[1]:-0}"
    if [ "$FORMATTER_EXIT" -ne 0 ]; then
      echo "$FORMATTER_EXIT" > "$AGENT_DIR/formatter-exit"
    fi
    exit "$CLAUDE_EXIT"
  ) &
  CLAUDE_PID=$!

  # Wait for agent to finish (or be killed)
  wait "$CLAUDE_PID" 2>/dev/null
  EXIT_CODE=$?

  rm -f "$CURRENT_PID_FILE"

  # Kill watchdog if agent finished naturally
  kill "$WATCHDOG_PID" 2>/dev/null
  wait "$WATCHDOG_PID" 2>/dev/null

  # Detect a formatter crash before normal dispatch. The sidecar is
  # written by the inner subshell only when the formatter exited
  # nonzero — meaning claude likely received SIGPIPE (EXIT_CODE=141).
  # Cursor logic unchanged: this case falls through to FAILED below
  # (no SHA advance), but we log a distinct line so the operator
  # knows it was the formatter, not an external kill.
  FORMATTER_EXIT=0
  if [ -f "$AGENT_DIR/formatter-exit" ]; then
    FORMATTER_EXIT=$(cat "$AGENT_DIR/formatter-exit")
    rm -f "$AGENT_DIR/formatter-exit"
  fi

  # Log completion. SHA advances ONLY on a clean run: render succeeded,
  # agent spawned, agent exited 0. External kills, nonzero exits, and
  # formatter crashes all hold the cursor — the next post-merge will
  # retry the range once the underlying issue is fixed.
  echo "" >> "$AGENT_LOG"
  if [ "$FORMATTER_EXIT" -ne 0 ]; then
    echo "=== FAILED: formatter crashed (exit $FORMATTER_EXIT, agent exit $EXIT_CODE) at $(date '+%Y-%m-%d %H:%M:%S') — see traceback above ===" >> "$AGENT_LOG"
    printf "%s | %-13s | failed: formatter crashed (exit %s, agent exit %s, commits: %s) — cursor held; see agent.log\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$FORMATTER_EXIT" "$EXIT_CODE" "$COMMIT_RANGE" >> "$LOG_FILE"
  elif [ "$EXIT_CODE" -eq 0 ]; then
    echo "$CURRENT_SHA" > "$SHA_FILE"
    echo "=== COMPLETED: exit 0 at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | completed: agent %s finished (exit 0, commits: %s, sha: advanced to %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$COMMIT_RANGE" "${CURRENT_SHA:0:7}" >> "$LOG_FILE"
  elif [ "$EXIT_CODE" -eq 137 ] || [ "$EXIT_CODE" -eq 143 ]; then
    # If the trap in agent-run.sh fired, it already logged to hooks.log and
    # exited the outer subshell — we never reach this branch in that case.
    # So a 137/143 here means the claude process was killed externally
    # (OOM, external SIGKILL, etc.) without our trap firing.
    echo "=== KILLED: exit $EXIT_CODE at $(date '+%Y-%m-%d %H:%M:%S') (external) ===" >> "$AGENT_LOG"
    printf "%s | %-13s | killed: agent %s (exit %s, external, commits: %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$EXIT_CODE" "$COMMIT_RANGE" >> "$LOG_FILE"
  else
    echo "=== FAILED: exit $EXIT_CODE at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | failed: agent %s (exit %s, commits: %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$EXIT_CODE" "$COMMIT_RANGE" >> "$LOG_FILE"
  fi
) &
BG_PID=$!

if [ "${LLAKE_POST_MERGE_SYNC:-}" = "1" ]; then
  # Test/debug mode: wait for the background subshell to finish so callers
  # can assert on the final state of hooks.log, agent.log, and SHA_FILE.
  wait "$BG_PID" 2>/dev/null
else
  disown "$BG_PID"
fi

hook_end "done: spawned agent $AGENT_ID (commits: $COMMIT_RANGE, timeout: ${MAX_TIMEOUT_SEC}s)" "$LOG_FILE"
exit 0
