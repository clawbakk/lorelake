"""Tests for build-ingest-context.py — Stage 1 of ingest v2."""
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "build-ingest-context.py"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def _make_repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit_file(repo, path, content, message):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _run(*args):
    cmd = ["python3", str(SCRIPT), *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_changes_json_lists_modified_files(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/foo.py", "print('a')\n", "initial")
    sha2 = _commit_file(repo, "src/foo.py", "print('b')\n", "modify foo")
    sha3 = _commit_file(repo, "src/bar.py", "print('c')\n", "add bar")
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", tmp_path / "wiki",
               "--last-sha", sha1, "--current-sha", sha3,
               "--include", "src/", "--out-dir", out_dir, "--diff-chunk-bytes", 2000)
    assert res.returncode == 0, res.stderr
    changes = json.loads((out_dir / "changes.json").read_text())
    assert changes["range"] == f"{sha1}..{sha3}"
    assert "src/foo.py" in changes["files_touched"]
    assert "src/bar.py" in changes["files_touched"]
    subjects = [c["subject"] for c in changes["commits"]]
    assert "modify foo" in subjects and "add bar" in subjects


def test_changes_json_filters_by_include(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/a.py", "x\n", "initial")
    sha2 = _commit_file(repo, "src/a.py", "y\n", "in scope")
    sha3 = _commit_file(repo, "docs/a.md", "z\n", "out of scope")
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", tmp_path / "wiki",
               "--last-sha", sha1, "--current-sha", sha3,
               "--include", "src/", "--out-dir", out_dir, "--diff-chunk-bytes", 2000)
    assert res.returncode == 0, res.stderr
    changes = json.loads((out_dir / "changes.json").read_text())
    assert changes["files_touched"] == ["src/a.py"]


def test_changes_json_records_deletes(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/old.py", "x\n", "initial")
    (repo / "src" / "old.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove old")
    sha2 = _git(repo, "rev-parse", "HEAD").stdout.strip()
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", tmp_path / "wiki",
               "--last-sha", sha1, "--current-sha", sha2,
               "--include", "src/", "--out-dir", out_dir, "--diff-chunk-bytes", 2000)
    assert res.returncode == 0, res.stderr
    changes = json.loads((out_dir / "changes.json").read_text())
    file_status = {f["path"]: f["status"] for c in changes["commits"] for f in c["files"]}
    assert file_status["src/old.py"] == "D"
