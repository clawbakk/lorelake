#!/usr/bin/env python3
"""Extract a sampled transcript from a Claude Code JSONL session file.

Reads the full JSONL, filters to visible messages, detects compaction
continuation summaries, and applies head+middle+tail sampling.

The source JSONL is READ-ONLY — this script never modifies it.

Output: Markdown transcript file + .turns / .words sidecar files.

Usage:
  python3 extract_transcript.py <jsonl_path> <output_path> <session_id> \
    <head_size> <tail_size> <middle_max> <middle_scale_start> <max_msg_len>
"""
import json
import re
import sys
import os
from datetime import datetime

# Matches system-injected XML blocks for word-count cleaning
SYSTEM_XML_RE = re.compile(
    r'<(?:system-reminder|local-command-\w+|command-\w+)[^>]*>.*?'
    r'</(?:system-reminder|local-command-\w+|command-\w+)>',
    re.DOTALL
)


def read_all_messages(path):
    """Read all messages from a JSONL transcript file."""
    messages = []
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        return []
    return messages


def get_content(msg):
    """Extract the content field from a message, handling nested formats."""
    content = msg.get('content', '')
    if not content and 'message' in msg:
        content = msg['message'].get('content', '')
    return content


def is_visible(msg):
    """Return True if the message should appear in the transcript."""
    role = msg.get('role', msg.get('type', ''))
    content = get_content(msg)

    if role in ('user', 'human'):
        if isinstance(content, list):
            return not all(
                isinstance(b, dict) and b.get('type') == 'tool_result'
                for b in content
            )
        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith(('<local-command-', '<command-name>', '<system-reminder')):
                return False
            return bool(stripped)
        return False

    if role == 'assistant':
        if isinstance(content, list):
            return any(
                isinstance(b, dict) and b.get('type') == 'text'
                and b.get('text', '').strip()
                for b in content
            )
        if isinstance(content, str):
            return bool(content.strip())
        return False

    return False


def is_continuation(msg):
    """Return True if this is a post-compaction continuation summary."""
    role = msg.get('role', msg.get('type', ''))
    if role not in ('user', 'human'):
        return False
    content = get_content(msg)
    if isinstance(content, str):
        return content.strip().startswith('This session is being continued')
    return False


def filter_visible(messages):
    """Filter messages to visible ones. Returns list of (original_index, msg)."""
    return [(i, msg) for i, msg in enumerate(messages) if is_visible(msg)]


def detect_continuations(visible):
    """Find continuation messages. Returns set of indices into the visible list."""
    return {i for i, (_, msg) in enumerate(visible) if is_continuation(msg)}


def sample_messages(visible, continuation_indices, head_size, tail_size, middle_max, middle_scale_start):
    """Select messages using head+middle/continuation+tail sampling.

    Path A (continuations exist): head + gap continuations + tail.
    Path B (no continuations): head + scaled middle + tail.

    Returns:
        selected: list of (visible_index, original_index, message) tuples
        gaps: list of (prev_vis_idx, next_vis_idx, omitted_count) for gap markers
    """
    n = len(visible)
    base = head_size + tail_size

    if n <= base:
        return [(i, orig_i, msg) for i, (orig_i, msg) in enumerate(visible)], []

    head_indices = set(range(head_size))
    tail_start = n - tail_size
    tail_indices = set(range(tail_start, n))

    if continuation_indices:
        # Path A: continuations bridge the gap
        gap_continuations = continuation_indices - head_indices - tail_indices

        tail_floor = 10
        max_gap_conts = tail_size - tail_floor
        if len(gap_continuations) > max_gap_conts:
            sorted_gap = sorted(gap_continuations)
            gap_continuations = set(sorted_gap[-max_gap_conts:])

        actual_tail_size = tail_size - len(gap_continuations)
        tail_indices = set(range(n - actual_tail_size, n))
        selected_indices = sorted(head_indices | gap_continuations | tail_indices)
    else:
        # Path B: no compaction — use middle sampling for large sessions
        if n <= middle_scale_start:
            selected_indices = sorted(head_indices | tail_indices)
        else:
            scale_range = middle_max * 5
            middle_size = min(middle_max, round((n - middle_scale_start) * middle_max / scale_range))

            if middle_size > 0:
                center = n // 2
                mid_start = center - middle_size // 2
                mid_end = mid_start + middle_size
                middle_indices = set(range(mid_start, mid_end))
                selected_indices = sorted(head_indices | middle_indices | tail_indices)
            else:
                selected_indices = sorted(head_indices | tail_indices)

    # Build result with gap tracking
    result = []
    gaps = []
    prev_idx = -1
    for idx in selected_indices:
        if prev_idx >= 0 and idx > prev_idx + 1:
            gaps.append((prev_idx, idx, idx - prev_idx - 1))
        result.append((idx, visible[idx][0], visible[idx][1]))
        prev_idx = idx

    return result, gaps


