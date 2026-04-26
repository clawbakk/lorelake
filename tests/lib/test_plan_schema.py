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


def with_update(slug, ops):
    plan = json.loads(json.dumps(MINIMAL_VALID_PLAN))
    plan["updates"] = [{"slug": slug, "rationale": "r", "ops": ops}]
    plan["log_entry"]["pages_affected"] = [slug]
    return plan


def test_invalid_slug_rejected(tmp_path):
    plan = with_update("Bad Slug!", [{"op": "body_replace", "content": "x"}])
    p = write_plan(tmp_path, plan)
    rc, _, err = run_validator(p)
    assert rc != 0
    assert "slug" in err.lower()


def test_unknown_op_type_rejected(tmp_path):
    plan = with_update("good-slug", [{"op": "frobnicate", "find": "x", "with": "y"}])
    p = write_plan(tmp_path, plan)
    rc, _, err = run_validator(p)
    assert rc != 0
    assert "frobnicate" in err or "op" in err.lower()


def test_body_replace_and_replace_in_same_update_rejected(tmp_path):
    plan = with_update("good-slug", [
        {"op": "replace", "find": "a", "with": "b"},
        {"op": "body_replace", "content": "x"}
    ])
    p = write_plan(tmp_path, plan)
    rc, _, err = run_validator(p)
    assert rc != 0
    assert "body_replace" in err and "replace" in err


def test_slug_in_two_buckets_rejected(tmp_path):
    plan = json.loads(json.dumps(MINIMAL_VALID_PLAN))
    plan["updates"] = [{"slug": "x", "rationale": "r", "ops": [{"op": "body_replace", "content": "y"}]}]
    plan["deletes"] = [{"slug": "x", "rationale": "r"}]
    plan["log_entry"]["pages_affected"] = ["x"]
    p = write_plan(tmp_path, plan)
    rc, _, err = run_validator(p)
    assert rc != 0
    assert "x" in err and ("updates" in err or "deletes" in err)


def test_bidirectional_link_to_deleted_slug_rejected(tmp_path):
    # The validator only catches in-plan inconsistencies; existence-against-wiki
    # is the applier's job (see Task 12: test_cli_bidir_ghost_slug_holds_cursor).
    # Here: linking to a slug that's in deletes[] is a self-contradiction the
    # validator must reject.
    plan = json.loads(json.dumps(MINIMAL_VALID_PLAN))
    plan["deletes"] = [{"slug": "x", "rationale": "r"}]
    plan["bidirectional_links"] = [{"a": "x", "b": "y"}]
    plan["log_entry"]["pages_affected"] = ["x"]
    p = write_plan(tmp_path, plan)
    rc, _, err = run_validator(p)
    assert rc != 0
    assert "bidirectional_links" in err
    assert "x" in err
    assert "deletes" in err


def test_log_entry_pages_affected_mismatch_rejected(tmp_path):
    plan = with_update("good-slug", [{"op": "body_replace", "content": "x"}])
    plan["log_entry"]["pages_affected"] = ["different-slug"]
    p = write_plan(tmp_path, plan)
    rc, _, err = run_validator(p)
    assert rc != 0
    assert "pages_affected" in err
