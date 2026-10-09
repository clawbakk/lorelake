# LOR-25 v3 Split After a Deterministic Failure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After an ingest v3 run fails and holds the cursor, the first trigger after the cause is fixed ingests everything from the held cursor to HEAD; range split and skip apply only to genuine, repeated analysis work failures.

**Architecture:** `plan.py` gets a third failure class (`pipeline`, which clears the counter), binds the counter to the plugin version, and picks the split point by watched-path churn instead of commit count. `run.py` classifies an invalid brief as a pipeline error and exits 3 when a finalized split or skip stopped short of HEAD; `post-merge.sh` then runs one continuation run under the same lock, with a fresh agent and deadline.

**Tech Stack:** Python 3 (stdlib only), bash 3.2, git, pytest.

**Spec:** `docs/plans/2026-10-06-LOR-25-split-after-deterministic-failure-design.md` (design, approved). Normative spec it amends: `docs/specs/ingest-v3.md` §2, §3, §12.

**Ticket:** LOR-25 — https://linear.app/clawbakk/issue/LOR-25/v3-split-after-a-deterministic-failure-processes-an-empty-slice

## Decisions taken

Binding on the implementation phase. Each one says what was decided, what settled it, and what changes if it is reversed.

1. **Architectural path, design doc + plan.** linear-ship rule. Reversal: none (process).
2. **Only `InvalidBrief` is a `pipeline` failure.** Analysis agent failures (budget, timeout, max turns, no result) stay `work`, and internal exceptions keep cursor-table row 10. Settled by the ticket ("brief/schema validation failure") and spec §12 (agent failures are infra or work). Reversal: widening it, e.g. to "agent succeeded but wrote no themes.json", would be one more `record_failure(..., "pipeline")` call.
3. **A pipeline failure clears the counter; infra still leaves it unchanged.** Settled by ticket cause 2 ("or the failure class changes … go back to a full range") and spec row 6 (infra changes nothing). Reversal: leaving the counter unchanged would mean an earlier work episode can still split after the pipeline bug is fixed. That is the baqqetto failure mode.
4. **The counter is keyed on `(base, plugin version)`, and the version comes from `.claude-plugin/plugin.json` beside the code.** A record without `plugin` (0.1.9 and earlier) never matches. Settled by the ticket's acceptance ("whatever counter state is left") and by the plugin being the only artifact that changes with a fix. Reversal: keying on a hash of the code instead would also catch unversioned local edits, at the cost of a less readable record.
5. **`lastHead` keeps meaning "the head that failed" (cause 3 gets no code change of its own).** For counted work failures that is what makes the search converge. The halving that went on after the trigger moved on is closed by decisions 3 and 4. Settled by spec §3 (each failure halves the range). Reversal: resetting `lastHead` to HEAD on every trigger would undo the binary search for real failures.
6. **Split point = the watched commit whose cumulative churn is closest to half the total.** Candidates exclude the last watched commit and any slice that nets to no watched change. Churn is the per-commit first-parent `--numstat` diff, at least 1 per file. Settled by the ticket's acceptance (weighted by churn, never an empty slice). Reversal: a count-based midpoint over watched commits only is the simpler fallback the ticket allows.
7. **"One-commit range" for skip means "at most one first-parent commit carries watched changes".** With decision 6 such a range has no non-empty split. Skip still needs two counted work failures under the same plugin version. Settled by the two acceptance lines together (never an empty slice, and skip only for a one-commit range). Reversal: splitting off the trivial prefix first costs two more failed analyses before the skip.
8. **The continuation runs in bash (`post-merge.sh`), not as a loop in Python.** It runs once per invocation, gets a fresh agent, deadline and watchdog, and skips the gate. Settled by the deadline design: a remainder squeezed into the first run's deadline would fail analysis as *work* and start counting again. The gate would defer the remainder because finalize reset the clock. Reversal: queuing it for the next trigger is the ticket's other allowed option, but the symptom ("nothing happened") stays.
9. **Continuation also follows a finalized skip that stopped short of HEAD.** Same symptom, same mechanism. Settled by the ticket's Expected section. Reversal: drop `"skip"` from `_finished`.
10. **Exit code 3 = `EXIT_CONTINUE`.** 0 and 1 keep their meaning, and nothing else reads the code today (`run_ingest_v3`'s status was ignored). Reversal: a marker file. That is more state for no gain.
11. **Remove `run.py`'s "no watched changes up to the split midpoint" branch and `NO_CHANGE_THEME`.** Decision 6 makes it unreachable. Reversal: keep it as a defensive branch, which leaves dead code with no test that can reach it.
12. **Version 0.1.9 → 0.1.10 in both manifests**, standing user rule. The CHANGELOG gets a `[Unreleased]` → Fixed bullet.
13. **TDD exception, Task 5 only:** documentation and manifest version text. There is no behaviour to test first. The existing `tests/lib/test_marketplace_manifest.py` guards that the two versions match.
14. **Design and plan are force-added (`git add -f`)** because `docs/*` is gitignored. `.gitignore` is unchanged. If the human wants internal plans untracked, drop them from the PR with `git rm --cached` (run-log note N1).
15. **Self-approved gates (autonomous mode):** the write-back of understanding and each design section were checked against the ticket. The written design was approved by the advisor (high). The plan review is this plan's Self-Review.
16. **Out of scope:** backoff for repeated pipeline errors (follow-up F1, not tracked in Linear); clearing skipped ranges (LOR-22, Backlog); splitting inside one merge commit (YAGNI).

## Global Constraints

- Shell code stays bash 3.2 portable: no `$BASHPID`, no `wait -n`, no associative arrays, no bash-4 features (`tests/hooks/test_bash32_portability.sh`).
- Python: stdlib only, matching `hooks/lib/ingest_v3/` style (`.format`, no f-strings needed but allowed; no new dependencies).
- Never write or stage anything under `llake/` (this repo's own wiki). Tests use temp projects.
- Plugin code writes only inside `<project>/llake/`.
- Failure file path stays `llake/.state/ingest-failures.json`; new shape `{"base", "count", "lastHead", "plugin"}`.
- Exit codes of `ingest-v3.py run`: `0` done, `1` cursor held, `3` finalized split/skip short of HEAD (continue).
- Version after this PR: `0.1.10` in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
- Full verification: `python3 -m pytest tests/ -q` and every `bash tests/hooks/*.sh` green.

## Review Focus

1. **A 0.1.9 failure file left on disk** (no `plugin` key, count ≥ 2, `lastHead` mid-range). Expect a full range run. Pinned in Task 1 (`test_counter_without_plugin_key_is_ignored`) and Task 3 (`test_counter_from_an_older_plugin_plans_the_full_range`).
2. **`lastHead` that no longer resolves** (force-push or rewritten history) while the counter is active. Expect a split computed over base..HEAD and no crash. Pinned in Task 2 (`test_unresolvable_last_head_splits_from_head`).
3. **A kill during the continuation run.** The trap must revert the continuation's agent dir and log under its ID, not the first run's. Pinned in Task 4 (`test_v3_next_agent_repoints_trap_globals`).
4. **Merge-commit history**, where first-parent commits are merges carrying the PR content. Expect churn to count the merged content. Pinned in Task 2 (`test_commit_churn_counts_a_merge_by_its_first_parent_diff`).
5. **A revert pair inside the range**, where a slice nets to no watched change. Expect the split never to land there. Pinned in Task 2 (`test_split_skips_a_candidate_whose_slice_nets_to_nothing`).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `hooks/lib/ingest_v3/common.py` | modify | `PLUGIN_MANIFEST`, `plugin_version()` |
| `hooks/lib/ingest_v3/plan.py` | modify | failure classes, `active_failures`, `commit_churn`, `split_point`, `plan_run` |
| `hooks/lib/ingest_v3/run.py` | modify | pipeline hold, stale-counter log, `EXIT_CONTINUE`, `_finished`, dead branch removed |
| `hooks/lib/ingest_v3/cli.py` | modify | `run` help text names exit codes |
| `hooks/lib/ingest-v3.sh` | modify | `V3_EXIT_CONTINUE`, `run_ingest_v3_watched`, `next_v3_agent` |
| `hooks/post-merge.sh` | modify | v3 subshell uses the helpers, runs one continuation |
| `tests/lib/test_v3_plan.py` | modify | class, version, churn, split tests |
| `tests/lib/test_v3_run_e2e.py` | modify | seeds with plugin, pipeline hold, regression, continuation codes |
| `tests/hooks/test_post_merge_v3.sh` | modify | continuation and trap-globals tests |
| `docs/specs/ingest-v3.md`, `docs/adr/0001-ingest-v3-pipeline.md`, `schema/operations.md`, `CONTEXT.md`, `CHANGELOG.md` | modify | behaviour docs |
| `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | modify | 0.1.10 |

---

### Task 1: Failure classes and the plugin-bound counter

**Files:**
- Modify: `hooks/lib/ingest_v3/common.py` (after the constants block, before `load_json`)
- Modify: `hooks/lib/ingest_v3/plan.py:1-70` (docstring, imports, `record_failure`, new `active_failures`, `plan_run` counter lookup)
- Test: `tests/lib/test_v3_plan.py`, `tests/lib/test_v3_run_e2e.py`

**Interfaces:**
- Produces: `common.PLUGIN_MANIFEST: str`; `common.plugin_version() -> str` (`"unknown"` when unreadable); `plan.plugin_version` (imported name, monkeypatchable); `plan.active_failures(llake_root: str, base: str) -> dict` (the record when `base` and `plugin` match, else `{}`); `plan.record_failure(llake_root, base, head, cls) -> dict` with `cls in {"work", "infra", "pipeline"}`: `pipeline` clears the file and returns `{}`.

- [ ] **Step 1: Write the failing tests** in `tests/lib/test_v3_plan.py`

Change the import line to:

```python
from v3_helpers import REPO_ROOT, commit, git, make_project, write
from ingest_v3 import common, plan, state
from ingest_v3.common import dump_json, load_json, plugin_version
```

Replace `test_work_failures_count_infra_does_not` with:

```python
def test_work_failures_count_infra_does_not(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 1)
    assert plan.record_failure(llake, base, shas[0], "infra") == {}
    assert plan.record_failure(llake, base, shas[0], "work")["count"] == 1
    f = plan.record_failure(llake, base, shas[0], "work")
    assert f == {"base": base, "count": 2, "lastHead": shas[0], "plugin": plugin_version()}
    assert plan.record_failure(llake, base, shas[0], "infra")["count"] == 2
    assert plan.record_failure(llake, "other", shas[0], "work")["count"] == 1
    plan.clear_failures(llake)
    assert plan.load_failures(llake) == {}
```

Add:

```python
def test_plugin_version_reads_the_manifest():
    import json
    assert plugin_version() == json.loads((REPO_ROOT / ".claude-plugin/plugin.json").read_text())["version"]


def test_plugin_version_unknown_without_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "PLUGIN_MANIFEST", str(tmp_path / "missing.json"))
    assert common.plugin_version() == "unknown"


def test_pipeline_failure_clears_the_counter(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 2)
    plan.record_failure(llake, base, shas[-1], "work")
    plan.record_failure(llake, base, shas[-1], "work")
    assert plan.record_failure(llake, base, shas[-1], "pipeline") == {}
    assert plan.load_failures(llake) == {}
    assert plan.plan_run(repo, llake, ["src/"], base, shas[-1]) == ("range", shas[-1])


def test_pipeline_failures_never_reach_split_or_skip(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 1)
    for _ in range(5):
        plan.record_failure(llake, base, shas[0], "pipeline")
    assert plan.plan_run(repo, llake, ["src/"], base, shas[0]) == ("range", shas[0])


def test_counter_from_another_plugin_version_is_ignored(tmp_path, monkeypatch):
    repo, llake, base, shas = chain_repo(tmp_path, 4)
    monkeypatch.setattr(plan, "plugin_version", lambda: "0.1.8")
    for _ in range(3):
        plan.record_failure(llake, base, shas[1], "work")
    monkeypatch.setattr(plan, "plugin_version", lambda: "0.1.9")
    assert plan.plan_run(repo, llake, ["src/"], base, shas[-1]) == ("range", shas[-1])
    assert plan.active_failures(llake, base) == {}
    assert plan.record_failure(llake, base, shas[-1], "work") == {
        "base": base, "count": 1, "lastHead": shas[-1], "plugin": "0.1.9"}


def test_counter_without_plugin_key_is_ignored(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 4)
    dump_json(llake + "/.state/ingest-failures.json", {"base": base, "count": 3, "lastHead": shas[1]})
    assert plan.plan_run(repo, llake, ["src/"], base, shas[-1]) == ("range", shas[-1])
```

- [ ] **Step 2: Update the e2e seeds** in `tests/lib/test_v3_run_e2e.py` (they would otherwise be ignored as stale once Step 4 lands)

Add the import `from ingest_v3.common import dump_json, load_json, plugin_version` (replacing the existing `from ingest_v3.common import dump_json, load_json`), and after `hooks()` add:

```python
def seed_failures(repo, base, count, last):
    dump_json(str(repo / "llake/.state/ingest-failures.json"),
              {"base": base, "count": count, "lastHead": last, "plugin": plugin_version()})
```

Replace every `dump_json(str(repo / "llake/.state/ingest-failures.json"), {"base": base, "count": 2, "lastHead": X})` in this file (four places: `test_split_run_advances_to_midpoint_row_4`, `test_single_commit_skip_row_7`, `test_split_midpoint_without_watched_changes_finalizes`, `test_killed_skip_is_revertible`) with `seed_failures(repo, base, 2, X)`. In `test_analysis_work_failure_holds_row_5`, change the expected dict to:

```python
    assert load_json(str(repo / "llake/.state/ingest-failures.json")) == {
        "base": base, "count": 1, "lastHead": shas[-1], "plugin": plugin_version()}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/lib/test_v3_plan.py tests/lib/test_v3_run_e2e.py -q`
Expected: FAIL — `ImportError: cannot import name 'plugin_version'`.

- [ ] **Step 4: Implement**

In `hooks/lib/ingest_v3/common.py`, after `BROKEN_ANCHOR_CAP = 25 …` add:

```python
# The plugin's own manifest, four levels up from this file (hooks/lib/ingest_v3/common.py).
PLUGIN_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    ".claude-plugin", "plugin.json")


