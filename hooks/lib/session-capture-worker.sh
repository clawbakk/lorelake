#!/bin/bash
# LoreLake session-capture worker. Invoked detached by hooks/session-end.sh.
# Takes one arg: path to a tempfile containing the hook's stdin JSON payload.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_ROOT="$(cd "$HOOKS_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR"
PROMPTS_DIR="$HOOKS_DIR/prompts"
TEMPLATES_DIR="$PLUGIN_ROOT/templates"
SCHEMA_DIR="$PLUGIN_ROOT/schema"

# shellcheck source=/dev/null
source "$LIB_DIR/constants.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/detect-project-root.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/agent-id.sh"
# shellcheck source=/dev/null
source "$LIB_DIR/hook-log.sh"

TMP_INPUT="${1:-}"
if [ -z "$TMP_INPUT" ] || [ ! -f "$TMP_INPUT" ]; then
  exit 0
fi

INPUT=$(cat "$TMP_INPUT")
rm -f "$TMP_INPUT"

CWD=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('cwd',''))" 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('session_id', d.get('sessionId','unknown')))" 2>/dev/null)
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('transcript_path', d.get('transcriptPath','')))" 2>/dev/null)

PROJECT_ROOT=$(detect_project_root "${CWD:-$PWD}" 2>/dev/null) || exit 0

LLAKE_ROOT="$PROJECT_ROOT/$LLAKE_DIR_NAME"
WIKI_ROOT="$LLAKE_ROOT/$WIKI_DIR_NAME"
STATE_DIR="$LLAKE_ROOT/.state"
mkdir -p "$STATE_DIR"
AGENTS_DIR="$STATE_DIR/agents"
SESSIONS_DIR="$STATE_DIR/sessions"
LOG_FILE="$STATE_DIR/hooks.log"
CONFIG_FILE="$LLAKE_ROOT/config.json"

HOOK_NAME="session-capture-worker"

hook_start "$HOOK_NAME" "$LOG_FILE" "$CONFIG_FILE" "$LIB_DIR"

# --- Recursion guard (belt-and-suspenders; foreground already checked) ---
if [ "${IS_LLAKE_AGENT:-}" = "true" ]; then
  hook_end "skipped: recursion guard [${LLAKE_AGENT_ID:-unknown}]" "$LOG_FILE"
  exit 0
fi

# Master toggle
SC_ENABLED=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.enabled")
if [ "$SC_ENABLED" = "false" ]; then
  hook_end "skipped: sessionCapture disabled" "$LOG_FILE"
  exit 0
fi

# Read all session capture settings from config
MAX_BUDGET_USD=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.maxBudgetUsd")
MAX_TIMEOUT_SEC=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.timeoutSeconds")
MIN_TURNS=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.minTurns")
MIN_WORDS=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.minWords")
# Two-pass model config
TRIAGE_MODEL=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.triageModel")
TRIAGE_EFFORT=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.triageEffort")
CAPTURE_MODEL=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.captureModel")
CAPTURE_EFFORT=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.captureEffort")
LOCK_STALENESS=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.lockStalenessSeconds")
ALLOWED_TOOLS_JSON=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.allowedTools")
WRITABLE_CATS_JSON=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.writableCategories")

# Read transcript sampling settings
HEAD_SIZE=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "transcript.headSize")
TAIL_SIZE=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "transcript.tailSize")
MIDDLE_MAX_SIZE=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "transcript.middleMaxSize")
MIDDLE_SCALE_START=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "transcript.middleScaleStart")
MAX_MSG_LEN=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "transcript.maxMessageLength")

# Convert allowedTools JSON array to comma-separated string for --allowedTools flag
ALLOWED_TOOLS=$(python3 -c "import json,sys; print(','.join(json.loads(sys.argv[1])))" "$ALLOWED_TOOLS_JSON" 2>/dev/null || echo "Read,Write,Edit,Glob,Grep,Bash")

