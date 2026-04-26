"""Tests for read-config.py."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "read-config.py"
DEFAULTS = REPO_ROOT / "templates" / "config.default.json"


def run(args, env=None):
    cmd = ["python3", str(SCRIPT)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    return result.returncode, result.stdout.strip(), result.stderr.strip()


@pytest.fixture
def user_config(tmp_path):
    """Write a minimal user config and return its path."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "ingest": {"branch": "develop", "maxBudgetUsd": 25.0}
    }))
    return cfg


def test_user_value_wins(user_config):
    rc, out, _ = run([str(user_config), "ingest.branch"])
    assert rc == 0
    assert out == "develop"


def test_default_when_user_omits(user_config):
    rc, out, _ = run([str(user_config), "ingest.timeoutSeconds"])
    assert rc == 0
    assert out == "1200"


def test_boolean_value(user_config):
    rc, out, _ = run([str(user_config), "ingest.enabled"])
    assert rc == 0
    assert out == "true"


def test_array_emitted_as_json(user_config):
    rc, out, _ = run([str(user_config), "ingest.allowedTools"])
    assert rc == 0
    parsed = json.loads(out)
    assert parsed == ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]


def test_unknown_key_returns_empty(user_config):
    rc, out, _ = run([str(user_config), "ingest.notARealKey"])
    assert rc == 0
    assert out == ""


def test_nested_default_present(user_config):
    rc, out, _ = run([str(user_config), "transcript.headSize"])
    assert rc == 0
    assert out == "10"


def test_missing_user_config_falls_back_to_defaults(tmp_path):
    nonexistent = tmp_path / "no-such-file.json"
    rc, out, _ = run([str(nonexistent), "ingest.branch"])
    assert rc == 0
    assert out == "main"


def test_default_pipeline_is_legacy(tmp_path):
    user_cfg = tmp_path / "config.json"; user_cfg.write_text("{}")
    rc, out, _ = run([str(user_cfg), "ingest.pipeline"])
    assert rc == 0
    assert out == "legacy"


def test_v2_planner_model_default(tmp_path):
    user_cfg = tmp_path / "config.json"; user_cfg.write_text("{}")
    rc, out, _ = run([str(user_cfg), "ingest.v2.plannerModel"])
    assert rc == 0
    assert out == "opus"
