#!/bin/bash
# Test stub for the `claude` CLI. Env vars control behavior:
#   STUB_EXIT_CODE   — exit code to return (default: 0)
#   STUB_DELAY_SEC   — sleep N seconds before exit (default: 0)
#   STUB_STREAM_FILE — path to a file whose contents are emitted on stdout
#                      as if they were Claude's stream-json output
#   STUB_RECORD_FILE — path where invocation args/prompt len are appended
#
# The stub ignores all actual CLI flags; the surrounding test harness
# controls outcome entirely via env vars.
set -u

if [ -n "${STUB_RECORD_FILE:-}" ]; then
  {
    echo "=== claude-stub invocation at $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "ARGS: $*"
  } >> "$STUB_RECORD_FILE"
fi

# Extract -p <prompt> for record purposes
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

if [ -n "${STUB_STREAM_FILE:-}" ] && [ -f "$STUB_STREAM_FILE" ]; then
  cat "$STUB_STREAM_FILE"
fi

if [ -n "${STUB_DELAY_SEC:-}" ] && [ "$STUB_DELAY_SEC" -gt 0 ] 2>/dev/null; then
  sleep "$STUB_DELAY_SEC"
fi

exit "${STUB_EXIT_CODE:-0}"
