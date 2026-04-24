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
