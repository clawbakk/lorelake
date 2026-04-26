"""Tests for build_ingest_context.py — Stage 1 of ingest v2."""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "hooks" / "lib" / "build_ingest_context.py"
LIB_DIR = REPO_ROOT / "hooks" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
import build_ingest_context as bic


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


def test_wiki_index_basic_shape(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/a.py", "x", "initial")
    wiki = tmp_path / "wiki"; (wiki / "hooks").mkdir(parents=True)
    (wiki / "hooks" / "post-merge-hook.md").write_text(
        '---\ntitle: "Post-Merge Hook"\ndescription: "x"\n'
        'tags: [hooks]\ncreated: 2026-04-23\nupdated: 2026-04-23\n'
        'status: current\nrelated:\n  - "[[a]]"\n---\n# Body\n')
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", wiki,
               "--last-sha", sha1, "--current-sha", sha1,
               "--include", "src/", "--out-dir", out_dir, "--diff-chunk-bytes", 2000)
    assert res.returncode == 0
    idx = json.loads((out_dir / "wiki-index.json").read_text())
    assert "post-merge-hook" in idx
    e = idx["post-merge-hook"]
    assert e["title"] == "Post-Merge Hook"
    assert e["category"] == "hooks"
    assert "[[a]]" in e["related"]


def test_wiki_index_handles_pages_without_frontmatter(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/a.py", "x", "initial")
    wiki = tmp_path / "wiki"; (wiki / "hooks").mkdir(parents=True)
    (wiki / "hooks" / "bare.md").write_text("# Bare page\n\nNo frontmatter.\n")
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", wiki,
               "--last-sha", sha1, "--current-sha", sha1,
               "--include", "src/", "--out-dir", out_dir, "--diff-chunk-bytes", 2000)
    assert res.returncode == 0
    idx = json.loads((out_dir / "wiki-index.json").read_text())
    assert "bare" in idx
    assert idx["bare"]["title"] == "bare"  # falls back to slug
    assert idx["bare"]["related"] == []


def test_churn_score_weights_commits_more_than_lines():
    # Same total lines (100), different commit counts
    high = bic.churn_score({"commits": 5, "added": 50, "removed": 50})
    low = bic.churn_score({"commits": 1, "added": 50, "removed": 50})
    assert high > low
    # commits dominates: 5 commits worth of weight (50) plus log-dampened lines
    # should exceed 1 commit + same lines
    assert high - low >= 35


def test_churn_score_log_dampens_line_blowup():
    # A single commit with massive lines should NOT outscore a multi-touch file
    multi = bic.churn_score({"commits": 4, "added": 100, "removed": 50})
    big_one = bic.churn_score({"commits": 1, "added": 5000, "removed": 0})
    assert multi > big_one


def test_churn_score_zero_lines_handled():
    # Edge: no line activity, only metadata-noise commits — still has a score
    s = bic.churn_score({"commits": 1, "added": 0, "removed": 0})
    assert s > 0


def test_compute_file_churn_aggregates_across_commits():
    commits = [
        {"sha": "a", "files": [
            {"path": "foo.py", "status": "M", "added": 10, "removed": 5},
            {"path": "bar.py", "status": "A", "added": 20, "removed": 0},
        ]},
        {"sha": "b", "files": [
            {"path": "foo.py", "status": "M", "added": 3, "removed": 2},
        ]},
        {"sha": "c", "files": [
            {"path": "foo.py", "status": "M", "added": 100, "removed": 50},
        ]},
    ]
    churn = bic.compute_file_churn(commits)
    foo = next(c for c in churn if c["path"] == "foo.py")
    bar = next(c for c in churn if c["path"] == "bar.py")
    assert foo["commits"] == 3
    assert foo["added"] == 113
    assert foo["removed"] == 57
    assert bar["commits"] == 1
    # Sorted by score descending
    assert churn[0]["path"] == "foo.py"
    assert churn[1]["path"] == "bar.py"


def test_compute_file_churn_includes_score():
    commits = [{"sha": "a", "files": [{"path": "x", "status": "M", "added": 5, "removed": 5}]}]
    churn = bic.compute_file_churn(commits)
    assert "score" in churn[0]
    assert churn[0]["score"] == bic.churn_score(churn[0])


def test_compute_file_churn_empty_input():
    assert bic.compute_file_churn([]) == []


def test_compute_file_churn_handles_files_without_line_counts():
    # Existing commit_metadata doesn't fill 'added'/'removed' yet (we add that
    # in Task 5); compute_file_churn must default missing keys to 0.
    commits = [{"sha": "a", "files": [{"path": "x", "status": "M"}]}]
    churn = bic.compute_file_churn(commits)
    assert churn[0]["commits"] == 1
    assert churn[0]["added"] == 0
    assert churn[0]["removed"] == 0


