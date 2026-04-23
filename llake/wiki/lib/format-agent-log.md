---
title: "format-agent-log.py"
description: "Converts Claude CLI stream-json output to human-readable traces; --extract-result for callers"
tags: [lib, agent-lifecycle, logging]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[session-end-hook]]"
  - "[[three-writer-model]]"
---

## Overview

`hooks/lib/format-agent-log.py` is a stream processor that converts the `--output-format stream-json` output of `claude -p` into a human-readable execution trace. It is used by every hook that spawns a background agent: the formatted output is written to the per-agent `agent.log` file. When the triage pass needs the agent's classification result (`CAPTURE`, `PARTIAL`, or `SKIP`), it also uses the `--extract-result` flag to capture the agent's final text output to a separate file.

The script reads from stdin and writes to stdout. It has no state between sessions.

## CLI Interface

```
claude -p "..." --output-format stream-json | \
    python3 hooks/lib/format-agent-log.py \
        [--extract-result <path>] \
        [--allowed-tools <comma-separated>]
```

| Flag | Description |
|---|---|
| `--extract-result <path>` | Write the agent's final result text to this file path. Used by callers that need the agent's output programmatically (e.g. triage classification). |
| `--allowed-tools <list>` | Comma-separated tool names to surface in the `INIT` log line. Tools not in this list are omitted from the init summary. Useful for keeping logs focused on the agent's intended tool set. |

**Exit code**: always `0`. Errors in individual stream-json lines are silently skipped.

## What is stream-json Format

When `claude -p` is invoked with `--output-format stream-json`, it emits one JSON object per line to stdout as the agent executes. Each object has a `type` field:

| Event type | When it fires |
|---|---|
| `system` / `subtype: init` | Agent startup: model name, available tools. |
| `assistant` | Each API response: content blocks (text, tool_use, thinking) plus token usage. |
| `tool_result` | After each tool call completes: tool name, result content or error. |
| `result` | Terminal event: total cost, duration, turn count, stop reason, per-model token usage. Also carries the agent's final text output under the `result` key. |
| `system` / `subtype: hook_started` or `hook_response` | Hook lifecycle events if any hooks fire during the agent run. |

## Output Format

Each input event produces one or more log lines in the format `[HH:MM:SS] EVENT_TYPE | details`:

```
[14:23:01] INIT | model=claude-sonnet-4-5 tools=Read,Write,Grep

[14:23:02] === TURN 1 === (in=4821 out=312 cache_read=0 cache_create=4821)
[14:23:02] TEXT | Here is my plan for updating the wiki.
[14:23:02] CALL | Read(/path/to/file.md)
[14:23:02] RESULT | Read → # Existing Page\n\nContent here... [147 chars truncated]

           ---
[14:23:03] CALL | Write(/path/to/output.md (2048 chars))

[14:23:05] === TURN 2 === (in=5133 out=89 cache_read=4821 cache_create=312)
[14:23:05] TEXT | Done. I've updated the page.

[14:23:05] USAGE | model=claude-sonnet-4-5 in=5133 out=89 cache_read=4821 cache_create=312
[14:23:05] DONE | turns=2 cost=$0.0041 duration=3.2s stop=end_turn
```

**Turn detection**: a new `=== TURN N ===` line appears when the token usage in an `assistant` event differs from the previous one — meaning a new API call was made. Within a single API call, multiple content blocks are separated by `---`.

**Tool input summaries** are formatted per tool type:
- `Read` → file path
- `Write` → `file path (N chars)`
- `Edit` → `file path | old: "first 80 chars of old_string"`
- `Glob` → glob pattern
- `Grep` → `"pattern" in path`
- `Bash` → command (truncated to 200 chars)
- All others → JSON-encoded input (truncated to 200 chars)

**Truncation**: text content is truncated at 1000 chars; tool results at 300 chars; thinking blocks at 500 chars.

## --extract-result Behaviour

When `--extract-result <path>` is provided, upon receiving the terminal `result` event the script:

1. Takes `event["result"]` if present and non-empty.
2. Falls back to `last_assistant_text` (the most recent text block seen in any `assistant` event) if `event["result"]` is absent.
3. Writes the text to the specified file path.

The file is written only once, at the `result` event. If neither source has text, nothing is written.

**Triage use case**: the session-end hook runs the triage agent and passes `--extract-result` pointing to a temp file. After the agent exits, the hook reads that file and checks whether it contains `CAPTURE`, `PARTIAL`, or `SKIP` to decide whether to proceed to the full capture pass.

## Callers

| Caller | Uses `--extract-result`? | Purpose |
|---|---|---|
| `hooks/session-end.sh` (triage pass) | Yes | Reads `CAPTURE`/`PARTIAL`/`SKIP` from the triage agent's output. |
| `hooks/session-end.sh` (capture pass) | No | Formats capture agent execution for the `agent.log`. |
| `hooks/post-merge.sh` | No | Formats ingest agent execution for the `agent.log`. |

## Example: Triage Extraction

```bash
TRIAGE_RESULT_FILE="$AGENT_DIR/triage-result.txt"

claude -p "$TRIAGE_PROMPT" \
    --output-format stream-json \
    --allowedTools "Read" \
    | python3 "$LIB/format-agent-log.py" \
        --extract-result "$TRIAGE_RESULT_FILE" \
        --allowed-tools "Read" \
    >> "$AGENT_LOG"

classification=$(cat "$TRIAGE_RESULT_FILE" 2>/dev/null || echo "SKIP")
```

## Key Points

- `--extract-result` is the mechanism that lets shell scripts read an agent's programmatic output without parsing the full log. Only the triage pass uses it; other passes discard the agent's return value.
- Turn detection is based on token-usage changes, not on message boundaries. A single API call that emits multiple content blocks (text + tool call) appears within one turn, separated by `---`.
- `--allowed-tools` affects only the `INIT` log line display — it does not restrict what tools the agent can actually call. It is used to keep the log readable when the real tool list is long.
- The script never buffers the full stream — it processes one line at a time and flushes stdout after each event, so `agent.log` grows in near-real-time during agent execution.
- Malformed JSON lines in the stream are silently discarded, so partial writes from a killed agent do not crash the formatter.

## Code References

| Symbol | Location |
|---|---|
| `format_tool_input(tool_name, tool_input)` | `hooks/lib/format-agent-log.py:21-45` |
| `main()` — INIT handling | `hooks/lib/format-agent-log.py:75-85` |
| `main()` — assistant event / turn detection | `hooks/lib/format-agent-log.py:88-130` |
| `main()` — tool result | `hooks/lib/format-agent-log.py:133-153` |
| `main()` — result / --extract-result | `hooks/lib/format-agent-log.py:156-185` |
| `truncate(text, max_len)` | `hooks/lib/format-agent-log.py:15-18` |
| Test suite | `tests/lib/test_format_agent_log.py` |

## See Also

- [[session-end-hook]] — the hook that uses both the formatting output and `--extract-result` for triage
- [[three-writer-model]] — architectural overview of the three agents whose logs this tool formats