def plugin_version():
    """The running plugin's version from its manifest, or "unknown" when the file or field is missing."""
    meta = load_json(PLUGIN_MANIFEST, None)
    version = meta.get("version") if isinstance(meta, dict) else None
    return str(version) if version else "unknown"
```

In `hooks/lib/ingest_v3/plan.py`, replace the module docstring's counter paragraph with:

```python
"""Run planning (spec §3): run kinds, the analysis failure counter, range split and skip, dead-run recovery.

Failure counter, .state/ingest-failures.json: {base, count, lastHead, plugin}. It applies only while its base
and plugin version match the current ones. Work failures increment it and record the head that failed;
infra failures change nothing; a pipeline failure (an invalid brief: deterministic, fails the same on any
range) clears it. With count >= 2 the next run splits base..lastHead at its churn midpoint (split_point); a
range whose watched changes sit in one first-parent commit cannot shrink and is skipped.
"""
```

Change the import to `from .common import SPLIT_AFTER_FAILURES, dump_json, git, load_json, plugin_version`, and replace `record_failure` with:

```python
def active_failures(llake_root, base):
    """The counter, only while it belongs to this base and this plugin version; else {}."""
    f = load_failures(llake_root)
    if f.get("base") == base and f.get("plugin") == plugin_version():
        return f
    return {}


