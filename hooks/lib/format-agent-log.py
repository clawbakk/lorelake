#!/usr/bin/env python3
"""Format Claude stream-json output into a readable agent execution log.

Reads stream-json lines from stdin, writes a human-readable trace to stdout.
Shows: tool calls (name + input summary), tool results (truncated), assistant
text, cost/usage summary, and errors.
"""
import argparse
import json
import sys
from datetime import datetime


def truncate(text, max_len=500):
    if not text or len(text) <= max_len:
        return text
    return text[:max_len] + f"... [{len(text) - max_len} chars truncated]"


def format_tool_input(tool_name, tool_input):
    """Summarize tool input based on tool type."""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:200]

    if tool_name == "Read":
        return tool_input.get("file_path", "?")
    elif tool_name == "Write":
        path = tool_input.get("file_path", "?")
        content = tool_input.get("content", "")
        preview = truncate(content, 2000)
        total_marker = f" [{len(content)} chars total]" if len(content) > 2000 else ""
        return f"{path} ({len(content)} chars), content: {preview!r}{total_marker}"
    elif tool_name == "Edit":
        path = tool_input.get("file_path", "?")
        old = truncate(tool_input.get("old_string", ""), 500)
        new = truncate(tool_input.get("new_string", ""), 500)
        return f"{path} | old: {old!r} | new: {new!r}"
    elif tool_name == "Glob":
        return tool_input.get("pattern", "?")
    elif tool_name == "Grep":
        pattern = tool_input.get("pattern", "?")
        path = tool_input.get("path", ".")
        return f"{pattern!r} in {path}"
    elif tool_name == "Bash":
        return truncate(tool_input.get("command", "?"), 500)
    else:
        return truncate(json.dumps(tool_input, ensure_ascii=False), 200)


_RESULT_CAPS = {
    "Read": 500,
    "Bash": 2000,
    "Write": 500,
    "Edit": 500,
    "Glob": 1000,
    "Grep": 2000,
}


def result_truncation_cap(tool_name):
    """Per-tool cap for tool-result content in the log. Default 1000."""
    return _RESULT_CAPS.get(tool_name, 1000)


def main():
    parser = argparse.ArgumentParser(description="Format Claude stream-json into readable agent log")
    parser.add_argument('--extract-result', dest='extract_result_path', default=None,
                        help='Write the agent result text to this file path')
    args = parser.parse_args()

    turn = 0
    prev_usage = None  # (in, out, cache_read, cache_create)
    last_assistant_text = ""
    tool_id_to_name = {}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")
        subtype = event.get("subtype", "")
        ts = datetime.now().strftime("%H:%M:%S")

        # --- Init ---
        if event_type == "system" and subtype == "init":
            model = event.get("model", "?")
            tools = event.get("tools", [])
            print(f"[{ts}] INIT | model={model} tools={','.join(tools)}")
            sys.stdout.flush()

        # --- Assistant message (text + tool_use) ---
        elif event_type == "assistant":
            msg = event.get("message", {})
            content_blocks = msg.get("content", [])
            usage = msg.get("usage", {})

            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            current_usage = (in_tok, out_tok, cache_read, cache_create)

            if current_usage != prev_usage:
                # New API call — new turn
                turn += 1
                prev_usage = current_usage
                print(f"\n[{ts}] === TURN {turn} === (in={in_tok} out={out_tok} cache_read={cache_read} cache_create={cache_create})")
            else:
                # Same API call — subseparator
                print(f"           ---")

            for block in content_blocks:
                block_type = block.get("type", "")

                if block_type == "text":
                    text = block.get("text", "").strip()
                    if text:
                        wrapped = truncate(text, 1000)
                        print(f"[{ts}] TEXT | {wrapped}")
                        last_assistant_text = text

                elif block_type == "tool_use":
                    name = block.get("name", "?")
                    tool_input = block.get("input", {})
                    tool_id = block.get("id", "?")
                    tool_id_to_name[tool_id] = name
                    summary = format_tool_input(name, tool_input)
                    print(f"[{ts}] CALL | {name}({summary})")

                elif block_type == "thinking":
                    thinking = block.get("thinking", "").strip()
                    if thinking:
                        print(f"[{ts}] THINK | {truncate(thinking, 500)}")

            sys.stdout.flush()

        # --- Tool result (wrapped inside a user message) ---
        elif event_type == "user":
            msg = event.get("message", {})
            content_blocks = msg.get("content", [])
            if not isinstance(content_blocks, list):
                continue

            for block in content_blocks:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue

                tool_id = block.get("tool_use_id", "")
                tool_name = tool_id_to_name.get(tool_id, "?")
                is_error = block.get("is_error", False)
                raw_content = block.get("content", "")

                # Content may be a string or a list of {type:"text",text:"..."} blocks.
                if isinstance(raw_content, list):
                    parts = []
                    for part in raw_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            parts.append(part)
                    content_text = "\n".join(parts)
                elif isinstance(raw_content, str):
                    content_text = raw_content
                else:
                    content_text = str(raw_content)

                prefix = "ERROR" if is_error else "RESULT"
                cap = result_truncation_cap(tool_name)
                print(f"[{ts}] {prefix} | {tool_name} → {truncate(content_text, cap)}")
            sys.stdout.flush()

        # --- Final result ---
        elif event_type == "result":
            cost = event.get("total_cost_usd", 0)
            duration = event.get("duration_ms", 0)
            num_turns = event.get("num_turns", 0)
            stop_reason = event.get("stop_reason", "?")
            is_error = event.get("is_error", False)
            errors = event.get("errors", [])

            model_usage = event.get("modelUsage", {})
            for model, stats in model_usage.items():
                print(f"\n[{ts}] USAGE | model={model} in={stats.get('inputTokens', 0)} out={stats.get('outputTokens', 0)} "
                      f"cache_read={stats.get('cacheReadInputTokens', 0)} cache_create={stats.get('cacheCreationInputTokens', 0)}")

            status = "ERROR" if is_error else "DONE"
            print(f"[{ts}] {status} | turns={num_turns} cost=${cost:.4f} duration={duration / 1000:.1f}s stop={stop_reason}")

            if errors:
                for err in errors:
                    print(f"[{ts}] ERROR | {err}")

            if args.extract_result_path:
                result_text = event.get("result", "") or last_assistant_text
                if result_text:
                    try:
                        with open(args.extract_result_path, 'w') as f:
                            f.write(result_text)
                    except IOError:
                        pass

            sys.stdout.flush()

        # --- Hook events ---
        elif event_type == "system" and subtype in ("hook_started", "hook_response"):
            hook_name = event.get("hook_name", "?")
            if subtype == "hook_started":
                print(f"[{ts}] HOOK | {hook_name} started")
            else:
                outcome = event.get("outcome", "?")
                print(f"[{ts}] HOOK | {hook_name} → {outcome}")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
