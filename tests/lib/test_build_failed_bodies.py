"""Tests for build_failed_bodies.py — assembles the FAILED_PAGE_BODIES slot."""
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "build_failed_bodies.py"


def _run(failed_path, wiki_root):
    cmd = ["python3", str(SCRIPT), str(failed_path), str(wiki_root)]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_emits_one_block_per_failed_page(tmp_path):
    wiki = tmp_path / "wiki"; (wiki / "hooks").mkdir(parents=True)
    (wiki / "hooks" / "broken.md").write_text("---\ntitle: B\n---\n# B\n")
    (wiki / "hooks" / "other.md").write_text("---\ntitle: O\n---\n# O\n")
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps([
        {"slug": "broken", "reason": "AnchorNotFound", "detail": "x"},
        {"slug": "other", "reason": "AnchorAmbiguous", "detail": "y"},
    ]))
    res = _run(failed, wiki)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "### broken" in out
    assert "### other" in out
    assert "title: B" in out
    assert "title: O" in out
    # Code-fenced page bodies (literal backticks)
    assert "```\n---\ntitle: B" in out


def test_skips_missing_pages(tmp_path):
    """A failed slug that has no page on disk (e.g., create-failed) is skipped silently."""
    wiki = tmp_path / "wiki"; (wiki / "hooks").mkdir(parents=True)
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps([{"slug": "ghost", "reason": "AlreadyExists", "detail": "z"}]))
    res = _run(failed, wiki)
    assert res.returncode == 0
    assert "ghost" not in res.stdout


def test_handles_empty_failed_list(tmp_path):
    wiki = tmp_path / "wiki"; wiki.mkdir(parents=True)
    failed = tmp_path / "failed.json"
    failed.write_text("[]")
    res = _run(failed, wiki)
    assert res.returncode == 0
    assert res.stdout.strip() == ""