def record_failure(llake_root, base, head, cls):
    if cls == "pipeline":
        clear_failures(llake_root)
        return {}
    if cls != "work":
        return load_failures(llake_root)
    prev = active_failures(llake_root, base)
    f = {"base": base, "count": int(prev.get("count", 0)) + 1, "lastHead": head, "plugin": plugin_version()}
    dump_json(_failures_path(llake_root), f)
    return f
```

In `plan_run`, replace the two lines

```python
    f = load_failures(llake_root)
    if f.get("base") == base and int(f.get("count", 0)) >= SPLIT_AFTER_FAILURES:
```

with

```python
    f = active_failures(llake_root, base)
    if int(f.get("count", 0)) >= SPLIT_AFTER_FAILURES:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/lib/test_v3_plan.py tests/lib/test_v3_run_e2e.py tests/lib/test_v3_finalize.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add hooks/lib/ingest_v3/common.py hooks/lib/ingest_v3/plan.py tests/lib/test_v3_plan.py tests/lib/test_v3_run_e2e.py
git commit -m "fix(ingest-v3): bind the failure counter to the plugin version; add the pipeline class (LOR-25)"
```

---

### Task 2: Churn-weighted split point

**Files:**
- Modify: `hooks/lib/ingest_v3/plan.py` (new `commit_churn`, `split_point`; `plan_run` split branch)
- Modify: `hooks/lib/ingest_v3/run.py:36-37` (drop `NO_CHANGE_THEME`), `run.py:298-317` (drop the no-watched-changes branch)
- Test: `tests/lib/test_v3_plan.py`, `tests/lib/test_v3_run_e2e.py`

**Interfaces:**
- Consumes: `plan.active_failures`, `plan.watched_changes(repo, base, head, include) -> bool`, `plan.first_parent_chain(repo, base, head) -> list[str]` (newest first).
- Produces: `plan.commit_churn(repo: str, sha: str, include: list) -> int`; `plan.split_point(repo: str, base: str, target: str, include: list) -> str | None`. `plan_run` returns `RunPlan("split", point)`, `RunPlan("skip", target)` or `RunPlan("range", head)` once the counter is at ≥ 2.

- [ ] **Step 1: Write the failing tests** in `tests/lib/test_v3_plan.py`

```python
def merge_repo(tmp_path):
    """Baqqetto's shape: base -> a trivial direct commit -> a --no-ff merge carrying all watched content.
    First-parent chain of base..merge is [merge, trivial]."""
    repo = make_project(tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, {"src/a.py": "a\n" * 50}, "feature 1")
    commit(repo, {"src/b.py": "b\n" * 50}, "feature 2")
    git(repo, "checkout", "-q", "main")
    trivial = commit(repo, {"package.json": "{}\n"}, "bump")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    merge = git(repo, "rev-parse", "HEAD").strip()
    return str(repo), str(repo / "llake"), base, trivial, merge


