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
