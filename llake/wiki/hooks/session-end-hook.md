---
title: "Session End Hook"
description: "SessionEnd foreground dispatcher (~10ms) that hands off to the detached two-pass triage→capture worker"
tags: [hooks, session-end, capture, triage, two-pass]
created: 2026-04-23
updated: 2026-09-20
status: current
related:
  - "[[three-writer-model]]"
  - "[[triage-template]]"
  - "[[capture-template]]"
  - "[[extract-transcript]]"
  - "[[format-agent-log]]"
  - "[[agent-run]]"
  - "[[agent-id]]"
  - "[[detect-project-root]]"
  - "[[is-llake-agent-guard]]"
  - "[[adr-002-two-pass-triage]]"
  - "[[config-schema]]"
  - "[[session-capture-worker]]"
  - "[[hook-log]]"
  - "[[claude-p-tools-flag]]"
---

## Overview

`hooks/session-end.sh` fires when a Claude Code session ends. It is the entry point of the **capture writer**, which extracts the session transcript, runs a cheap triage agent to decide whether the session is worth capturing, and — only if the answer is yes — runs a full capture agent that writes wiki pages and discussion entries.

The script itself is now only a **foreground dispatcher**. It does five things in pure bash — marker-walk for the project root, check the recursion guard, persist stdin to a tempfile, write one `hooks.log` line, and `nohup` the worker — then exits. There are no `python3` calls in it at all. Everything described in the walkthrough below runs in `hooks/lib/session-capture-worker.sh`, documented at [[session-capture-worker]].

The split exists for latency. The hook previously made roughly twenty sequential `python3` calls in the foreground (config reads, transcript extraction, agent dispatch) even though the agents themselves were already detached, costing 1.5–3s of visible delay on every `/exit` and `/clear`. The foreground is now about 10ms.

If this hook were removed or broken, no session knowledge would ever flow into the wiki automatically. The wiki would only grow via `/llake-bootstrap` (initial population) and the post-merge ingest (code changes). Conversational decisions, gotchas surfaced in chat, and architectural reasoning discussed with Claude would be lost.

## Registration

Registered in `hooks/hooks.json` under `SessionEnd`:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-end.sh" }
        ]
      }
    ]
  }
}
```

Claude Code passes session metadata (including `cwd`, `session_id`, and `transcript_path`) as JSON on stdin.

## Two-pass design

The hook runs two sequential agents inside a single background subshell. See [[adr-002-two-pass-triage]] for the rationale.

```
session ends
    │
    ▼
extract transcript (extract_transcript.py)
    │
    ├─ thin session? (< minTurns or < minWords) → SKIP, clean up
    │
    ▼
Pass 1: triage agent (cheap — Read-only, $0.50 cap, triageModel/triageEffort)
    │
    ├─ SKIP → clean up session dir, exit
    │
    └─ CAPTURE or PARTIAL
           │
           ▼
Pass 2: capture agent (full — all allowed tools, maxBudgetUsd cap, captureModel/captureEffort)
           │
           ▼
      clean up session dir, log completion
```

The triage agent is intentionally constrained: `--allowedTools Read`, hard-coded `--max-budget-usd 0.50`. It reads the transcript and outputs a single-line classification: `CAPTURE: <reason>`, `PARTIAL: <reason>`, or `SKIP: <reason>`. This keeps background cost bounded even at high session volume — only sessions with genuine knowledge pass to the expensive capture agent.

## Foreground dispatcher

```bash
PROJECT_ROOT=$(detect_project_root "$PWD" 2>/dev/null) || exit 0
# ... recursion guard ...
TMP_INPUT=$(mktemp -t llake-session-end.XXXXXX)
cat > "$TMP_INPUT"
hook_log_line "session-end" "dispatched (async)" "$LOG_FILE"
nohup bash "$LIB_DIR/session-capture-worker.sh" "$TMP_INPUT" </dev/null >/dev/null 2>&1 &
disown $!
```

Notes on the ordering and the two env hooks:

- **stdin is persisted before the log line is written.** The hook must consume its stdin pipe before anything else can block on it; the worker then reads the tempfile and deletes it.
- `hook_log_line` is used rather than the `hook_start`/`hook_end` pair, because the foreground has no paired outcome to report — it only records `dispatched (async)`. The worker opens its own `hook_start` line under the name `session-capture-worker`. See [[hook-log]].
- `LLAKE_SESSION_END_SYNC=1` makes the hook `exec` the worker instead of forking it, forcing synchronous execution for debugging and for tests that assert on final state.
- `LLAKE_LIB_DIR_OVERRIDE` is a test-only seam that lets `tests/hooks/test_session_end_foreground.sh` swap in a recorder worker and assert on what the foreground passed it.

The recursion guard fires in the foreground (`hooks/session-end.sh:39-42`) *and* again in the worker — belt and suspenders, since the worker can also be invoked directly.

## Step-by-step walkthrough (runs in the worker)

### 1. Parse stdin

```bash
INPUT=$(cat)
CWD=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('cwd',''))" 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('session_id', d.get('sessionId','unknown')))" 2>/dev/null)
TRANSCRIPT_PATH=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('transcript_path', d.get('transcriptPath','')))" 2>/dev/null)
```

Both camelCase and snake_case field names are handled for forward compatibility.

### 2. Project-root detection

```bash
PROJECT_ROOT=$(detect_project_root "${CWD:-$PWD}" 2>/dev/null) || exit 0
```

Uses [[detect-project-root]] (env override → marker walk for `llake/config.json`). Exits silently if no install found.

### 3. Recursion guard

```bash
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${AGENT_ID:-unknown}]"
  exit 0