def test_commit_churn_counts_a_merge_by_its_first_parent_diff(tmp_path):
    repo, llake, base, trivial, merge = merge_repo(tmp_path)
    assert plan.commit_churn(repo, trivial, ["src/"]) == 0
    assert plan.commit_churn(repo, merge, ["src/"]) == 100


def test_commit_churn_counts_a_pure_rename(tmp_path):
    repo = make_project(tmp_path, src={"src/app.py": "x = 1\n"})
    git(repo, "mv", "src/app.py", "src/main.py")
    git(repo, "commit", "-q", "-m", "rename")
    sha = git(repo, "rev-parse", "HEAD").strip()
    assert plan.commit_churn(str(repo), sha, ["src/"]) >= 1


def test_split_never_lands_on_a_slice_without_watched_changes(tmp_path):
    repo, llake, base, trivial, merge = merge_repo(tmp_path)
    assert plan.split_point(repo, base, merge, ["src/"]) is None
    plan.record_failure(llake, base, merge, "work")
    assert plan.plan_run(repo, llake, ["src/"], base, merge) == ("range", merge)
    plan.record_failure(llake, base, merge, "work")
    assert plan.plan_run(repo, llake, ["src/"], base, merge) == ("skip", merge)


def test_baqqetto_shape_after_pipeline_failures_is_a_full_range(tmp_path):
    repo, llake, base, trivial, merge = merge_repo(tmp_path)
    for _ in range(3):
        plan.record_failure(llake, base, merge, "pipeline")
    assert plan.plan_run(repo, llake, ["src/"], base, merge) == ("range", merge)