# Build writable categories section for agent prompt
WRITABLE_CATS_LIST=$(python3 -c "
import json, sys
cats = json.loads(sys.argv[1])
print('   ' + ', '.join(c + '/' for c in cats))
" "$WRITABLE_CATS_JSON" 2>/dev/null || echo "   discussions/, decisions/, gotchas/, playbook/")

# Check that prompt templates exist
TRIAGE_PROMPT_FILE="$PROMPTS_DIR/triage.md.tmpl"
CAPTURE_PROMPT_FILE="$PROMPTS_DIR/capture.md.tmpl"

if [ ! -f "$TRIAGE_PROMPT_FILE" ] || [ ! -f "$CAPTURE_PROMPT_FILE" ]; then
  hook_end "skipped: missing prompt files (triage or capture)" "$LOG_FILE"
  exit 0
fi

# If no transcript path provided, try to find it from Claude's conversation store
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  CLAUDE_DIR="$HOME/.claude/conversations"
  if [ -d "$CLAUDE_DIR" ] && [ "$SESSION_ID" != "unknown" ]; then
    TRANSCRIPT_PATH=$(find "$CLAUDE_DIR" -name "*${SESSION_ID}*" -type f 2>/dev/null | head -1)
  fi
fi

# If we still don't have a transcript, exit gracefully
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  hook_end "skipped: no transcript found (session: $SESSION_ID)" "$LOG_FILE"
  exit 0
fi

# Generate unique agent ID for this run
AGENT_ID=$(generate_agent_id)
AGENT_DIR="$AGENTS_DIR/$AGENT_ID"
mkdir -p "$AGENT_DIR"
AGENT_LOG="$AGENT_DIR/agent.log"

# --- Session directory ---
SESSION_DIR="$SESSIONS_DIR/$SESSION_ID"
mkdir -p "$SESSION_DIR"

# Extract sampled transcript from full JSONL
TRANSCRIPT_FILE="$SESSION_DIR/transcript.md"

python3 "$LIB_DIR/extract_transcript.py" \
  "$TRANSCRIPT_PATH" "$TRANSCRIPT_FILE" "$SESSION_ID" \
  "$HEAD_SIZE" "$TAIL_SIZE" "$MIDDLE_MAX_SIZE" "$MIDDLE_SCALE_START" "$MAX_MSG_LEN"
EXTRACT_EXIT=$?

if [ ! -f "$TRANSCRIPT_FILE" ]; then
  if [ "$EXTRACT_EXIT" -eq 2 ]; then
    hook_end "skipped: no visible messages [$AGENT_ID] (session: $SESSION_ID)" "$LOG_FILE"
  else
    hook_end "skipped: extraction failed (exit $EXTRACT_EXIT) [$AGENT_ID] (session: $SESSION_ID)" "$LOG_FILE"
  fi
  rm -rf "$SESSION_DIR"
  exit 0
fi

TURN_COUNT=0
if [ -f "$TRANSCRIPT_FILE.turns" ]; then
  TURN_COUNT=$(cat "$TRANSCRIPT_FILE.turns")
  rm -f "$TRANSCRIPT_FILE.turns"
fi

WORD_COUNT=0
if [ -f "$TRANSCRIPT_FILE.words" ]; then
  WORD_COUNT=$(cat "$TRANSCRIPT_FILE.words")
  rm -f "$TRANSCRIPT_FILE.words"
fi

# --- Empty session filter ---
if [ "$TURN_COUNT" -lt "$MIN_TURNS" ]; then
  rm -rf "$SESSION_DIR"
  hook_end "skipped: empty session ($TURN_COUNT turns < $MIN_TURNS) [$AGENT_ID] (session: $SESSION_ID)" "$LOG_FILE"
  exit 0
fi

# --- Word count filter ---
if [ "$WORD_COUNT" -lt "$MIN_WORDS" ]; then
  rm -rf "$SESSION_DIR"
  hook_end "skipped: thin session ($WORD_COUNT words < $MIN_WORDS) [$AGENT_ID]" "$LOG_FILE"
  exit 0
fi

# --- Session lock — deduplication via meta file ---
if [ -f "$SESSION_DIR/meta" ]; then
  # Another agent may already be processing this session
  LOCK_TS=$(grep '^timestamp:' "$SESSION_DIR/meta" 2>/dev/null | awk '{print $2}')
  NOW_TS=$(date +%s)
  if [ -n "$LOCK_TS" ] && [ $(( NOW_TS - LOCK_TS )) -gt "$LOCK_STALENESS" ]; then
    # Stale lock — take over
    rm -f "$SESSION_DIR/meta"
  else
    hook_end "skipped: duplicate (locked by another agent) [$AGENT_ID] (session: $SESSION_ID)" "$LOG_FILE"
    exit 0
  fi
fi

# Write lock metadata
cat > "$SESSION_DIR/meta" << LOCK_META
agent: $AGENT_ID
started: $(date '+%Y-%m-%d %H:%M:%S')
timestamp: $(date +%s)
LOCK_META

# --- Spawn agent with watchdog ---
export IS_LLAKE_AGENT=true
export LLAKE_AGENT_ID="$AGENT_ID"

# Shared log — header
cat > "$AGENT_LOG" << EOF
=== LoreLake Session Capture: $AGENT_ID ===
Session: $SESSION_ID
Started: $(date '+%Y-%m-%d %H:%M:%S')
Timeout: ${MAX_TIMEOUT_SEC}s
Budget:  \$${MAX_BUDGET_USD}
Turns:   $TURN_COUNT
Triage:  $TRIAGE_MODEL ($TRIAGE_EFFORT) → Capture: $CAPTURE_MODEL ($CAPTURE_EFFORT)
---
EOF

(
  source "$LIB_DIR/agent-run.sh"
  MY_PID=$(sh -c 'echo $PPID')
  HOOKS_LOG_FILE="$LOG_FILE"
  setup_kill_trap

  # Single watchdog for the whole session capture (covers both triage and
  # capture phases). Sends USR1 to the outer subshell, which fires the trap's
  # timeout path.
  (
    sleep "$MAX_TIMEOUT_SEC"
    if kill -0 "$MY_PID" 2>/dev/null; then
      kill -USR1 "$MY_PID" 2>/dev/null
    fi
  ) &
  WATCHDOG_PID=$!

  FORMATTER="$LIB_DIR/format-agent-log.py"
  TRIAGE_AGENT_ID="${AGENT_ID}_triage"
  CAPTURE_AGENT_ID="${AGENT_ID}_capture"
  TRIAGE_RESULT_FILE="$SESSION_DIR/triage-result.txt"

  # --- Prepare triage prompt ---
  TRIAGE_RENDER_ERR=$(mktemp -t llake-render.XXXXXX)
  TRIAGE_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
    --templates-dir "$TEMPLATES_DIR" \
    "$PROMPTS_DIR/triage.md.tmpl" \
    "$CONFIG_FILE" \
    "SESSION_DIR=$SESSION_DIR" 2>"$TRIAGE_RENDER_ERR")
  TRIAGE_RENDER_EXIT=$?

  if [ "$TRIAGE_RENDER_EXIT" -ne 0 ] || [ -z "$TRIAGE_PROMPT" ]; then
    TRIAGE_ERR=$(cat "$TRIAGE_RENDER_ERR" 2>/dev/null)
    rm -f "$TRIAGE_RENDER_ERR"
    {
      echo ""
      echo "=== TRIAGE RENDER FAILED: exit $TRIAGE_RENDER_EXIT at $(date '+%Y-%m-%d %H:%M:%S') ==="
      [ -n "$TRIAGE_ERR" ] && echo "$TRIAGE_ERR" || echo "(renderer produced empty prompt with exit $TRIAGE_RENDER_EXIT)"
    } >> "$AGENT_LOG"
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null
    rm -rf "$SESSION_DIR"
    ERR_SUMMARY=$(echo "$TRIAGE_ERR" | head -1 | tr -d '\n' | cut -c1-200)
    [ -z "$ERR_SUMMARY" ] && ERR_SUMMARY="empty prompt"
    printf "%s | %-13s | render-failed: triage prompt (agent %s, exit %s): %s\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$TRIAGE_RENDER_EXIT" "$ERR_SUMMARY" >> "$LOG_FILE"
    exit 0
  fi
  rm -f "$TRIAGE_RENDER_ERR"

  # --- Pass 1: Triage ---
  echo "" >> "$AGENT_LOG"
  echo "=== TRIAGE ($TRIAGE_AGENT_ID) ===" >> "$AGENT_LOG"

  CURRENT_PID_FILE="$AGENT_DIR/triage.pid"
  echo "$MY_PID" > "$CURRENT_PID_FILE"

  IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$TRIAGE_AGENT_ID" \
    claude --model "$TRIAGE_MODEL" --effort "$TRIAGE_EFFORT" \
    -p "$TRIAGE_PROMPT" \
    --allowedTools "Read" \
    --max-budget-usd 0.50 \
    --output-format stream-json --verbose 2>&1 \
    | python3 "$FORMATTER" --extract-result "$TRIAGE_RESULT_FILE" >> "$AGENT_LOG"
  TRIAGE_EXIT=${PIPESTATUS[0]}

  rm -f "$CURRENT_PID_FILE"

  if [ "$TRIAGE_EXIT" -ne 0 ]; then
    echo "" >> "$AGENT_LOG"
    echo "=== TRIAGE FAILED: exit $TRIAGE_EXIT at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | triage-failed: agent %s (exit %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$TRIAGE_AGENT_ID" "$TRIAGE_EXIT" >> "$LOG_FILE"
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null
    rm -rf "$SESSION_DIR"
    exit 0
  fi

  # Parse triage result
  if [ -f "$TRIAGE_RESULT_FILE" ]; then
    CLASSIFICATION=$(head -1 "$TRIAGE_RESULT_FILE" | awk '{print $1}' | tr -d ':' | tr '[:lower:]' '[:upper:]')
    TRIAGE_REASON=$(head -1 "$TRIAGE_RESULT_FILE" | sed 's/^[A-Z]*: *//')
  else
    CLASSIFICATION="CAPTURE"
    TRIAGE_REASON="triage result file missing — defaulting to CAPTURE"
  fi

  # Log triage result
  printf "%s | %-13s | triage %s by %s: %s\n" \
    "$(date '+%Y-%m-%d %H:%M:%S')" "triage-done" "$CLASSIFICATION" "$TRIAGE_AGENT_ID" "$TRIAGE_REASON" >> "$LOG_FILE"

  if [ "$CLASSIFICATION" = "SKIP" ]; then
    echo "" >> "$AGENT_LOG"
    echo "=== SKIPPED: triage SKIP at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null
    rm -rf "$SESSION_DIR"
    exit 0
  fi

  # --- Prepare capture prompt ---
  CAPTURE_RENDER_ERR=$(mktemp -t llake-render.XXXXXX)
  CAPTURE_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
    --templates-dir "$TEMPLATES_DIR" \
    "$PROMPTS_DIR/capture.md.tmpl" \
    "$CONFIG_FILE" \
    "AGENT_ID=$CAPTURE_AGENT_ID" \
    "PROJECT_ROOT=$PROJECT_ROOT" \
    "TRIAGE_CLASSIFICATION=$CLASSIFICATION" \
    "TRIAGE_REASON=$TRIAGE_REASON" \
    "WRITABLE_CATEGORIES=$WRITABLE_CATS_LIST" \
    "LLAKE_ROOT=$LLAKE_ROOT" \
    "WIKI_ROOT=$WIKI_ROOT" \
    "SCHEMA_DIR=$SCHEMA_DIR" \
    "SESSION_DIR=$SESSION_DIR" 2>"$CAPTURE_RENDER_ERR")
  CAPTURE_RENDER_EXIT=$?

  if [ "$CAPTURE_RENDER_EXIT" -ne 0 ] || [ -z "$CAPTURE_PROMPT" ]; then
    CAPTURE_ERR=$(cat "$CAPTURE_RENDER_ERR" 2>/dev/null)
    rm -f "$CAPTURE_RENDER_ERR"
    {
      echo ""
      echo "=== CAPTURE RENDER FAILED: exit $CAPTURE_RENDER_EXIT at $(date '+%Y-%m-%d %H:%M:%S') ==="
      [ -n "$CAPTURE_ERR" ] && echo "$CAPTURE_ERR" || echo "(renderer produced empty prompt with exit $CAPTURE_RENDER_EXIT)"
    } >> "$AGENT_LOG"
    kill "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null
    rm -rf "$SESSION_DIR"
    ERR_SUMMARY=$(echo "$CAPTURE_ERR" | head -1 | tr -d '\n' | cut -c1-200)
    [ -z "$ERR_SUMMARY" ] && ERR_SUMMARY="empty prompt"
    printf "%s | %-13s | render-failed: capture prompt (agent %s, exit %s): %s\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$CAPTURE_RENDER_EXIT" "$ERR_SUMMARY" >> "$LOG_FILE"
    exit 0
  fi
  rm -f "$CAPTURE_RENDER_ERR"

  # --- Pass 2: Capture ---
  echo "" >> "$AGENT_LOG"
  echo "=== CAPTURE ($CAPTURE_AGENT_ID) ===" >> "$AGENT_LOG"

  CURRENT_PID_FILE="$AGENT_DIR/capture.pid"
  echo "$MY_PID" > "$CURRENT_PID_FILE"

  # Wrap in a subshell so PIPESTATUS[0] (claude's exit code) becomes the
  # subshell's exit code. `wait "$CLAUDE_PID"` would otherwise return the
  # formatter's exit code (always 0).
  (
    IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$CAPTURE_AGENT_ID" \
      claude --model "$CAPTURE_MODEL" --effort "$CAPTURE_EFFORT" \
      -p "$CAPTURE_PROMPT" \
      --allowedTools "$ALLOWED_TOOLS" \
      --max-budget-usd "$MAX_BUDGET_USD" \
      --output-format stream-json --verbose 2>&1 \
      | python3 "$FORMATTER" >> "$AGENT_LOG"
    exit ${PIPESTATUS[0]}
  ) &
  CLAUDE_PID=$!

  # Wait for agent to finish (or be killed)
  wait "$CLAUDE_PID" 2>/dev/null
  EXIT_CODE=$?

  rm -f "$CURRENT_PID_FILE"

  # Kill watchdog if agent finished naturally
  kill "$WATCHDOG_PID" 2>/dev/null
  wait "$WATCHDOG_PID" 2>/dev/null

  # Clean up session directory
  rm -rf "$SESSION_DIR"

  # Log completion. See note in post-merge.sh on trap vs external-kill
  # distinction for 137/143: if the trap fired, we would have exited 143
  # already and never reached this block.
  echo "" >> "$AGENT_LOG"
  if [ "$EXIT_CODE" -eq 0 ]; then
    echo "=== COMPLETED: exit 0 at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | completed: agent %s finished (exit 0)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" >> "$LOG_FILE"
  elif [ "$EXIT_CODE" -eq 137 ] || [ "$EXIT_CODE" -eq 143 ]; then
    echo "=== KILLED: exit $EXIT_CODE at $(date '+%Y-%m-%d %H:%M:%S') (external) ===" >> "$AGENT_LOG"
    printf "%s | %-13s | killed: agent %s (exit %s, external)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$EXIT_CODE" >> "$LOG_FILE"
  else
    echo "=== FAILED: exit $EXIT_CODE at $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$AGENT_LOG"
    printf "%s | %-13s | failed: agent %s (exit %s)\n" \
      "$(date '+%Y-%m-%d %H:%M:%S')" "agent-done" "$AGENT_ID" "$EXIT_CODE" >> "$LOG_FILE"
  fi
) </dev/null >/dev/null 2>&1 &
BG_PID=$!

if [ "${LLAKE_SESSION_CAPTURE_SYNC:-}" = "1" ]; then
  wait "$BG_PID" 2>/dev/null
else
  disown
fi

hook_end "done: spawned agent $AGENT_ID (session: $SESSION_ID, turns: $TURN_COUNT, timeout: ${MAX_TIMEOUT_SEC}s)" "$LOG_FILE"
exit 0
