"""Tests for plugin hook manifest integrity."""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "hooks" / "hooks.json"
PLUGIN_META = REPO_ROOT / ".claude-plugin" / "plugin.json"


def test_manifest_file_exists():
    assert MANIFEST.is_file(), f"hooks/hooks.json must exist at {MANIFEST}"


def test_plugin_metadata_file_exists():
    assert PLUGIN_META.is_file(), f".claude-plugin/plugin.json must exist at {PLUGIN_META}"


def test_plugin_metadata_has_required_fields():
    data = json.loads(PLUGIN_META.read_text())
    assert data.get("name"), "plugin.json must declare a non-empty name"
    assert data.get("version"), "plugin.json must declare a non-empty version"


def test_manifest_declares_both_session_hooks():
    data = json.loads(MANIFEST.read_text())
    hooks = data.get("hooks", {})
    assert "SessionStart" in hooks, "manifest must declare SessionStart"
    assert "SessionEnd" in hooks, "manifest must declare SessionEnd"


def test_every_command_resolves_to_existing_file():
    data = json.loads(MANIFEST.read_text())
    commands = []
    for event in ("SessionStart", "SessionEnd"):
        for block in data.get("hooks", {}).get(event, []):
            for hook in block.get("hooks", []):
                cmd = hook.get("command", "")
                assert cmd, f"empty command in {event} block: {block}"
                commands.append((event, cmd))
    assert commands, "manifest declares no hook commands"
    for event, cmd in commands:
        resolved = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO_ROOT))
        path = Path(resolved)
        assert path.is_file(), (
            f"{event} command resolves to missing file: {cmd} → {path}"
        )
