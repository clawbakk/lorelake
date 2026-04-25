"""Tests for hooks/lib/format-agent-log.py.

The formatter must log every event the CLI emits faithfully — no filtering,
no silent drops. These tests assert that contract."""
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "format-agent-log.py"


def feed(stream_lines):
    """Pipe stream-json lines through the formatter, return stdout."""
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        input="\n".join(stream_lines),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"formatter failed: {result.stderr}"
    return result.stdout


def test_init_line_shows_every_tool():
    init_event = json.dumps({
        "type": "system",
        "subtype": "init",
        "model": "claude-opus",
        "tools": ["Read", "Write", "Edit", "Bash", "ToolSearch", "Custom"],
    })
    out = feed([init_event])
    for tool in ["Read", "Write", "Edit", "Bash", "ToolSearch", "Custom"]:
        assert tool in out, f"INIT line missing {tool!r}: {out!r}"


def test_tool_result_is_unwrapped_from_user_envelope():
    """Tool results arrive as type:'user' with tool_result blocks inside."""
    assistant_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use",
                "id": "toolu_01ABC",
                "name": "Read",
                "input": {"file_path": "/tmp/hello.md"},
            }],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    })
    user_event = json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_01ABC",
                "content": "hello world\nfrom the file",
            }],
        },
    })
    out = feed([assistant_event, user_event])
    assert "CALL | Read(/tmp/hello.md)" in out
    assert "RESULT | Read → hello world" in out


def test_tool_result_with_list_content_blocks():
    """Some CLI versions emit content as a list of {type:'text',text:'...'}."""
    assistant_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use",
                "id": "toolu_02XYZ",
                "name": "Bash",
                "input": {"command": "echo hi"},
            }],
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        },
    })
    user_event = json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_02XYZ",
                "content": [{"type": "text", "text": "hi"}],
            }],
        },
    })
    out = feed([assistant_event, user_event])
    assert "RESULT | Bash → hi" in out


def test_tool_result_error_is_flagged():
    assistant_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use", "id": "toolu_03ERR",
                "name": "Read", "input": {"file_path": "/nope"},
            }],
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        },
    })
    user_event = json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result", "tool_use_id": "toolu_03ERR",
                "content": "File does not exist.", "is_error": True,
            }],
        },
    })
    out = feed([assistant_event, user_event])
    assert "ERROR | Read → File does not exist." in out


def _assistant_with_tool_use(tool_id, tool_name, tool_input):
    return json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}],
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        },
    })


def test_edit_call_shows_both_old_and_new():
    event = _assistant_with_tool_use(
        "toolu_edit1", "Edit",
        {"file_path": "/tmp/x.md",
         "old_string": "alpha beta gamma",
         "new_string": "alpha BETA gamma"})
    out = feed([event])
    assert "CALL | Edit(/tmp/x.md" in out
    assert "old:" in out and "alpha beta gamma" in out
    assert "new:" in out and "alpha BETA gamma" in out


def test_write_call_shows_content_preview():
    long_content = "x" * 5000
    event = _assistant_with_tool_use(
        "toolu_write1", "Write",
        {"file_path": "/tmp/y.md", "content": long_content})
    out = feed([event])
    # Path + "(5000 chars)" summary still present
    assert "CALL | Write(/tmp/y.md (5000 chars)" in out
    # Plus a content preview, capped at 2000 chars, with a total-size marker
    assert "content: 'xxxx" in out  # leading content preview
    assert "[3000 chars truncated]" in out


def test_bash_result_untruncated_at_1500_chars():
    assistant = _assistant_with_tool_use(
        "toolu_bash1", "Bash", {"command": "echo big"})
    user = json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "toolu_bash1",
            "content": "y" * 1500,
        }]},
    })
    out = feed([assistant, user])
    assert "y" * 1500 in out  # full output retained, not truncated at 300


def test_read_result_truncated_at_500():
    assistant = _assistant_with_tool_use(
        "toolu_read1", "Read", {"file_path": "/tmp/big.txt"})
    user = json.dumps({
        "type": "user",
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "toolu_read1",
            "content": "r" * 1000,
        }]},
    })
    out = feed([assistant, user])
    # Read results cap at 500 with a truncation marker
    assert "[500 chars truncated]" in out


def test_formatter_skips_malformed_json_lines():
    """Lines that fail json.loads are skipped silently — they are not events."""
    init = json.dumps({"type": "system", "subtype": "init",
                       "model": "x", "tools": ["Read"]})
    out = feed([init, "garbage not json", init])
    assert out.count("INIT |") == 2


def test_formatter_fails_loudly_on_unexpected_event_shape():
    """An unexpected event structure causes nonzero exit and stderr diagnostic."""
    bad_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": ["this string should be a dict block"],
            "usage": {"input_tokens": 1, "output_tokens": 1,
                      "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0},
        },
    })
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        input=bad_event,
        capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        f"expected nonzero exit; got rc={result.returncode}, "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "format-agent-log:" in result.stderr
    assert "unexpected error" in result.stderr.lower()


def test_truncate_handles_none_and_empty():
    """truncate(None) and truncate('') both return the empty string."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fmt_agent_log", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.truncate(None) == ""
    assert mod.truncate("") == ""
    assert mod.truncate("hello") == "hello"
    long_text = "x" * 600
    assert mod.truncate(long_text, 500) == "x" * 500 + "... [100 chars truncated]"


def test_extract_result_writes_file(tmp_path):
    """--extract-result writes the result text to a file."""
    result_path = tmp_path / "result.txt"

    init = json.dumps({"type": "system", "subtype": "init",
                       "model": "x", "tools": []})
    result_event = json.dumps({
        "type": "result",
        "subtype": "success",
        "total_cost_usd": 0,
        "duration_ms": 0,
        "num_turns": 1,
        "stop_reason": "end_turn",
        "result": "CAPTURE: forced classification for test",
    })

    proc = subprocess.run(
        ["python3", str(SCRIPT), "--extract-result", str(result_path)],
        input="\n".join([init, result_event]),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"formatter failed: {proc.stderr}"
    assert result_path.exists(), "result file not written"
    assert result_path.read_text() == "CAPTURE: forced classification for test"


def test_unknown_tool_use_id_falls_back_to_question_mark():
    """A tool_result whose tool_use_id was not seen renders with '?' as tool name."""
    user_event = json.dumps({
        "type": "user",
        "message": {
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_NEVER_SEEN",
                "content": "orphan result content",
            }],
        },
    })
    out = feed([user_event])
    assert "RESULT | ? → orphan result content" in out
