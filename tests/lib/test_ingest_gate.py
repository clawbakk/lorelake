"""Tests for ingest_gate.py."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "hooks" / "lib"))

import ingest_gate  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


def commit(repo, path, content, msg="change"):
    target = Path(repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    """A git repo with one commit under src/. Returns (path, base_sha)."""
    r = tmp_path / "proj"
    r.mkdir()
    # No -b: the gate never inspects the branch, and `git init -b` needs git 2.28+.
    git(r, "init", "-q")
    git(r, "config", "user.email", "test@example.com")
    git(r, "config", "user.name", "Test")
    git(r, "config", "commit.gpgsign", "false")
    base = commit(r, "src/a.txt", "line1\n", "initial")
    return r, base


def pile(repo_path, last, current, include=("src/",)):
    out = ingest_gate.git_numstat(repo_path, last, current, include)
    return ingest_gate.summarize(out)


def test_no_changes_is_empty_pile(repo):
    r, base = repo
    assert pile(r, base, base) == (0, 0)


def test_counts_insertions_and_deletions(repo):
    r, base = repo
    head = commit(r, "src/a.txt", "line1\nline2\nline3\n")
    # One line replaced by three: 2 insertions, 0 deletions.
    files, lines = pile(r, base, head)
    assert files == 1
    assert lines == 2


def test_deletions_count_toward_the_pile(repo):
    r, base = repo
    commit(r, "src/a.txt", "a\nb\nc\nd\ne\n")
    head = commit(r, "src/a.txt", "a\n")
    # The pile is the NET range diff base..head: "line1" -> "a" is one
    # insertion plus one deletion. Insertions alone would be 1, so this
    # asserts that deletions are counted too.
    files, lines = pile(r, base, head)
    assert files == 1
    assert lines == 2


def test_net_zero_range_is_empty(repo):
    r, base = repo
    commit(r, "src/a.txt", "line1\nadded\n")
    head = commit(r, "src/a.txt", "line1\n")
    # Added then reverted — nothing to document.
    assert pile(r, base, head) == (0, 0)


def test_changes_outside_include_paths_are_ignored(repo):
    r, base = repo
    head = commit(r, "README.md", "# docs\n")
    assert pile(r, base, head) == (0, 0)


def test_binary_change_counts_as_a_file_with_zero_lines(repo):
    r, base = repo
    head = commit(r, "src/blob.bin", bytes(range(256)) * 4)
    files, lines = pile(r, base, head)
    assert files == 1
    assert lines == 0


def test_multiple_include_paths(repo):
    r, base = repo
    commit(r, "src/a.txt", "line1\nline2\n")
    head = commit(r, "hooks/h.sh", "echo hi\n")
    files, lines = pile(r, base, head, include=("src/", "hooks/"))
    assert files == 2
    assert lines == 2


def test_git_failure_raises(repo):
    r, _ = repo
    with pytest.raises(RuntimeError):
        ingest_gate.git_numstat(r, "deadbeef", "HEAD", ("src/",))
