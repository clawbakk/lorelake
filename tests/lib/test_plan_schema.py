"""Tests for plan-schema.py — validates ingest v2 plan.json files."""
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "plan-schema.py"


def run_validator(plan_path):
    cmd = ["python3", str(SCRIPT), str(plan_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def write_plan(tmp_path, plan):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    return p


MINIMAL_VALID_PLAN = {
    "version": "1",
    "skip_reason": None,
    "summary": "trivial",
    "updates": [],
    "creates": [],
    "deletes": [],
    "bidirectional_links": [],
    "log_entry": {
        "operation": "ingest",
        "commit_range": "abc1234..def5678",
        "summary": "trivial",
        "pages_affected": []
    }
}


def test_minimal_valid_plan_passes(tmp_path):
    p = write_plan(tmp_path, MINIMAL_VALID_PLAN)
    rc, out, err = run_validator(p)
    assert rc == 0, f"stderr: {err}"


def test_missing_version_fails(tmp_path):
    plan = dict(MINIMAL_VALID_PLAN)
    del plan["version"]
    p = write_plan(tmp_path, plan)
    rc, _, err = run_validator(p)
    assert rc != 0
    assert "version" in err