fi
```

Background agents spawned by this hook run `claude -p` with `IS_LLAKE_AGENT=true` in their environment. When those agents' sessions end, `SessionEnd` fires again — this guard prevents the hook from processing the agent's own session. See [[is-llake-agent-guard]] for the full guard pattern.

### 4. Master toggle

```bash
SC_ENABLED=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "sessionCapture.enabled")
if [ "$SC_ENABLED" = "false" ]; then
  hook_end "skipped: sessionCapture disabled"
  exit 0
fi
```

`sessionCapture.enabled` (from [[config-schema]]) lets users disable the feature entirely without uninstalling the hook.

### 5. Config reads

All tunable parameters are read from `config.json` (with fallback to `config.default.json`) via `read-config.py`:

| Config key | Purpose |
|---|---|
| `sessionCapture.maxBudgetUsd` | USD cap on the capture agent |
| `sessionCapture.timeoutSeconds` | Watchdog timeout covering both passes |
| `sessionCapture.minTurns` | Minimum turn count; thinner sessions are skipped |
| `sessionCapture.minWords` | Minimum word count; thinner sessions are skipped |
| `sessionCapture.triageModel` / `triageEffort` | Model and effort for triage pass |
| `sessionCapture.captureModel` / `captureEffort` | Model and effort for capture pass |
| `sessionCapture.allowedTools` | JSON array; converted to comma-separated string for `--allowedTools` |
| `sessionCapture.writableCategories` | Categories the capture agent may write to |
| `sessionCapture.lockStalenessSeconds` | Age after which a session lock is considered stale |
| `transcript.headSize` / `tailSize` / `middleMaxSize` | Sampling window sizes for transcript extraction |

### 6. Transcript extraction

If `transcript_path` is missing or the file doesn't exist, the hook attempts to find the transcript in `~/.claude/conversations/` by matching against `session_id`.

The actual extraction is done by [[extract-transcript]] (`hooks/lib/extract_transcript.py`):

```bash
python3 "$LIB_DIR/extract_transcript.py" \
  "$TRANSCRIPT_PATH" "$TRANSCRIPT_FILE" "$SESSION_ID" \
  "$HEAD_SIZE" "$TAIL_SIZE" "$MIDDLE_MAX_SIZE" "$MIDDLE_SCALE_START" "$MAX_MSG_LEN"
```

The extractor writes:
- `<session-dir>/transcript.md` — the sampled markdown transcript
- `<session-dir>/transcript.md.turns` — turn count (integer)
- `<session-dir>/transcript.md.words` — word count (integer)

Exit code 2 means no visible messages were found (e.g., empty session or compacted-only content).

### 7. Thin-session filter

```bash
if [ "$TURN_COUNT" -lt "$MIN_TURNS" ]; then
  rm -rf "$SESSION_DIR"
  hook_end "skipped: empty session ($TURN_COUNT turns < $MIN_TURNS) [$AGENT_ID] (session: $SESSION_ID)"
  exit 0
fi

if [ "$WORD_COUNT" -lt "$MIN_WORDS" ]; then
  rm -rf "$SESSION_DIR"
  hook_end "skipped: thin session ($WORD_COUNT words < $MIN_WORDS) [$AGENT_ID]"
  exit 0
fi
```

Two thresholds are applied before any agent is spawned: a turn count minimum and a word count minimum. Sessions below either threshold are discarded and their session directory is cleaned up immediately.

### 8. Session lock

```bash
if [ -f "$SESSION_DIR/meta" ]; then
  LOCK_TS=$(grep '^timestamp:' "$SESSION_DIR/meta" 2>/dev/null | awk '{print $2}')
  NOW_TS=$(date +%s)
  if [ -n "$LOCK_TS" ] && [ $(( NOW_TS - LOCK_TS )) -gt "$LOCK_STALENESS" ]; then
    rm -f "$SESSION_DIR/meta"
  else
    hook_end "skipped: duplicate (locked by another agent) [$AGENT_ID] (session: $SESSION_ID)"
    exit 0
  fi
