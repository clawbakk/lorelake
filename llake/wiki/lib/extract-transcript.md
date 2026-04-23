---
title: "extract_transcript.py"
description: "JSONL session reader that samples and writes markdown transcripts with sidecar metadata"
tags: [lib, session-capture, transcript]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[session-end-hook]]"
---

## Overview

`hooks/lib/extract_transcript.py` reads a Claude Code JSONL session file, filters it to the messages a human would consider meaningful, and writes a sampled markdown transcript plus two sidecar files containing turn count and word count. The sidecars are consumed by the session-end hook to decide whether to skip a session as too thin (below `minTurns` or `minWords` thresholds).

The source JSONL file is **never modified**. This script is read-only with respect to the session file.

## CLI Interface

```
python3 extract_transcript.py \
    <jsonl_path> \
    <output_path> \
    <session_id> \
    <head_size> \
    <tail_size> \
    <middle_max> \
    <middle_scale_start> \
    <max_msg_len>
```

All positional arguments are required. There are no optional flags.

| Argument | Type | Description |
|---|---|---|
| `<jsonl_path>` | path | Claude Code session JSONL file (read-only). |
| `<output_path>` | path | Destination for `transcript.md`. Parent dirs are created automatically. |
| `<session_id>` | string | Session identifier; appears in the transcript header. |
| `<head_size>` | int | Number of visible messages to take from the start. |
| `<tail_size>` | int | Number of visible messages to take from the end. |
| `<middle_max>` | int | Maximum number of messages to include from the middle band. |
| `<middle_scale_start>` | int | Visible-message count at which middle sampling begins to scale. |
| `<max_msg_len>` | int | Per-message character cap before `... [truncated]` is appended. |

**Exit codes**:

| Code | Meaning |
|---|---|
| `0` | Transcript written successfully. |
| `1` | JSONL file is empty or unreadable. |
| `2` | File is valid but contains no visible messages (empty/tool-only session). |

**Output files** (always written together on exit 0):

| File | Contents |
|---|---|
| `<output_path>` | Markdown transcript with `## User` / `## Assistant` headings and `[... N messages omitted ...]` gap markers. |
| `<output_path>.turns` | Plain integer: number of user+assistant turns in the sampled window. |
| `<output_path>.words` | Plain integer: total word count across the **full** session (before sampling), used for the thin-session filter. |

## JSONL Format Parsed

Each line is a JSON object. The script handles the two message shapes Claude Code produces:

```json
{"role": "user", "content": "fix the bug"}
{"role": "assistant", "content": [{"type": "text", "text": "Here is the fix."}]}
```

And the nested variant used in some assistant events:

```json
{"type": "assistant", "message": {"role": "assistant", "content": [...]}}
```

Lines that fail JSON parsing are silently skipped.

## Visibility Filter

`is_visible()` decides which messages appear in the transcript. The rules are:

**User messages are visible when:**
- Content is a non-empty string that does not start with `<local-command-`, `<command-name>`, or `<system-reminder` (these are injected harness controls, not human input).
- Content is a list that is NOT composed entirely of `tool_result` blocks.

**Assistant messages are visible when:**
- Content list contains at least one `text` block with non-empty text.
- Content is a non-empty string.

**Always filtered out:**
- `system` type messages (compact boundaries, away summaries).
- `attachment` type messages.
- Tool-result-only user messages (the harness feeding results back to the model).
- Assistant messages that contain only `tool_use` blocks (pure tool calls with no explanatory text).

## Continuation Detection

After long sessions Claude Code compacts context and inserts a summary. The continuation message looks like:

```
"This session is being continued from a previous conversation that ran out of context..."
```

`is_continuation()` identifies these user-role messages by their opening phrase. They pass the visibility filter (so they appear in the transcript) but are flagged separately to enable Path A sampling.

## Sampling Algorithm

The algorithm selects a representative subset when a session has more visible messages than `head_size + tail_size`.

### Path B — No compaction continuations (normal sessions)

1. Always take `head_size` messages from the start and `tail_size` from the end.
2. If the total visible count is `<= middle_scale_start`, head + tail is sufficient — no middle.
3. If `total > middle_scale_start`, compute a middle window size:

```python
scale_range = middle_max * 5
middle_size = min(middle_max, round((n - middle_scale_start) * middle_max / scale_range))
```

The middle window is centered on `n // 2` and capped at `middle_max` messages.

**Example** (from tests): 200 visible, head=10, tail=20, middle_max=30, scale_start=100:
- `middle_size = min(30, round((200-100)*30/150)) = 20`
- Selected = 10 head + 20 middle (indices 90–109) + 20 tail = 50 messages, 2 gap markers.

### Path A — Compaction continuations present

Continuation summaries bridge the compacted gap, so they are surfaced instead of a sampled middle:

1. Take `head_size` messages.
2. Take continuation messages that fall outside the head and tail windows ("gap continuations"). These are capped at `tail_size - 10` to ensure the tail never shrinks below a floor of 10.
3. Shrink the tail by the number of gap continuations actually included.
4. Take the adjusted tail.

**Example** (from tests): 80 visible, one continuation at index 40, head=10, tail=20:
- 1 gap continuation used → tail shrinks to 19.
- Selected = 10 head + 1 continuation + 19 tail = 30 messages.

### Gap Markers

After selection, gaps between non-adjacent selected indices produce markers in the markdown:

```
[... 50 messages omitted ...]
```

## Word Count and Thin-Session Filtering

`count_session_words()` counts words across all messages in the full JSONL (not just the sampled window). It strips system-injected XML blocks (`<system-reminder>`, `<local-command-*>`, `<command-*>`) from user content before counting. Tool-use blocks and tool-result blocks are not counted.

The `.words` sidecar receives this total. The session-end hook reads it and compares against the `minWords` threshold from config. If below threshold, the capture agent is not spawned. The `.turns` sidecar provides the same check against `minTurns`.

## Key Points

- The script never writes to the source JSONL — it is read-only.
- Both sidecars (`.turns`, `.words`) are always written together with the transcript. The hook relies on both being present.
- Word count is over the full session; turn count is over the sampled window only.
- Path A (continuation) sampling eats from the tail budget, but the tail floor of 10 ensures the end of the session is always represented.
- The `middle_scale_start` parameter controls when middle sampling begins; below that threshold a session gets head + tail only. This keeps small sessions clean.
- Per-message truncation (`max_msg_len`) prevents very long messages from bloating the transcript file.

## Code References

| Symbol | Location |
|---|---|
| `read_all_messages(path)` | `hooks/lib/extract_transcript.py:29` |
| `get_content(msg)` | `hooks/lib/extract_transcript.py:47` |
| `is_visible(msg)` | `hooks/lib/extract_transcript.py:55-84` |
| `is_continuation(msg)` | `hooks/lib/extract_transcript.py:87-95` |
| `filter_visible(messages)` | `hooks/lib/extract_transcript.py:98-100` |
| `detect_continuations(visible)` | `hooks/lib/extract_transcript.py:103-105` |
| `sample_messages(...)` | `hooks/lib/extract_transcript.py:108-168` |
| `count_session_words(messages)` | `hooks/lib/extract_transcript.py:226-248` |
| `format_markdown(...)` | `hooks/lib/extract_transcript.py:192-223` |
| `main()` | `hooks/lib/extract_transcript.py:251-293` |
| `SYSTEM_XML_RE` regex | `hooks/lib/extract_transcript.py:22-26` |
| Test suite | `tests/lib/test_extract_transcript.py` |

## See Also

- [[session-end-hook]] — the hook that calls this script and uses the sidecar files for thin-session filtering
