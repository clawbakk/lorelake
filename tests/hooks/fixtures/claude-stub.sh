#!/bin/bash
# Test stub for the `claude` CLI. Env vars control behavior.
#
# Single-call mode:
#   STUB_EXIT_CODE   — exit code (default: 0)
#   STUB_DELAY_SEC   — sleep N seconds before exit (default: 0)
#   STUB_STREAM_FILE — path to file emitted on stdout as Claude stream-json
#   STUB_RECORD_FILE — path where invocation args/prompt are appended
#
# Multi-call mode (for two-pass triage→capture tests):
#   STUB_COUNT_FILE   — path to file where per-invocation counter is stored
#   STUB_EXIT_CODES   — comma-separated; Nth call uses Nth code (overrides
#                       STUB_EXIT_CODE for that call). Empty Nth element
#                       falls back to STUB_EXIT_CODE.
#   STUB_STREAM_FILES — comma-separated; Nth call uses Nth stream file
#                       (overrides STUB_STREAM_FILE for that call). Empty
#                       Nth element falls back to STUB_STREAM_FILE.
#
# The stub ignores all actual CLI flags; the surrounding test harness
# controls outcome entirely via env vars.
set -u

# v2-aware mode for ingest pipeline v2 tests.
#   LLAKE_STUB_MODE=ingest-v2-planner | ingest-v2-fixer
#   LLAKE_STUB_PLAN_JSON     — path to a file whose contents become the result
#   LLAKE_STUB_PLAN_INLINE   — literal JSON to emit as the result
#   LLAKE_STUB_PLAN_INLINE_1 — literal JSON for the 1st invocation (planner pass)
#   LLAKE_STUB_PLAN_INLINE_2 — literal JSON for the 2nd invocation (fixer pass)
#   LLAKE_STUB_CALL_COUNTER  — file used to count invocations across calls
#   LLAKE_STUB_SLEEP_SECONDS — sleep N seconds before exiting (for timeout tests)
if [ "${LLAKE_STUB_MODE:-}" = "ingest-v2-planner" ] || [ "${LLAKE_STUB_MODE:-}" = "ingest-v2-fixer" ]; then
  # Multi-call counter — supports different plans for call 1 vs call 2
  COUNTER_FILE="${LLAKE_STUB_CALL_COUNTER:-/tmp/llake-stub-counter-$$}"
  CALL_NUM=$(if [ -f "$COUNTER_FILE" ]; then cat "$COUNTER_FILE"; else echo 0; fi)
  CALL_NUM=$((CALL_NUM + 1))
  echo "$CALL_NUM" > "$COUNTER_FILE"

  PLAN_TEXT=""
  if [ -n "${LLAKE_STUB_PLAN_JSON:-}" ] && [ -f "${LLAKE_STUB_PLAN_JSON:-}" ]; then
    PLAN_TEXT=$(cat "$LLAKE_STUB_PLAN_JSON")
  elif [ "$CALL_NUM" = "1" ] && [ -n "${LLAKE_STUB_PLAN_INLINE_1:-}" ]; then
    PLAN_TEXT="$LLAKE_STUB_PLAN_INLINE_1"
  elif [ "$CALL_NUM" = "2" ] && [ -n "${LLAKE_STUB_PLAN_INLINE_2:-}" ]; then
    PLAN_TEXT="$LLAKE_STUB_PLAN_INLINE_2"
  elif [ -n "${LLAKE_STUB_PLAN_INLINE:-}" ]; then
    PLAN_TEXT="$LLAKE_STUB_PLAN_INLINE"
  elif [ -n "${LLAKE_STUB_PLAN_INLINE_1:-}" ]; then
    PLAN_TEXT="$LLAKE_STUB_PLAN_INLINE_1"
  fi
  # Emit a system init event so format-agent-log shows an INIT line.
  printf '%s\n' '{"type":"system","subtype":"init","session_id":"stub","model":"stub","tools":[]}'
  # Encode plan text as a JSON string, then embed it into both an assistant
  # message and a result event. The result event is what --extract-result reads.
  PLAN_ESCAPED=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$PLAN_TEXT")
  printf '%s\n' "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":$PLAN_ESCAPED}]}}"
  printf '%s\n' "{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"total_cost_usd\":0.10,\"result\":$PLAN_ESCAPED}"
  if [ -n "${LLAKE_STUB_SLEEP_SECONDS:-}" ] && [ "$LLAKE_STUB_SLEEP_SECONDS" -gt 0 ] 2>/dev/null; then
    sleep "$LLAKE_STUB_SLEEP_SECONDS"
  fi
  exit 0
fi

# Resolve which call this is, if multi-call mode is active.
CALL_N=""
if [ -n "${STUB_COUNT_FILE:-}" ]; then
  N=0
  [ -f "$STUB_COUNT_FILE" ] && N=$(cat "$STUB_COUNT_FILE")
  N=$((N + 1))
  echo "$N" > "$STUB_COUNT_FILE"
  CALL_N="$N"
fi

# Resolve effective exit code.
EFFECTIVE_EXIT="${STUB_EXIT_CODE:-0}"
if [ -n "$CALL_N" ] && [ -n "${STUB_EXIT_CODES:-}" ]; then
  CODE_AT_N=$(printf '%s' "$STUB_EXIT_CODES" | cut -d',' -f"$CALL_N")
  [ -n "$CODE_AT_N" ] && EFFECTIVE_EXIT="$CODE_AT_N"
fi

# Resolve effective stream file.
EFFECTIVE_STREAM="${STUB_STREAM_FILE:-}"
if [ -n "$CALL_N" ] && [ -n "${STUB_STREAM_FILES:-}" ]; then
  STREAM_AT_N=$(printf '%s' "$STUB_STREAM_FILES" | cut -d',' -f"$CALL_N")
  [ -n "$STREAM_AT_N" ] && EFFECTIVE_STREAM="$STREAM_AT_N"
fi

if [ -n "${STUB_RECORD_FILE:-}" ]; then
  {
    if [ -n "$CALL_N" ]; then
      echo "=== claude-stub invocation #$CALL_N at $(date '+%Y-%m-%d %H:%M:%S') ==="
    else
      echo "=== claude-stub invocation at $(date '+%Y-%m-%d %H:%M:%S') ==="
    fi
    echo "ARGS: $*"
  } >> "$STUB_RECORD_FILE"
fi

# Extract -p <prompt> for record purposes.
PROMPT=""
saved_args=("$@")
while [ $# -gt 0 ]; do
  if [ "$1" = "-p" ] && [ $# -ge 2 ]; then
    PROMPT="$2"
    break
  fi
  shift
done
set -- "${saved_args[@]}"

if [ -n "${STUB_RECORD_FILE:-}" ]; then
  echo "PROMPT_LEN: ${#PROMPT}" >> "$STUB_RECORD_FILE"
  PROMPT_HEAD=$(printf '%s' "$PROMPT" | head -c 200)
  echo "PROMPT_HEAD: $PROMPT_HEAD" >> "$STUB_RECORD_FILE"
fi

if [ -n "$EFFECTIVE_STREAM" ] && [ -f "$EFFECTIVE_STREAM" ]; then
  cat "$EFFECTIVE_STREAM"
fi

if [ -n "${STUB_DELAY_SEC:-}" ] && [ "$STUB_DELAY_SEC" -gt 0 ] 2>/dev/null; then
  sleep "$STUB_DELAY_SEC"
fi

exit "$EFFECTIVE_EXIT"