fi
```

A `meta` file in the session directory acts as a lock. If the file exists and is not stale (age < `lockStalenessSeconds`), the hook exits — another agent is already processing this session. Stale locks are removed and the current run takes over.

The lock file contains:

```
agent: <agent-id>
started: YYYY-MM-DD HH:MM:SS
timestamp: <unix-epoch>
```

### 9. Background subshell

```bash
(
  source "$LIB_DIR/agent-run.sh"
  MY_PID=$(sh -c 'echo $PPID')
  HOOKS_LOG_FILE="$LOG_FILE"
  setup_kill_trap
  ...
) </dev/null >/dev/null 2>&1 &
disown
```

Everything from here runs in a detached subshell. `</dev/null >/dev/null 2>&1` severs all stdio from the parent. `disown` removes it from the shell's job table so it survives the parent process exiting.

See [[agent-run]] for the `setup_kill_trap` / watchdog pattern.

### 10. Single watchdog

A single watchdog subshell covers both triage and capture passes:

```bash
(
  sleep "$MAX_TIMEOUT_SEC"
  if kill -0 "$MY_PID" 2>/dev/null; then
    kill -USR1 "$MY_PID" 2>/dev/null
  fi
) &
WATCHDOG_PID=$!
```

After `timeoutSeconds`, it sends `USR1` to the outer subshell's PID. `setup_kill_trap` binds `USR1` → timeout path, which tree-kills all descendants and logs the timeout. The capture agent can be killed mid-run; partial wiki writes are possible but the session directory is always cleaned up.

### 11. Triage pass (Pass 1)

```bash
IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$TRIAGE_AGENT_ID" \
  claude --model "$TRIAGE_MODEL" --effort "$TRIAGE_EFFORT" \
  -p "$TRIAGE_PROMPT" \
  --tools "Read" \
  --allowedTools "Read" \
  --strict-mcp-config \
  --max-budget-usd 0.50 \
  --output-format stream-json --verbose 2>&1 \
  | python3 "$FORMATTER" --extract-result "$TRIAGE_RESULT_FILE" >> "$AGENT_LOG"
```

Key constraints:
- `--tools "Read"` — the triage agent cannot write anything. `--allowedTools` is passed too, but it is `--tools` that actually restricts the built-in set in `-p` mode, and `--strict-mcp-config` that keeps ambient MCP servers out. See [[claude-p-tools-flag]].
- `--max-budget-usd 0.50` — hard-coded cap, not configurable; triage must be cheap.
- Output piped through [[format-agent-log]] with `--extract-result` to save the final text response to `triage-result.txt`.

The classification is read from the first line of `triage-result.txt`:

```bash
CLASSIFICATION=$(head -1 "$TRIAGE_RESULT_FILE" | awk '{print $1}' | tr -d ':' | tr '[:lower:]' '[:upper:]')
TRIAGE_REASON=$(head -1 "$TRIAGE_RESULT_FILE" | sed 's/^[A-Z]*: *//')
```

If the result file is missing (triage agent crashed or was killed), the hook defaults to `CAPTURE` rather than silently dropping the session.

### 12. Capture pass (Pass 2)

Only runs when `CLASSIFICATION` is `CAPTURE` or `PARTIAL`:

```bash
IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$CAPTURE_AGENT_ID" \
  claude --model "$CAPTURE_MODEL" --effort "$CAPTURE_EFFORT" \
  -p "$CAPTURE_PROMPT" \
  --tools "$ALLOWED_TOOLS" \
  --allowedTools "$ALLOWED_TOOLS" \
  --strict-mcp-config \
  --max-budget-usd "$MAX_BUDGET_USD" \
  --output-format stream-json --verbose 2>&1 \
  | python3 "$FORMATTER" >> "$AGENT_LOG" &