def test_split_point_is_weighted_by_churn(tmp_path):
    repo = make_project(tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    commit(repo, {"src/a.py": "a\n"}, "small 1")
    commit(repo, {"src/b.py": "b\n"}, "small 2")
    big = commit(repo, {"src/c.py": "c\n" * 100}, "big")
    last = commit(repo, {"src/d.py": "d\n" * 100}, "last")
    # by commit count the midpoint would be "small 2"; by churn it is "big"
    assert plan.split_point(str(repo), base, last, ["src/"]) == big


def test_split_skips_a_candidate_whose_slice_nets_to_nothing(tmp_path):
    repo = make_project(tmp_path, src={"src/app.py": "x = 1\n"})
    base = git(repo, "rev-parse", "HEAD").strip()
    change = commit(repo, {"src/app.py": "x = 2\n"}, "change")
    commit(repo, {"src/app.py": "x = 1\n"}, "revert")  # base..revert nets to no watched change
    more = commit(repo, {"src/c.py": "c\n" * 10}, "more")
    assert plan.split_point(str(repo), base, more, ["src/"]) == change


def test_unresolvable_last_head_splits_from_head(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 4)
    dump_json(llake + "/.state/ingest-failures.json",
              {"base": base, "count": 2, "lastHead": "0" * 40, "plugin": plugin_version()})
    assert plan.plan_run(repo, llake, ["src/"], base, shas[-1]) == ("split", shas[1])
```

The existing `test_split_after_two_work_failures_halves_until_skip`, `test_two_commit_split_midpoint_is_strictly_older` and `test_single_commit_range_skipped_after_two_work_failures` stay unchanged and must still pass: `chain_repo` commits have equal churn (2 each), so the churn midpoint equals the old count midpoint.

In `tests/lib/test_v3_run_e2e.py`, replace `test_split_midpoint_without_watched_changes_finalizes` with:

```python
def test_split_lands_on_a_watched_commit_not_on_trivial_ones(tmp_path, stages):
    repo, base, _ = project(tmp_path, commits=0)
    commit(repo, {"README.md": "one\n"}, "docs 1")
    commit(repo, {"README.md": "two\n"}, "docs 2")
    c3 = commit(repo, {"src/app.py": "def loadProfile():\n    return 1\n"}, "rename")
    c4 = commit(repo, {"src/app.py": "def loadProfile():\n    return 2\n"}, "tweak")
    seed_failures(repo, base, 2, c4)
    assert go(repo) == 0
    assert cursor(repo) == c3 and stages() == ["analysis", "recall"]
    assert "ingest | {}..{}: v3".format(base[:7], c3[:7]) in (repo / "llake/log.md").read_text()
    assert not (repo / "llake/.state/ingest-failures.json").exists()
    assert "completed: agent run-1 v3 split" in hooks(repo)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/lib/test_v3_plan.py tests/lib/test_v3_run_e2e.py -q`
Expected: FAIL — `AttributeError: module 'ingest_v3.plan' has no attribute 'commit_churn'`, and the e2e split test lands on `docs 2`.

- [ ] **Step 3: Implement**

In `hooks/lib/ingest_v3/plan.py`, after `watched_changes`, add:

```python
def commit_churn(repo, sha, include):
    """Watched churn of one commit against its first parent: added + deleted lines, at least 1 per file (a pure
    rename or a binary file counts 1). For a merge commit this is the merged branch's content."""
    out = git(repo, "diff", "--numstat", sha + "^1", sha, "--", *include)
    total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        n = int(added) + int(deleted) if added.isdigit() and deleted.isdigit() else 0
        total += max(n, 1)
    return total


def split_point(repo, base, target, include):
    """The split head for base..target (spec §3): the watched first-parent commit whose cumulative churn is
    closest to half the total, ties to the earlier. Never the last watched commit (the remainder keeps watched
    content) and never a commit where base..commit nets to no watched change. None when the range cannot
    shrink: at most one first-parent commit carries watched changes."""
    commits = list(reversed(first_parent_chain(repo, base, target)))
    watched = [(c, w) for c, w in ((c, commit_churn(repo, c, include)) for c in commits) if w > 0]
    if len(watched) < 2:
        return None
    total = sum(w for _, w in watched)
    best, best_d, cum = None, None, 0
    for c, w in watched[:-1]:
        cum += w
        if not watched_changes(repo, base, c, include):
            continue
        d = abs(2 * cum - total)
        if best is None or d < best_d:
            best, best_d = c, d
    return best
```

Replace `plan_run` with:

```python
def plan_run(repo, llake_root, include, base, head):
    if not watched_changes(repo, base, head, include):
        return RunPlan("gap-only" if gaps.owed_major(gaps.load(llake_root)) else "empty", head)
    f = active_failures(llake_root, base)
    if int(f.get("count", 0)) < SPLIT_AFTER_FAILURES:
        return RunPlan("range", head)
    target = f.get("lastHead") or head
    try:
        point = split_point(repo, base, target, include)
    except RuntimeError:  # lastHead no longer resolves (rewritten history): work from the trigger head
        target, point = head, split_point(repo, base, head, include)
    if point is not None:
        return RunPlan("split", point)
    if watched_changes(repo, base, target, include):
        return RunPlan("skip", target)
    return RunPlan("range", head)
```

In `hooks/lib/ingest_v3/run.py`, delete the `NO_CHANGE_THEME = {...}` constant (lines 36-37), and replace

```python
        if gap_only:
            names.write_empty_inputs(inputs)
        elif not plan.watched_changes(project, base, rp.head, include):
            # A split midpoint can precede every watched change: nothing to analyse, carried gaps still ride.
            names.write_empty_inputs(inputs)
            dump_json(os.path.join(agent_dir, "brief", "themes.json"), [NO_CHANGE_THEME])
            log.line("no watched changes in {}..{}: analysis and recall skipped".format(base[:7], rp.head[:7]))
        else:
```

with

```python
        if gap_only:
            names.write_empty_inputs(inputs)
        else:  # a range or split head always has watched changes (plan.split_point never picks an empty slice)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/lib/test_v3_plan.py tests/lib/test_v3_run_e2e.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/ingest_v3/plan.py hooks/lib/ingest_v3/run.py tests/lib/test_v3_plan.py tests/lib/test_v3_run_e2e.py
git commit -m "fix(ingest-v3): split at the churn midpoint, never on a slice without watched changes (LOR-25)"
```

---

### Task 3: Pipeline hold, stale-counter log, continuation exit code

**Files:**
- Modify: `hooks/lib/ingest_v3/run.py` (docstring, `EXIT_CONTINUE`, `_finished`, `_run`)
- Modify: `hooks/lib/ingest_v3/cli.py:66` (help text)
- Test: `tests/lib/test_v3_run_e2e.py`

**Interfaces:**
- Consumes: `plan.record_failure(..., "pipeline")`, `plan.load_failures`, `plan.active_failures`, `plan.plugin_version` (Task 1).
- Produces: `run.EXIT_CONTINUE == 3`; `run.run(...)` returns 3 after a finalized `split` or `skip` whose head is not the current `HEAD`. Task 4's bash reads this code.

- [ ] **Step 1: Write the failing tests** in `tests/lib/test_v3_run_e2e.py`

Replace `test_invalid_brief_is_a_work_failure` with:

```python
INVALID_BRIEF = '[{"path": "llake/wiki/arch/client.md"}]'


def test_invalid_brief_is_a_pipeline_error_not_counted(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", INVALID_BRIEF)
    assert go(repo) == 1
    assert cursor(repo) == base
    assert not (repo / "llake/.state/ingest-failures.json").exists()
    assert "held: agent run-1 v3 (pipeline error: invalid brief:" in hooks(repo)
    assert "(not counted toward split)" in (repo / "llake/.state/agents/run-1/agent.log").read_text()


def test_held_invalid_briefs_then_fixed_brief_ingest_the_whole_range(tmp_path, stages, monkeypatch):
    """LOR-25 regression: three runs held on a brief validation error, then the fix -> one range run over
    the full held range."""
    repo, base, shas = project(tmp_path, commits=4)
    monkeypatch.setenv("V3_STUB_BRIEF", INVALID_BRIEF)
    for i in range(3):
        assert go(repo, agent="held-{}".format(i)) == 1
        assert cursor(repo) == base
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    assert go(repo, agent="fixed") == 0
    assert cursor(repo) == shas[-1]
    assert load_json(str(repo / "llake/.state/agents/fixed/run.json"))["kind"] == "range"
    assert "ingest | {}..{}: v3 — 1 updated".format(base[:7], shas[-1][:7]) in (repo / "llake/log.md").read_text()
    assert "completed: agent fixed v3 range" in hooks(repo)


def test_counter_from_an_older_plugin_plans_the_full_range(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path, commits=4)
    dump_json(str(repo / "llake/.state/ingest-failures.json"), {"base": base, "count": 3, "lastHead": shas[1]})
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    assert "failure counter ignored (plugin unrecorded" in \
        (repo / "llake/.state/agents/run-1/agent.log").read_text()
    assert "completed: agent run-1 v3 range" in hooks(repo)


def test_skip_short_of_head_continues(tmp_path, stages):
    repo, base, shas = project(tmp_path, commits=2)
    seed_failures(repo, base, 2, shas[0])
    assert go(repo) == run.EXIT_CONTINUE
    assert cursor(repo) == shas[0]
```

In `test_split_run_advances_to_midpoint_row_4`, change `assert go(repo) == 0` to:

```python
    assert go(repo) == run.EXIT_CONTINUE
    assert "remainder {}..{} pending".format(shas[1][:7], shas[-1][:7]) in \
        (repo / "llake/.state/agents/run-1/agent.log").read_text()
```

In `test_split_lands_on_a_watched_commit_not_on_trivial_ones` (Task 2), change `assert go(repo) == 0` to `assert go(repo) == run.EXIT_CONTINUE`. `test_single_commit_skip_row_7` keeps `== 0` (its skip head is HEAD).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/lib/test_v3_run_e2e.py -q`
Expected: FAIL. `run.EXIT_CONTINUE` is missing, the hooks.log line lacks "pipeline error", and the regression run plans a split or keeps a counter.

- [ ] **Step 3: Implement** in `hooks/lib/ingest_v3/run.py`

Change the first docstring line to:

```python
"""The ingest v3 orchestrator (spec §2, design §5). Exit 0: finalized, or nothing to do. Exit 1: the cursor holds.
Exit 3 (EXIT_CONTINUE): a split or skip finalized short of HEAD; post-merge.sh runs the remainder at once.
```

After `STOP_STAGES = (...)` add:

```python
EXIT_CONTINUE = 3  # a finalized split or skip stopped short of HEAD: the hook continues with the remainder (§3)
```

After `_log_unrecovered` add:

```python
def _finished(project, rp, log):
    """0, or EXIT_CONTINUE when a finalized split or skip left the cursor short of HEAD."""
    if rp.kind not in ("split", "skip"):
        return 0
    now = git(project, "rev-parse", "HEAD").strip()
    if now == rp.head:
        return 0
    log.line("remainder {}..{} pending: continuing in this invocation".format(rp.head[:7], now[:7]))
    return EXIT_CONTINUE
```

In `_run`, immediately before `rp = plan.plan_run(project, llake, include, base, head)` add:

```python
        stale = plan.load_failures(llake)
        if stale and not plan.active_failures(llake, base):
            log.line("failure counter ignored (plugin {} -> {}, base {}): planning without it".format(
                stale.get("plugin") or "unrecorded", plan.plugin_version(), str(stale.get("base") or "")[:7]))
```

In the skip branch, replace its final `return 0` (after the `hooks_log(... "skipped: single-commit range ...")` call) with `return _finished(project, rp, log)`.

Replace the `InvalidBrief` handler with:

```python
        except InvalidBrief as exc:
            # deterministic: the same brief fails on any range, so it never counts toward split or skip
            plan.record_failure(llake, base, rp.head, "pipeline")
            raise HoldRun("pipeline error: invalid brief: {} (not counted toward split)".format(
                "; ".join(exc.errors[:3])))
```

Replace the final `return 0` of the finalized path (right after the `hooks_log(llake, "completed: agent …")` call) with `return _finished(project, rp, log)`.

In `hooks/lib/ingest_v3/cli.py`, change the `run` parser help to:

```python
    p = sub.add_parser("run", help="run ingest v3 once (called by hooks/lib/ingest-v3.sh); exit 0 done, "
                                   "1 cursor held, 3 split/skip finalized short of HEAD (continue)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/lib/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/ingest_v3/run.py hooks/lib/ingest_v3/cli.py tests/lib/test_v3_run_e2e.py
git commit -m "fix(ingest-v3): an invalid brief is a pipeline error; a finished split asks to continue (LOR-25)"
```

---

### Task 4: post-merge continuation run

**Files:**
- Modify: `hooks/lib/ingest-v3.sh` (after `run_ingest_v3`)
- Modify: `hooks/post-merge.sh` (v3 subshell, the watchdog block and the `run_ingest_v3` call)
- Test: `tests/hooks/test_post_merge_v3.sh`

**Interfaces:**
- Consumes: exit code 3 from `ingest-v3.py run` (Task 3); `generate_agent_id`, `kill_tree`, `AGENTS_DIR`, `MY_PID`, `V3_TIMEOUT`, `V3_WATCHDOG` from the caller's scope.
- Produces (bash): `V3_EXIT_CONTINUE=3`; `run_ingest_v3_watched` (returns the run's exit code); `next_v3_agent` (sets `V3_AGENT_ID V3_AGENT_DIR V3_AGENT_LOG V3_PID_FILE AGENT_LOG CURRENT_PID_FILE LLAKE_AGENT_ID` and writes `$MY_PID` to the new pid file).

- [ ] **Step 1: Write the failing tests** in `tests/hooks/test_post_merge_v3.sh`

Add these helpers after `new_project()`:

```bash
plugin_version() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$REPO_ROOT/.claude-plugin/plugin.json"
}

src_commit() {  # src_commit <project> <app.py content> <message>
  printf '%s\n' "$2" > "$1/src/app.py"
  git -C "$1" add -A
  git -C "$1" commit -q -m "$3"
}
```

Add the tests before the run list:

```bash
# LOR-25: a finalized split leaves the rest of the range to a second run in the same invocation.
test_v3_split_continues_with_the_remainder() {
  local proj base head
  proj=$(new_project v3); TMP_PROJECTS+=("$proj")
  base=$(cursor "$proj")
  src_commit "$proj" 'def loadProfile():' "c1"
  src_commit "$proj" 'def loadProfile(): pass' "c2"
  src_commit "$proj" 'def loadProfile(): return 1' "c3"
  src_commit "$proj" 'def loadProfile(): return 2' "c4"
  head=$(git -C "$proj" rev-parse HEAD)
  printf '{"base": "%s", "count": 2, "lastHead": "%s", "plugin": "%s"}\n' "$base" "$head" "$(plugin_version)" \
    > "$proj/llake/.state/ingest-failures.json"
  run_hook "$proj" V3_STUB_BRIEF="$V3_BRIEF"
  assert_eq "continue_cursor_at_head" "$head" "$(cursor "$proj")"
  assert_file_contains "continue_split_completed" "$proj/llake/.state/hooks.log" "v3 split"
  assert_file_contains "continue_logged" "$proj/llake/.state/hooks.log" "takes the rest of the range"
  assert_file_contains "continue_range_completed" "$proj/llake/.state/hooks.log" "v3 range"
  assert_eq "continue_two_agent_dirs" "2" "$(ls "$proj/llake/.state/agents" | wc -l | tr -d ' ')"
  assert_eq "continue_lock_released" "no" "$([ -d "$proj/llake/.state/post-merge.lock.d" ] && echo yes || echo no)"
}

# The kill trap and _agent_cleanup read these globals: a continuation must repoint all of them.
test_v3_next_agent_repoints_trap_globals() {
  local out
  out=$(
    tmp=$(mktemp -d -t llake-v3-next.XXXXXX)
    generate_agent_id() { echo "next-agent-1"; }
    AGENTS_DIR="$tmp"; MY_PID=4242
    # shellcheck source=../../hooks/lib/ingest-v3.sh
    source "$REPO_ROOT/hooks/lib/ingest-v3.sh"
    next_v3_agent
    echo "$V3_AGENT_ID|$V3_AGENT_DIR|$AGENT_LOG|$CURRENT_PID_FILE|$LLAKE_AGENT_ID|$(cat "$V3_PID_FILE")|$tmp"
    rm -rf "$tmp"
  )
  local tmp="${out##*|}"
  assert_eq "next_agent_globals" \
    "next-agent-1|$tmp/next-agent-1|$tmp/next-agent-1/agent.log|$tmp/next-agent-1/orchestrator.pid|next-agent-1|4242|$tmp" \
    "$out"
}
```

Add `test_v3_split_continues_with_the_remainder` and `test_v3_next_agent_repoints_trap_globals` to the run list (before `echo "PASS=$PASS FAIL=$FAIL"`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash tests/hooks/test_post_merge_v3.sh`
Expected: FAIL on `continue_cursor_at_head` (the cursor stops at the split point), `continue_logged`, `continue_range_completed`, `continue_two_agent_dirs`, and `next_agent_globals` (`next_v3_agent: command not found`).

- [ ] **Step 3: Implement**

In `hooks/lib/ingest-v3.sh`, after the `run_ingest_v3` function, add:

```bash
# `ingest-v3.py run` exit code for "a split or skip finalized short of HEAD" (run.EXIT_CONTINUE).
V3_EXIT_CONTINUE=3

# One v3 run under the hard watchdog, inside post-merge.sh's run subshell, for the current agent globals
# (V3_AGENT_ID V3_AGENT_DIR V3_AGENT_LOG V3_PID_FILE). Returns the run's exit code.
run_ingest_v3_watched() {
  local rc=0
  (
    sleep "$V3_WATCHDOG"
    if kill -0 "$MY_PID" 2>/dev/null; then kill -USR1 "$MY_PID" 2>/dev/null; fi
  ) &
  WATCHDOG_PID=$!
  run_ingest_v3 "$V3_AGENT_ID" "$V3_AGENT_DIR" "$V3_AGENT_LOG" "$V3_TIMEOUT" || rc=$?
  kill_tree "$WATCHDOG_PID"
  wait "$WATCHDOG_PID" 2>/dev/null
  rm -f "$V3_PID_FILE"
  return "$rc"
}

# Point the run subshell at a fresh agent for the continuation run: new ID, dir, log and pid file, plus the
# agent-run.sh globals the kill traps read (AGENT_LOG, CURRENT_PID_FILE, LLAKE_AGENT_ID), so a kill reverts
# and logs the continuation, not the finished run before it.
next_v3_agent() {
  V3_AGENT_ID=$(generate_agent_id)
  V3_AGENT_DIR="$AGENTS_DIR/$V3_AGENT_ID"
  mkdir -p "$V3_AGENT_DIR"
  V3_AGENT_LOG="$V3_AGENT_DIR/agent.log"
  V3_PID_FILE="$V3_AGENT_DIR/orchestrator.pid"
  echo "$MY_PID" > "$V3_PID_FILE"
  AGENT_LOG="$V3_AGENT_LOG"
  CURRENT_PID_FILE="$V3_PID_FILE"
  LLAKE_AGENT_ID="$V3_AGENT_ID"
}
```

In `hooks/post-merge.sh`, inside the v3 subshell, replace

```bash
    claim_v3_lock
    (
      sleep "$V3_WATCHDOG"
      if kill -0 "$MY_PID" 2>/dev/null; then kill -USR1 "$MY_PID" 2>/dev/null; fi
    ) &
    WATCHDOG_PID=$!
    run_ingest_v3 "$V3_AGENT_ID" "$V3_AGENT_DIR" "$V3_AGENT_LOG" "$V3_TIMEOUT"
    kill_tree "$WATCHDOG_PID"
    wait "$WATCHDOG_PID" 2>/dev/null
    rm -f "$V3_PID_FILE"
  ) &
```

with

```bash
    claim_v3_lock
    V3_RC=0
    run_ingest_v3_watched || V3_RC=$?
    # A finalized split or skip stops short of HEAD: run the rest now, once, under the same lock, with a fresh
    # agent and deadline. No gate: it already passed the full range, and the finalize just reset the clock.
    if [ "$V3_RC" -eq "$V3_EXIT_CONTINUE" ]; then
      next_v3_agent
      printf "%s | %-13s | continuing: v3 agent %s takes the rest of the range\n" \
        "$(date '+%Y-%m-%d %H:%M:%S')" "continue" "$V3_AGENT_ID" >> "$LOG_FILE"
      run_ingest_v3_watched || true
    fi
  ) &
```

(`V3_WATCHDOG` and `V3_TIMEOUT` are already set in the parent scope above the subshell; leave those lines as they are.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash tests/hooks/test_post_merge_v3.sh && bash tests/hooks/test_bash32_portability.sh && bash -n hooks/post-merge.sh && bash -n hooks/lib/ingest-v3.sh`
Expected: `FAIL=0` from both suites, no syntax errors. If `shellcheck` is installed, also run `shellcheck hooks/post-merge.sh hooks/lib/ingest-v3.sh` and expect no new warnings.

- [ ] **Step 5: Commit**

```bash
git add hooks/lib/ingest-v3.sh hooks/post-merge.sh tests/hooks/test_post_merge_v3.sh
git commit -m "fix(ingest-v3): continue with the rest of the range after a finalized split (LOR-25)"
```

---

### Task 5: Docs and version bump

**TDD exception (Decisions taken #13):** documentation and version strings only. No behaviour to drive with a failing test. `tests/lib/test_marketplace_manifest.py` already guards the version pair.

**Files:**
- Modify: `docs/specs/ingest-v3.md` lines 81, 94, 97, 107, 109, 404, 413-416
- Modify: `docs/adr/0001-ingest-v3-pipeline.md:32`
- Modify: `schema/operations.md:70`
- Modify: `CONTEXT.md:57`
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### Fixed`)
- Modify: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`

- [ ] **Step 1: Spec edits** in `docs/specs/ingest-v3.md`

Line 81, last cell `invalid brief: hold cursor, work failure` → `invalid brief: hold cursor, pipeline failure (counter cleared)`.

Line 94 → `| Split run | analysis failed twice with work failures on this base, same plugin version | a range run over base..split point (§3); the remainder runs next in the same invocation |`

Line 97 → `| Skip | the range's watched changes sit in one first-parent commit and it failed analysis twice | cursor advances with a `ranges[]` entry; no agent; the remainder runs next in the same invocation |`

Line 107 →

```markdown
**Failure counter.** `.state/ingest-failures.json` holds `{base, count, lastHead, plugin}`. It applies only while `base` is the current cursor and `plugin` is the running plugin version; a record from another version (or one without `plugin`) is ignored, so the first run after an upgrade plans the full range. Analysis work failures increment it and set `lastHead` to the head that failed; infra failures do not change it (§12). An invalid brief is a **pipeline failure**: deterministic, it fails the same on any range, so it holds the cursor and clears the counter instead of counting. A finalized run clears it.
```

Line 109 →

```markdown
**Range split.** When the counter reaches 2, the next run ingests base..split point over the range base..`lastHead`. The split point is the first-parent commit whose cumulative watched churn (added + deleted lines under `ingest.include`, per commit against its first parent, so a merge counts its branch's content) is closest to half the total. It is never the last commit with watched changes, and never a commit where base..commit nets to no watched change, so neither half is empty. On success the cursor advances to the split point and the run exits 3; `post-merge.sh` then runs the rest of the range at once, under the same lock, as a fresh run with its own deadline (once per invocation; the gate is not consulted again). A range whose watched changes sit in one first-parent commit cannot shrink: after two work failures it is skipped. The cursor advances past it, and the gap record gets a `ranges[]` entry with cause `analysis-failed` and the range's $0 hit lines as leads, so a human can see what was not ingested; the rest of the range follows in the same invocation as above.
```

Line 404: after `Anything else (budget, timeout, max turns, invalid output) is **work**.` insert ` An invalid brief at assembly is a **pipeline** failure (§3): it holds the cursor, clears the counter and is logged as `pipeline error`.`

Cursor table: row 4 last cell `counter cleared` → `counter cleared; the remainder runs next in the same invocation`. Replace row 5 with two rows and keep the following numbers as they are:

```markdown
| 5 | Analysis work failure | holds | counter +1 (same base and plugin version) |
| 5b | Invalid brief (pipeline failure) | holds | counter cleared |
```

Row 7 → `| 7 | Range with one watched first-parent commit failed twice | advances past it | `ranges[]` entry; the remainder runs next |`

- [ ] **Step 2: ADR, operations schema, glossary, changelog**

`docs/adr/0001-ingest-v3-pipeline.md:32`: replace `Analysis failures do hold it, and after 2 work failures on the same base the next run ingests half the range (a one-commit range is skipped and recorded).` with `Analysis failures do hold it. After 2 work failures on the same base and plugin version, the next run ingests the range up to its churn midpoint and continues with the rest; a range whose watched changes sit in one commit is skipped and recorded. An invalid brief is a pipeline failure: it holds the cursor but never counts toward split or skip (LOR-25).`

`schema/operations.md:70`: replace `an analysis failure holds it, counted in `.state/ingest-failures.json`; after two work failures on one base the next run ingests the first half of the range, and a one-commit range is skipped and recorded as a skipped range.` with `an analysis failure holds it, counted in `.state/ingest-failures.json` per base and plugin version; after two work failures the next run ingests the range up to its churn midpoint and then the rest in the same invocation, and a range whose watched changes sit in one commit is skipped and recorded as a skipped range. An invalid brief holds the cursor as a pipeline error and is never counted.`

`CONTEXT.md:57` → `- **Range split**: after repeated analysis work failure, ingesting the range up to its watched-churn midpoint and then the rest, so that a failing range shrinks instead of widening. Pipeline failures (an invalid brief) never split.`

`CHANGELOG.md`, first bullet under `## [Unreleased]` → `### Fixed`:

```markdown
- Ingest v3 no longer splits or skips a range because of a deterministic failure. An invalid brief now holds the cursor as a `pipeline error` without counting toward split or skip; the failure counter is tied to the plugin version, so the first run after an upgrade ingests the whole held range; the split point is chosen by watched-path churn and never lands on a slice with no watched changes; and a finished split continues with the rest of the range in the same `post-merge` invocation (LOR-25).
```

- [ ] **Step 3: Version bump** — `"version": "0.1.9"` → `"version": "0.1.10"` in `.claude-plugin/plugin.json` and in `.claude-plugin/marketplace.json` (`plugins[0].version`).

- [ ] **Step 4: Full verification**

Run: `python3 -m pytest tests/ -q && for t in tests/hooks/*.sh; do bash "$t" || echo "FAILED: $t"; done`
Expected: pytest all pass; no `FAILED:` lines.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/ingest-v3.md docs/adr/0001-ingest-v3-pipeline.md schema/operations.md CONTEXT.md CHANGELOG.md .claude-plugin/plugin.json .claude-plugin/marketplace.json
git commit -m "docs(ingest-v3): pipeline failures, churn split, continuation; bump 0.1.10 (LOR-25)"
```
