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
