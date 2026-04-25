#!/bin/bash
# Integration tests for hooks/lib/session-capture-worker.sh.
# Uses claude stub (PATH injection) + LLAKE_SESSION_CAPTURE_SYNC=1.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"

# shellcheck source=fixtures/mkproject.sh
source "$FIXTURES/mkproject.sh"

STUB_BIN=$(mktemp -d -t llake-stub-bin.XXXXXX)
cp "$FIXTURES/claude-stub.sh" "$STUB_BIN/claude"
chmod +x "$STUB_BIN/claude"

STREAM_DIR=$(mktemp -d -t llake-stream.XXXXXX)
cat > "$STREAM_DIR/triage-skip.jsonl" <<'JSON'
{"type":"system","subtype":"init"}
{"type":"result","subtype":"success","result":"SKIP: test fixture forced SKIP classification for session-capture-worker integration test"}
JSON
cat > "$STREAM_DIR/triage-capture.jsonl" <<'JSON'
{"type":"system","subtype":"init"}
{"type":"result","subtype":"success","result":"CAPTURE: test fixture forced CAPTURE classification"}
JSON

PASS=0 ; FAIL=0 ; FAILED_NAMES=()
trap 'rm -rf "$STUB_BIN" "$STREAM_DIR"' EXIT

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label"); echo "  FAIL [$label]: expected '$expected', got '$actual'"
  fi
}
assert_log_grep() {
  local label="$1" log="$2" pattern="$3"
  if [ -f "$log" ] && grep -q "$pattern" "$log"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label"); echo "  FAIL [$label]: '$pattern' not in $log"; [ -f "$log" ] && sed 's/^/    /' "$log"
  fi
}
assert_log_no_grep() {
  local label="$1" log="$2" pattern="$3"
  if [ ! -f "$log" ] || ! grep -q "$pattern" "$log"; then PASS=$((PASS+1))
  else FAIL=$((FAIL+1)); FAILED_NAMES+=("$label"); echo "  FAIL [$label]: unexpected '$pattern' in $log"; sed 's/^/    /' "$log"
  fi
}

# Build a minimal Claude-style JSONL transcript for the worker to extract.
make_fake_transcript() {
  local path="$1"
  cat > "$path" <<'JSONL'
{"role":"user","content":"hello world this is a test conversation about the design of our session capture worker for LoreLake which must survive the capture pipeline and produce a meaningful transcript."}
{"role":"assistant","content":[{"type":"text","text":"acknowledged, thinking through the design with enough words to exceed the minimum threshold for a realistic test case that the worker will accept and process all the way through triage and capture passes."}]}
{"role":"user","content":"great, proceed with the plan and continue generating enough content that the word-count filter does not reject this conversation as thin."}
{"role":"assistant","content":[{"type":"text","text":"continuing with additional substantive content so the minWords threshold of 150 is exceeded by this fixture and the worker advances past the thin-session guard into the agent-spawning path for test purposes."}]}
JSONL
}

run_worker() {
  local proj="$1" session_id="$2" transcript="$3"
  local tmp_input; tmp_input=$(mktemp -t llake-worker-input.XXXXXX)
  cat > "$tmp_input" <<JSON
{"cwd":"$proj","session_id":"$session_id","transcript_path":"$transcript"}
JSON
  PATH="$STUB_BIN:$PATH" \
    LLAKE_SESSION_CAPTURE_SYNC=1 \
    STUB_EXIT_CODE="${STUB_EXIT_CODE:-0}" \
    STUB_STREAM_FILE="${STUB_STREAM_FILE:-}" \
    STUB_RECORD_FILE="${STUB_RECORD_FILE:-}" \
    STUB_COUNT_FILE="${STUB_COUNT_FILE:-}" \
    STUB_EXIT_CODES="${STUB_EXIT_CODES:-}" \
    STUB_STREAM_FILES="${STUB_STREAM_FILES:-}" \
    STUB_DELAY_SEC="${STUB_DELAY_SEC:-0}" \
    bash "$REPO_ROOT/hooks/lib/session-capture-worker.sh" "$tmp_input"
}

test_triage_skip() {
  local proj; proj=$(mkproject)
  local tr="$proj/transcript.jsonl"; make_fake_transcript "$tr"
  STUB_STREAM_FILE="$STREAM_DIR/triage-skip.jsonl" \
    run_worker "$proj" "sess-skip" "$tr"
  assert_log_grep "triage-skip:log" "$proj/llake/.state/hooks.log" "triage SKIP by"
  assert_log_no_grep "triage-skip:no-capture" "$proj/llake/.state/hooks.log" "completed: agent"
  rm -rf "$proj"
}

