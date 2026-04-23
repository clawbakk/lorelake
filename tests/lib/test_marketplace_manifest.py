"""Validate .claude-plugin/marketplace.json shape and contents.

Parallels test_plugin_manifest.py. The marketplace manifest is what makes
`/plugin marketplace add clawbakk/lorelake` + `/plugin install lorelake@clawbakk`
resolve; its absence or corruption breaks the documented install flow.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"


def _load():
    return json.loads(MARKETPLACE_PATH.read_text())


def test_marketplace_file_exists():
    assert MARKETPLACE_PATH.is_file(), (
        f"{MARKETPLACE_PATH} must exist for /plugin marketplace add to work"
    )


def test_marketplace_name_is_clawbakk():
    assert _load()["name"] == "clawbakk"


def test_marketplace_has_single_plugin_named_lorelake():
    plugins = _load()["plugins"]
    assert isinstance(plugins, list) and len(plugins) == 1
    assert plugins[0]["name"] == "lorelake"


def test_marketplace_has_owner():
    data = _load()
    assert isinstance(data.get("owner"), dict), "owner must be an object"
    assert data["owner"].get("name"), "owner.name is required"


def test_marketplace_plugin_source_is_repo_root():
    plugins = _load()["plugins"]
    assert plugins[0]["source"] == "./"


def test_marketplace_plugin_name_matches_plugin_json():
    plugin_json = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text()
    )
    marketplace_plugin = _load()["plugins"][0]
    assert marketplace_plugin["name"] == plugin_json["name"]
