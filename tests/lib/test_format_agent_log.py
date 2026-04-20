"""Smoke test: format-agent-log.py with --allowed-tools surfaces only the listed tools in the INIT line."""
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "format-agent-log.py"


def feed(stream_lines, allowed_tools=""):
    args = ["python3", str(SCRIPT)]
    if allowed_tools:
        args += ["--allowed-tools", allowed_tools]
    result = subprocess.run(args, input="\n".join(stream_lines), capture_output=True, text=True)
    return result.stdout


def test_init_line_filters_to_allowed_tools():
    init_event = json.dumps({
        "type": "system",
        "subtype": "init",
        "model": "claude-opus",
        "tools": ["Read", "Write", "Edit", "Bash", "ToolSearch", "Custom"]
    })
    out = feed([init_event], allowed_tools="Read,Write,Bash")
    assert "Read" in out
    assert "Write" in out
    assert "Bash" in out
    assert "Custom" not in out
    assert "ToolSearch" not in out


def test_init_line_no_filter_shows_all():
    init_event = json.dumps({
        "type": "system",
        "subtype": "init",
        "model": "claude-opus",
        "tools": ["Read", "Custom"]
    })
    out = feed([init_event])
    assert "Read" in out
    assert "Custom" in out
