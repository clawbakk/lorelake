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


import os
import time

SCRIPT = REPO_ROOT / "hooks" / "lib" / "ingest_gate.py"


def run_gate(repo_path, last, current, state_dir,
             include=("src/",), schedule_enabled="true",
             min_lines=1500, max_age_hours=24, env=None):
    args = [sys.executable, str(SCRIPT),
            "--project-root", str(repo_path),
            "--last-sha", last,
            "--current-sha", current,
            "--state-dir", str(state_dir),
            "--schedule-enabled", schedule_enabled,
            "--min-changed-lines", str(min_lines),
            "--max-age-hours", str(max_age_hours)]
    for p in include:
        args += ["--include", p]
    res = subprocess.run(args, capture_output=True, text=True,
                         env={**os.environ, **(env or {})})
    return res.returncode, res.stdout.strip(), res.stderr.strip()


@pytest.fixture
def state_dir(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


def seed_timestamp(state_dir, seconds_ago=0):
    (state_dir / "last-ingest-at").write_text(str(int(time.time()) - seconds_ago))


def verdict(out):
    return out.split(" ", 1)[0]


def test_empty_pile_verdict(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "README.md", "# docs\n")
    rc, out, _ = run_gate(r, base, head, state_dir)
    assert rc == 0
    assert verdict(out) == "EMPTY"


def test_small_pile_waits(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "src/a.txt", "line1\nline2\n")
    rc, out, _ = run_gate(r, base, head, state_dir, min_lines=1000)
    assert rc == 0
    assert verdict(out) == "WAIT"
    assert "need_lines=1000" in out
    assert "need_age_h=24" in out


def test_lines_arm_trips(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "src/a.txt", "line1\nline2\nline3\n")
    rc, out, _ = run_gate(r, base, head, state_dir, min_lines=2)
    assert rc == 0
    assert verdict(out) == "RUN"
    assert "reason=lines" in out


def test_age_arm_trips(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir, seconds_ago=60 * 60 * 30)  # 30h ago
    head = commit(r, "src/a.txt", "line1\nline2\n")
    rc, out, _ = run_gate(r, base, head, state_dir, min_lines=100000)
    assert rc == 0
    assert verdict(out) == "RUN"
    assert "reason=age" in out


def test_missing_timestamp_runs(repo, state_dir):
    r, base = repo
    head = commit(r, "src/a.txt", "line1\nline2\n")
    rc, out, _ = run_gate(r, base, head, state_dir, min_lines=100000)
    assert rc == 0
    assert verdict(out) == "RUN"
    assert "reason=no-timestamp" in out


def test_unparseable_timestamp_runs(repo, state_dir):
    r, base = repo
    (state_dir / "last-ingest-at").write_text("not-a-number")
    head = commit(r, "src/a.txt", "line1\nline2\n")
    rc, out, _ = run_gate(r, base, head, state_dir, min_lines=100000)
    assert rc == 0
    assert verdict(out) == "RUN"
    assert "reason=no-timestamp" in out


def test_schedule_disabled_runs_on_nonempty_pile(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "src/a.txt", "line1\nline2\n")
    rc, out, _ = run_gate(r, base, head, state_dir,
                          schedule_enabled="false", min_lines=100000)
    assert rc == 0
    assert verdict(out) == "RUN"
    assert "reason=schedule-disabled" in out


def test_schedule_disabled_still_skips_empty_pile(repo, state_dir):
    """Turning off batching must not re-introduce runs over zero relevant input."""
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "README.md", "# docs\n")
    rc, out, _ = run_gate(r, base, head, state_dir, schedule_enabled="false")
    assert rc == 0
    assert verdict(out) == "EMPTY"


def test_env_override_forces_run(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "src/a.txt", "line1\nline2\n")
    rc, out, _ = run_gate(r, base, head, state_dir, min_lines=100000,
                          env={"LLAKE_IGNORE_SCHEDULE": "1"})
    assert rc == 0
    assert verdict(out) == "RUN"
    assert "reason=forced" in out


def test_env_override_still_skips_empty_pile(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "README.md", "# docs\n")
    rc, out, _ = run_gate(r, base, head, state_dir,
                          env={"LLAKE_IGNORE_SCHEDULE": "1"})
    assert rc == 0
    assert verdict(out) == "EMPTY"


def test_bad_sha_exits_nonzero_for_fail_open(repo, state_dir):
    r, _ = repo
    rc, out, err = run_gate(r, "deadbeef", "HEAD", state_dir)
    assert rc != 0
    assert err != ""
    assert out == ""


def test_verdict_line_is_a_single_line(repo, state_dir):
    r, base = repo
    seed_timestamp(state_dir)
    head = commit(r, "src/a.txt", "line1\nline2\n")
    _, out, _ = run_gate(r, base, head, state_dir, min_lines=1000)
    assert len(out.splitlines()) == 1
    assert " " in out  # verdict + detail, splittable on the first space
