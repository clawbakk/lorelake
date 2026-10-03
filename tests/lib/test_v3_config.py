"""ingest.v3.* defaults (spec §13) and V3Config layering."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "hooks" / "lib"))

from ingest_v3.config import V3Config  # noqa: E402

SPEC_DEFAULTS = {
    "analysisModel": "opus", "analysisEffort": "medium", "analysisBudgetUsd": 10.0,
    "analysisTimeoutSeconds": 1800, "recallPass": "opus", "recallEffort": "medium",
    "recallBudgetUsd": 1.0, "recallTimeoutSeconds": 900, "writerModel": "sonnet",
    "writerEffort": "medium", "writerBudgetUsd": 3.0, "writerTimeoutSeconds": 900,
    "writerConcurrency": 4, "bundleMaxPages": 4, "bundleMaxWeight": 80, "writeMode": "edit",
    "fixRound": "on", "verifierMode": "off", "verifierModel": "sonnet", "verifierEffort": "medium",
    "verifierBudgetUsd": 1.0, "verifierTimeoutSeconds": 600, "cacheTtl": "5m",
    "maxRunBudgetUsd": 40.0, "timeoutSeconds": 3600,
}


def write_config(tmp_path, ingest):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ingest": ingest}))
    return path


@pytest.mark.parametrize("key,value", sorted(SPEC_DEFAULTS.items()))
def test_spec_default(tmp_path, key, value):
    assert V3Config(write_config(tmp_path, {})).get(key) == value


def test_defaults_file_has_exactly_the_spec_keys():
    data = json.loads((REPO_ROOT / "templates" / "config.default.json").read_text())
    keys = {k for k in data["ingest"]["v3"] if not k.startswith("_")}
    assert keys == set(SPEC_DEFAULTS)


def test_user_value_wins_per_key(tmp_path):
    cfg = V3Config(write_config(tmp_path, {"v3": {"writerModel": "opus"}}))
    assert cfg.get("writerModel") == "opus"
    assert cfg.get("writerEffort") == "medium"


def test_unknown_key_raises(tmp_path):
    with pytest.raises(KeyError):
        V3Config(write_config(tmp_path, {})).get("noSuchKnob")


def test_missing_user_config_falls_back(tmp_path):
    assert V3Config(tmp_path / "absent.json").get("cacheTtl") == "5m"


def test_include_layering(tmp_path):
    assert V3Config(write_config(tmp_path, {})).include() == ["src/"]
    assert V3Config(write_config(tmp_path, {"include": ["lib/", "app/"]})).include() == ["lib/", "app/"]


def test_include_rejects_non_list(tmp_path):
    with pytest.raises(ValueError):
        V3Config(write_config(tmp_path, {"include": "src/"})).include()


def test_effective_merges_and_drops_comments(tmp_path):
    eff = V3Config(write_config(tmp_path, {"v3": {"bundleMaxPages": 8}})).effective()
    assert eff["bundleMaxPages"] == 8
    assert eff["writerModel"] == "sonnet"
    assert not any(k.startswith("_") for k in eff)


def test_read_config_cli_sees_v3_defaults(tmp_path):
    out = subprocess.run([sys.executable, str(REPO_ROOT / "hooks" / "lib" / "read-config.py"),
                          str(tmp_path / "none.json"), "ingest.v3.timeoutSeconds"],
                         capture_output=True, text=True)
    assert out.stdout.strip() == "3600"
