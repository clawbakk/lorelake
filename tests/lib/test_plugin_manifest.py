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


def test_plugin_has_author_object():
    data = json.loads(PLUGIN_META.read_text())
    author = data.get("author")
    assert isinstance(author, dict), "plugin.json author must be an object"
    assert author.get("name"), "author.name must be non-empty"
    assert author.get("email"), "author.email must be non-empty"
    assert author.get("url"), "author.url must be non-empty"
    assert author["url"].startswith("https://"), (
        f"author.url must be an https URL, got {author['url']!r}"
    )


def test_plugin_has_repo_homepage_bugs():
    data = json.loads(PLUGIN_META.read_text())
    for field in ("repository", "homepage", "bugs"):
        value = data.get(field)
        assert isinstance(value, str) and value.startswith("https://github.com/"), (
            f"plugin.json {field} must be an https://github.com/... URL, got {value!r}"
        )


def test_plugin_has_keywords_including_karpathy():
    data = json.loads(PLUGIN_META.read_text())
    keywords = data.get("keywords")
    assert isinstance(keywords, list) and keywords, "keywords must be a non-empty array"
    for required in ("claude-code", "wiki", "karpathy"):
        assert required in keywords, (
            f"keywords must include {required!r}, got {keywords!r}"
        )


def test_plugin_license_is_mit():
    data = json.loads(PLUGIN_META.read_text())
    assert data.get("license") == "MIT", (
        f"plugin.json license must be 'MIT', got {data.get('license')!r}"
    )


def test_plugin_description_matches_spec():
    """Spec §3.1 specifies the exact marketing-grade description string."""
    data = json.loads(PLUGIN_META.read_text())
    expected = (
        "Compounding per-project wiki maintained by Claude Code on every "
        "session end and every merge."
    )
    assert data.get("description") == expected, (
        f"description drift: expected {expected!r}, got {data.get('description')!r}"
    )