CLAUDE_PID=$!
```

Variables passed to the capture prompt template include: `AGENT_ID`, `PROJECT_ROOT`, `TRIAGE_CLASSIFICATION`, `TRIAGE_REASON`, `WRITABLE_CATEGORIES`, `LLAKE_ROOT`, `WIKI_ROOT`, `SCHEMA_DIR`, `SESSION_DIR`. See [[capture-template]] for how these are used.

### 13. Cleanup

After the capture agent exits (or is killed):

```bash
kill "$WATCHDOG_PID" 2>/dev/null
wait "$WATCHDOG_PID" 2>/dev/null
rm -rf "$SESSION_DIR"
```

The session directory (`llake/.state/sessions/<session-id>/`) is always removed after capture completes. The agent directory (`llake/.state/agents/<agent-id>/`) is retained for log inspection.

## Runtime state layout

```
<project>/llake/.state/
  hooks.log                           # one line per hook invocation (start + outcome)
  agents/<agent-id>/
    agent.log                         # full triage + capture trace (format-agent-log output)
    triage.pid                        # PID file during triage (removed after triage exits)
    capture.pid                       # PID file during capture (removed after capture exits)
  sessions/<session-id>/
    transcript.md                     # sampled transcript (removed after capture)
    triage-result.txt                 # triage classification line (removed after capture)
    meta                              # lock file (removed after capture)
```

The `sessions/` directory is transient — entries are cleaned up regardless of outcome.

## hooks.log format

Every hook invocation appends a line:

```
2026-04-23 14:05:01 | session-end   | started → done: spawned agent witty-fox-140501 (session: abc123, turns: 18, timeout: 120s)
2026-04-23 14:05:03 | triage-done   | triage CAPTURE by witty-fox-140501_triage: session covers new API design decision
2026-04-23 14:08:17 | agent-done    | completed: agent witty-fox-140501 finished (exit 0)
```

If the hook process is killed mid-line (e.g., system shutdown), the next hook invocation detects the unterminated line and appends ` → CRASHED` before starting its own entry.

## Key Points

- Two-pass design: cheap triage (Read-only, $0.50 cap) gates the expensive capture agent.
- Background execution: detached with `disown`; the user's session closes immediately.
- Thin-session filter: sessions below `minTurns` or `minWords` are discarded before any agent runs.
- Single watchdog covers both passes; timeout triggers a tree-kill.
- Recursion guard (`IS_LLAKE_AGENT=true`) prevents agent sessions from re-triggering capture.
- Session lock (`meta` file) prevents duplicate processing if `SessionEnd` fires twice.
- Triage defaults to `CAPTURE` if the result file is missing (safe-open failure mode).
- All session state lives under `.state/sessions/<id>/` and is cleaned up after capture.
- Agent logs are retained under `.state/agents/<id>/agent.log` for post-hoc inspection.
- The script is a ~40-line pure-bash dispatcher; the two-pass logic lives in [[session-capture-worker]].
- Foreground time is ~10ms; it was 1.5–3s before the split, which users saw as a delay on `/exit` and `/clear`.
- stdin must be consumed into a tempfile before anything else — the worker reads and deletes that file.
- `LLAKE_SESSION_END_SYNC=1` runs the worker synchronously; `LLAKE_LIB_DIR_OVERRIDE` is the test seam.

## Code References

- `hooks/session-end.sh:1-60` — the whole foreground dispatcher
- `hooks/session-end.sh:31` — marker-walk project-root detection
- `hooks/session-end.sh:39-42` — foreground recursion guard
- `hooks/session-end.sh:45-49` — stdin persisted to a tempfile, then the dispatch log line
- `hooks/session-end.sh:52-58` — `LLAKE_SESSION_END_SYNC` escape hatch and the `nohup`/`disown` fork
- `hooks/lib/session-capture-worker.sh:49-55` — worker `hook_start` and its own recursion guard
- `hooks/lib/session-capture-worker.sh:131-170` — transcript extraction and thin-session filters
- `hooks/lib/session-capture-worker.sh:173-191` — session lock
- `hooks/lib/session-capture-worker.sh:269-277` — triage invocation
- `hooks/lib/session-capture-worker.sh:361-378` — capture invocation
- `tests/hooks/test_session_end_foreground.sh` — foreground dispatch and recursion-guard tests
- `tests/hooks/test_session_capture_worker.sh` — worker happy path, timeout, render-failure, and flag assertions
- `hooks/hooks.json` — `SessionEnd` registration

## See Also

- [[triage-template]] — the prompt template the triage agent receives
- [[capture-template]] — the prompt template the capture agent receives
- [[extract-transcript]] — the Python extractor that produces `transcript.md`
- [[format-agent-log]] — formats `stream-json` output; `--extract-result` extracts triage classification
- [[agent-run]] — `setup_kill_trap` and watchdog pattern used in the background subshell
- [[agent-id]] — how the readable agent ID (`witty-fox-140501`) is generated
- [[detect-project-root]] — project-root detection algorithm
- [[is-llake-agent-guard]] — the recursion guard pattern
- [[adr-002-two-pass-triage]] — decision record explaining why two passes instead of one
- [[config-schema]] — all `sessionCapture.*` and `transcript.*` config keys
