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


def test_diffs_one_file_one_chunk(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/foo.py", "x\n", "initial")
    sha2 = _commit_file(repo, "src/foo.py", "x\ny\n", "modify")
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", tmp_path / "wiki",
               "--last-sha", sha1, "--current-sha", sha2,
               "--include", "src/", "--out-dir", out_dir, "--diff-chunk-bytes", 100000)
    assert res.returncode == 0
    diffs = list((out_dir / "diffs").glob("*.patch"))
    assert len(diffs) == 1
    assert "src__foo.py" in diffs[0].name


def test_diffs_split_at_hunk_boundaries(tmp_path):
    repo = _make_repo(tmp_path)
    initial = "\n".join(f"line {i}" for i in range(50)) + "\n"
    sha1 = _commit_file(repo, "src/big.py", initial, "initial")
    modified = initial.replace("line 5", "LINE 5").replace("line 30", "LINE 30")
    sha2 = _commit_file(repo, "src/big.py", modified, "two hunks")
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", tmp_path / "wiki",
               "--last-sha", sha1, "--current-sha", sha2,
               "--include", "src/", "--out-dir", out_dir,
               "--diff-chunk-bytes", 100)  # tiny → forces split
    assert res.returncode == 0
    chunks = sorted((out_dir / "diffs").glob("src__big.py.*.patch"))
    assert len(chunks) >= 2
    index = json.loads((out_dir / "diffs" / "src__big.py.index.json").read_text())
    assert index["chunks"] == [c.name for c in chunks]


def test_diffs_oversized_single_hunk_emits_one_chunk(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/x.py", "before\n", "initial")
    sha2 = _commit_file(repo, "src/x.py",
                        "before\n" + ("inserted line\n" * 200), "huge insert")
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", tmp_path / "wiki",
               "--last-sha", sha1, "--current-sha", sha2,
               "--include", "src/", "--out-dir", out_dir,
               "--diff-chunk-bytes", 50)
    assert res.returncode == 0
    # The single hunk is bigger than 50 bytes, but we don't split mid-hunk.
    files = list((out_dir / "diffs").glob("src__x.py*.patch"))
    assert len(files) >= 1
