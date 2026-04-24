#!/bin/bash
# Test session-end.sh foreground: returns fast, dispatches worker, writes one
# log line. Swaps the real worker for a recorder script.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOKS_DIR="$REPO_ROOT/hooks"
LIB_DIR="$HOOKS_DIR/lib"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Set up a fake project with llake/
PROJ="$TMP/proj"
mkdir -p "$PROJ/llake/.state"
echo '{"_schemaVersion":1,"sessionCapture":{"enabled":true},"logging":{"maxLines":1000,"rotateKeepLines":500}}' > "$PROJ/llake/config.json"

# Swap the worker for a recorder. We create a shim lib dir that overrides
# session-capture-worker.sh only — everything else falls back to the real lib.
SHIM_LIB="$TMP/shim-lib"
mkdir -p "$SHIM_LIB"
for f in "$LIB_DIR"/*.sh "$LIB_DIR"/*.py; do
  ln -s "$f" "$SHIM_LIB/$(basename "$f")"
done
# Replace the worker symlink with a recorder.
rm -f "$SHIM_LIB/session-capture-worker.sh"
cat > "$SHIM_LIB/session-capture-worker.sh" <<EOF
#!/bin/bash
echo "INVOKED: \$1" > "$TMP/worker-was-called"
EOF
chmod +x "$SHIM_LIB/session-capture-worker.sh"

# Run session-end.sh with LIB_DIR overridden to the shim.
# Note: session-end.sh resolves LIB_DIR from its own path, so we invoke via a
# bash wrapper that exports LLAKE_LIB_DIR_OVERRIDE, which the hook should honor.
cd "$PROJ"
START=$(date +%s)
echo '{"cwd":"'"$PROJ"'","session_id":"TEST123","transcript_path":""}' \
  | LLAKE_LIB_DIR_OVERRIDE="$SHIM_LIB" "$HOOKS_DIR/session-end.sh"
END=$(date +%s)
ELAPSED=$(( END - START ))

# Foreground should return in well under 1 second — allow 2s for CI variance.
if [ "$ELAPSED" -gt 2 ]; then
  echo "FAIL: foreground took ${ELAPSED}s (expected < 2s)"
  exit 1
fi

# The worker is detached (nohup &); give it a moment to run.
sleep 1

# Worker should have been invoked with the tempfile.
if [ ! -f "$TMP/worker-was-called" ]; then
  echo "FAIL: worker was not invoked"
  exit 1
fi
ARG_LINE=$(cat "$TMP/worker-was-called")
TMP_INPUT=$(echo "$ARG_LINE" | sed 's/^INVOKED: //')
if [ -z "$TMP_INPUT" ]; then
  echo "FAIL: worker invoked without tempfile arg: '$ARG_LINE'"
  exit 1
fi

# Exactly one 'dispatched (async)' line should have been logged.
LOG="$PROJ/llake/.state/hooks.log"
COUNT=$(grep -c 'dispatched (async)' "$LOG" 2>/dev/null || echo 0)
if [ "$COUNT" -ne 1 ]; then
  echo "FAIL: expected 1 'dispatched (async)' line, got $COUNT"
  cat "$LOG"
  exit 1
fi

echo "PASS: session-end foreground"
