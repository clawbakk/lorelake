"""Tests for render-prompt.py — the prompt template composer."""
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "render-prompt.py"


def render(template_path, config_path, *kv_args):
    cmd = ["python3", str(SCRIPT), str(template_path), str(config_path)] + list(kv_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def write(path, content):
    path.write_text(content)


def test_runtime_var_substitution(tmp_path):
    tmpl = tmp_path / "ingest.md.tmpl"
    write(tmpl, "Project: {{PROJECT_ROOT}}\nRange: {{COMMIT_RANGE}}")
    cfg = tmp_path / "config.json"
    write(cfg, json.dumps({}))

    rc, out, err = render(tmpl, cfg, "PROJECT_ROOT=/tmp/proj", "COMMIT_RANGE=abc..def")
    assert rc == 0, err
    assert out == "Project: /tmp/proj\nRange: abc..def"


def test_custom_slot_from_config(tmp_path):
    tmpl = tmp_path / "ingest.md.tmpl"
    write(tmpl, "Examples:\n{{EXAMPLES}}")
    cfg = tmp_path / "config.json"
    write(cfg, json.dumps({"prompts": {"ingest": {"EXAMPLES": "An example body."}}}))

    rc, out, err = render(tmpl, cfg)
    assert rc == 0, err
    assert out == "Examples:\nAn example body."


def test_fallback_marker_used_when_slot_empty(tmp_path):
    fallback = tmp_path / "generic.md"
    write(fallback, "GENERIC FALLBACK CONTENT")
    tmpl = tmp_path / "ingest.md.tmpl"
    write(tmpl, "Examples:\n{{EXAMPLES|fallback:" + str(fallback) + "}}")
    cfg = tmp_path / "config.json"
    write(cfg, json.dumps({"prompts": {"ingest": {"EXAMPLES": ""}}}))

    rc, out, err = render(tmpl, cfg)
    assert rc == 0, err
    assert out == "Examples:\nGENERIC FALLBACK CONTENT"


def test_fallback_marker_not_used_when_slot_filled(tmp_path):
    fallback = tmp_path / "generic.md"
    write(fallback, "FALLBACK")
    tmpl = tmp_path / "ingest.md.tmpl"
    write(tmpl, "Examples:\n{{EXAMPLES|fallback:" + str(fallback) + "}}")
    cfg = tmp_path / "config.json"
    write(cfg, json.dumps({"prompts": {"ingest": {"EXAMPLES": "REAL"}}}))

    rc, out, err = render(tmpl, cfg)
    assert rc == 0, err
    assert "FALLBACK" not in out
    assert "REAL" in out


def test_unresolved_var_exits_nonzero(tmp_path):
    tmpl = tmp_path / "ingest.md.tmpl"
    write(tmpl, "Has {{UNFILLED}} placeholder")
    cfg = tmp_path / "config.json"
    write(cfg, json.dumps({}))

    rc, out, err = render(tmpl, cfg)
    assert rc != 0
    assert "UNFILLED" in err


def test_template_name_drives_slot_lookup(tmp_path):
    """Template named 'capture.md.tmpl' should look up slots under prompts.capture.*"""
    tmpl = tmp_path / "capture.md.tmpl"
    write(tmpl, "{{NOTE}}")
    cfg = tmp_path / "config.json"
    write(cfg, json.dumps({"prompts": {"capture": {"NOTE": "from capture section"}}}))

    rc, out, err = render(tmpl, cfg)
    assert rc == 0, err
    assert out == "from capture section"


def test_relative_fallback_resolved_against_templates_dir(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "generic-examples.md").write_text("FROM TEMPLATES DIR")

    tmpl = tmp_path / "ingest.md.tmpl"
    write(tmpl, "{{EXAMPLES|fallback:generic-examples.md}}")
    cfg = tmp_path / "config.json"
    write(cfg, json.dumps({"prompts": {"ingest": {"EXAMPLES": ""}}}))

    cmd = ["python3", str(SCRIPT), "--templates-dir", str(templates_dir),
           str(tmpl), str(cfg)]
    import subprocess
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "FROM TEMPLATES DIR"