def test_changes_json_records_per_file_line_counts(tmp_path):
    repo = _make_repo(tmp_path)
    sha1 = _commit_file(repo, "src/foo.py", "a\nb\nc\n", "initial")
    sha2 = _commit_file(repo, "src/foo.py", "a\nb\nc\nd\ne\n", "add 2 lines")
    out_dir = tmp_path / "ctx"
    res = _run("--project-root", repo, "--wiki-root", tmp_path / "wiki",
               "--last-sha", sha1, "--current-sha", sha2,
               "--include", "src/", "--out-dir", out_dir, "--diff-chunk-bytes", 2000)
    assert res.returncode == 0, res.stderr
    changes = json.loads((out_dir / "changes.json").read_text())
    foo_files = [f for c in changes["commits"] for f in c["files"] if f["path"] == "src/foo.py"]
    # Latest commit added 2 lines, removed 0
    add_two = next(f for f in foo_files if f.get("added") == 2)
    assert add_two["removed"] == 0


def test_select_must_read_pareto_cutoff():
    # 5 files, total score 100, top 2 contribute 80
    churn = [
        {"path": "a", "commits": 1, "added": 0, "removed": 0, "score": 50.0},
        {"path": "b", "commits": 1, "added": 0, "removed": 0, "score": 30.0},
        {"path": "c", "commits": 1, "added": 0, "removed": 0, "score": 10.0},
        {"path": "d", "commits": 1, "added": 0, "removed": 0, "score": 5.0},
        {"path": "e", "commits": 1, "added": 0, "removed": 0, "score": 5.0},
    ]
    result = bic.select_must_read(
        churn, range_commit_count=10, get_bytes=lambda p: 1000,
        pareto_target=0.80, max_bytes=999_999, max_bytes_per_file=999_999,
        multi_touch_floor=99,  # disabled for this test
    )
    paths = [r["path"] for r in result]
    # Stops once cumulative >= 80% of 100
    assert paths == ["a", "b"]


def test_select_must_read_multi_touch_floor_overrides_pareto():
    churn = [
        {"path": "a", "commits": 1, "added": 0, "removed": 0, "score": 50.0},
        {"path": "b", "commits": 1, "added": 0, "removed": 0, "score": 50.0},
        {"path": "c", "commits": 5, "added": 0, "removed": 0, "score": 1.0},  # below pareto, but multi-touch
    ]
    result = bic.select_must_read(
        churn, range_commit_count=10, get_bytes=lambda p: 1000,
        pareto_target=0.50, max_bytes=999_999, max_bytes_per_file=999_999,
        multi_touch_floor=3,
    )
    paths = [r["path"] for r in result]
    assert "c" in paths


def test_select_must_read_max_bytes_caps_total():
    churn = [
        {"path": f"f{i}", "commits": 1, "added": 0, "removed": 0, "score": 10.0 - i}
        for i in range(10)
    ]
    result = bic.select_must_read(
        churn, range_commit_count=20, get_bytes=lambda p: 50_000,
        pareto_target=1.00, max_bytes=120_000, max_bytes_per_file=999_999,
        multi_touch_floor=99,
    )
    # 50KB per file, 120KB total → at most 2 files fit
    assert len(result) == 2


def test_select_must_read_dynamic_max_files_floor_at_5():
    # 4 commits in range; dynamic max = max(5, 4//4) = 5
    churn = [
        {"path": f"f{i}", "commits": 1, "added": 0, "removed": 0, "score": 10.0 - i}
        for i in range(8)
    ]
    result = bic.select_must_read(
        churn, range_commit_count=4, get_bytes=lambda p: 100,
        pareto_target=1.00, max_bytes=999_999, max_bytes_per_file=999_999,
        multi_touch_floor=99,
    )
    # Floor of 5 even though range is tiny
    assert len(result) == 5


def test_select_must_read_dynamic_max_files_scales_with_range():
    churn = [
        {"path": f"f{i}", "commits": 1, "added": 0, "removed": 0, "score": 100.0 - i}
        for i in range(50)
    ]
    result = bic.select_must_read(
        churn, range_commit_count=80, get_bytes=lambda p: 100,
        pareto_target=1.00, max_bytes=999_999, max_bytes_per_file=999_999,
        multi_touch_floor=99,
    )
    # 80 // 4 = 20
    assert len(result) == 20


def test_select_must_read_empty_input():
    result = bic.select_must_read(
        [], range_commit_count=5, get_bytes=lambda p: 100,
        pareto_target=0.80, max_bytes=999_999, max_bytes_per_file=999_999,
        multi_touch_floor=3,
    )
    assert result == []


def test_select_must_read_max_bytes_per_file_truncation_does_not_skip_file():
    # A monster file is capped per-file but still selected
    churn = [{"path": "huge", "commits": 5, "added": 0, "removed": 0, "score": 100.0}]
    result = bic.select_must_read(
        churn, range_commit_count=10, get_bytes=lambda p: 1_000_000,
        pareto_target=0.80, max_bytes=200_000, max_bytes_per_file=30_000,
        multi_touch_floor=3,
    )
    assert len(result) == 1
    assert result[0]["path"] == "huge"