def extract_text(msg, max_len):
    """Extract and truncate text content from a message."""
    content = get_content(msg)

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                text_parts.append(block.get('text', ''))
            elif isinstance(block, str):
                text_parts.append(block)
        content = '\n'.join(p for p in text_parts if p)
    elif not isinstance(content, str):
        content = str(content) if content else ''

    if len(content) > max_len:
        content = content[:max_len] + '\n... [truncated]'

    return content


def format_markdown(selected, gaps, session_id, max_msg_len):
    """Format selected messages as markdown with gap markers."""
    lines = [
        f"# Session Transcript: {session_id}",
        "",
        f"Extracted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ]

    turn_count = 0
    gap_dict = {after: omitted for _, after, omitted in gaps}

    for vis_idx, orig_idx, msg in selected:
        if vis_idx in gap_dict:
            lines.append(f"[... {gap_dict[vis_idx]} messages omitted ...]\n")

        role = msg.get('role', msg.get('type', ''))
        text = extract_text(msg, max_msg_len)

        if not text.strip():
            continue

        if role in ('user', 'human'):
            lines.append(f"## User\n\n{text}\n")
            turn_count += 1
        elif role == 'assistant':
            lines.append(f"## Assistant\n\n{text}\n")
            turn_count += 1

    return '\n'.join(lines), turn_count


def count_session_words(messages):
    """Count user + assistant words across ALL messages (before sampling).

    Used for the thin-session filter — counts words in the full session,
    not just the sampled window.
    """
    total = 0
    for msg in messages:
        role = msg.get('role', msg.get('type', ''))
        content = get_content(msg)
        if role in ('user', 'human'):
            if isinstance(content, str):
                cleaned = SYSTEM_XML_RE.sub('', content).strip()
                if cleaned:
                    total += len(cleaned.split())
        elif role == 'assistant':
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        text = block.get('text', '').strip()
                        if text:
                            total += len(text.split())
    return total


def main():
    if len(sys.argv) != 9:
        print(
            f"Usage: {sys.argv[0]} <jsonl> <output> <session_id> "
            "<head> <tail> <mid_max> <mid_start> <max_msg_len>",
            file=sys.stderr
        )
        sys.exit(1)

    jsonl_path = sys.argv[1]
    output_file = sys.argv[2]
    session_id = sys.argv[3]
    head_size = int(sys.argv[4])
    tail_size = int(sys.argv[5])
    middle_max = int(sys.argv[6])
    middle_scale_start = int(sys.argv[7])
    max_msg_len = int(sys.argv[8])

    all_messages = read_all_messages(jsonl_path)
    if not all_messages:
        sys.exit(1)  # no messages in JSONL

    word_count = count_session_words(all_messages)
    visible = filter_visible(all_messages)

    if not visible:
        sys.exit(2)  # messages exist but none are visible (empty session)

    continuation_idx = detect_continuations(visible)
    selected, gaps = sample_messages(
        visible, continuation_idx,
        head_size, tail_size, middle_max, middle_scale_start
    )

    markdown, turn_count = format_markdown(selected, gaps, session_id, max_msg_len)

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write(markdown)
    with open(output_file + '.turns', 'w') as f:
        f.write(str(turn_count))
    with open(output_file + '.words', 'w') as f:
        f.write(str(word_count))


if __name__ == '__main__':
    main()