test_triage_capture_success() {
  local proj; proj=$(mkproject)
  local tr="$proj/transcript.jsonl"; make_fake_transcript "$tr"
  STUB_STREAM_FILE="$STREAM_DIR/triage-capture.jsonl" STUB_EXIT_CODE=0 \
    run_worker "$proj" "sess-cap" "$tr"
  assert_log_grep "capture-success:triage" "$proj/llake/.state/hooks.log" "triage CAPTURE by"
  assert_log_grep "capture-success:completed" "$proj/llake/.state/hooks.log" "completed: agent .*exit 0"
  rm -rf "$proj"
}

test_triage_agent_fails() {
  local proj; proj=$(mkproject)
  local tr="$proj/transcript.jsonl"; make_fake_transcript "$tr"
  STUB_EXIT_CODE=1 run_worker "$proj" "sess-fail" "$tr"
  assert_log_grep "triage-fail:log" "$proj/llake/.state/hooks.log" "triage-failed"
  assert_log_no_grep "triage-fail:no-capture" "$proj/llake/.state/hooks.log" "completed: agent"
  rm -rf "$proj"
}

test_capture_agent_fails() {
  local proj; proj=$(mkproject)
  local tr="$proj/transcript.jsonl"; make_fake_transcript "$tr"
  local count_file; count_file=$(mktemp -t llake-stub-count.XXXXXX)

  # Two successive claude invocations:
  #   #1 (triage):  STUB_STREAM_FILES element 1 = triage-capture stream, exit 0
  #   #2 (capture): no stream override, exit 2
  STUB_COUNT_FILE="$count_file" \
    STUB_STREAM_FILES="$STREAM_DIR/triage-capture.jsonl," \
    STUB_EXIT_CODES="0,2" \
    run_worker "$proj" "sess-capfail" "$tr"

  assert_log_grep "cap-fail:log" "$proj/llake/.state/hooks.log" "failed: agent .*exit 2"

  rm -f "$count_file"
  rm -rf "$proj"
}

test_watchdog_timeout_during_triage() {
  local proj; proj=$(mkproject)
  local tr="$proj/transcript.jsonl"; make_fake_transcript "$tr"

  # Tighten the worker's timeout to 1s so the watchdog fires before the
  # claude stub returns. Stub sleeps 5s during triage.
  python3 -c "
import json, sys
p = sys.argv[1]
with open(p) as f: d = json.load(f)
d['sessionCapture']['timeoutSeconds'] = 1
with open(p, 'w') as f: json.dump(d, f)
" "$proj/llake/config.json"

  STUB_DELAY_SEC=5 STUB_EXIT_CODE=0 \
    run_worker "$proj" "sess-watchdog" "$tr"

  # Watchdog must have fired and logged a timeout in hooks.log.
  assert_log_grep "watchdog:timeout-line" "$proj/llake/.state/hooks.log" "timeout: agent.*phase: triage"

  # No 'completed: agent' line should appear (capture must not have run).
  assert_log_no_grep "watchdog:no-capture" "$proj/llake/.state/hooks.log" "completed: agent"

  # Note: _agent_cleanup intentionally leaves SESSION_DIR intact for
  # post-mortem inspection; lock staleness handles the next-agent takeover.

  rm -rf "$proj"
}

test_triage_failure_no_session_residue() {
  local proj; proj=$(mkproject)
  local tr="$proj/transcript.jsonl"; make_fake_transcript "$tr"

  STUB_EXIT_CODE=1 run_worker "$proj" "sess-tri-fail" "$tr"

  # Triage failure must remove SESSION_DIR (worker's TRIAGE_EXIT branch).
  if [ -d "$proj/llake/.state/sessions/sess-tri-fail" ]; then
    FAIL=$((FAIL+1)); FAILED_NAMES+=("triage-fail:session-residue")
    echo "  FAIL [triage-fail:session-residue]: SESSION_DIR persisted after triage failure"
  else
    PASS=$((PASS+1))
  fi

  rm -rf "$proj"
}

echo "-- triage SKIP";                 test_triage_skip
echo "-- triage CAPTURE + success";    test_triage_capture_success
echo "-- triage agent fails";          test_triage_agent_fails
echo "-- capture agent fails";         test_capture_agent_fails
echo "-- watchdog timeout (triage)";   test_watchdog_timeout_during_triage
echo "-- triage failure no residue";   test_triage_failure_no_session_residue

echo
echo "Passed: $PASS"
echo "Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failing assertions:"
  for n in "${FAILED_NAMES[@]}"; do echo "  - $n"; done
  exit 1
fi
