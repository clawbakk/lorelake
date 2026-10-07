"""End to end through run.run() with the fake claude: the cursor table (spec §12) and §18 tests."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from v3_helpers import REPO_ROOT, commit, git, make_project, page_text
from ingest_v3 import finalize, gaps, run, snapshots
from ingest_v3.common import dump_json, load_json, plugin_version
from ingest_v3.state import RunState

CLIENT = "The client calls fetchUserData on start"
BRIEF = json.dumps([{"path": "llake/wiki/arch/client.md", "kind": "direct", "severity": "major", "themes": ["T1"],
                     "reason": "loader renamed", "stale": [{"quote": CLIENT, "head": "src/app.py:1 loadProfile",
                                                             "severity": "major"}]}])
STUB_VARS = ("V3_STUB_BRIEF", "V3_STUB_RECALL", "V3_STUB_FAIL", "V3_STUB_INFRA", "V3_STUB_SLEEP", "V3_STUB_MARK",
             "V3_STUB_IGNORE_TERM", "V3_STUB_DECLARE", "V3_STUB_COST", "V3_STUB_OTHER_STALE",
             "V3_STUB_RECALL_CLOBBER", "V3_STUB_RECALL_EXTRA", "V3_STUB_STAY", "V3_STUB_PIDFILE",
             "LLAKE_V3_STOP_AFTER", "LLAKE_V3_FROZEN_BRIEF")


@pytest.fixture
def stages(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = bin_dir / "claude"
    shutil.copy(str(REPO_ROOT / "tests/hooks/fixtures/claude-v3-stub.py"), str(target))
    target.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    for var in STUB_VARS:
        monkeypatch.delenv(var, raising=False)
    log = tmp_path / "stages.log"
    monkeypatch.setenv("V3_STUB_LOG", str(log))
    return lambda: log.read_text().split() if log.exists() else []


def project(tmp_path, commits=1, pages=None, v3=None):
    pages = pages or {"arch/client.md": page_text("Client", "The HTTP client", CLIENT + ".\n\nIt is small."),
                      "arch/other.md": page_text("Other", "Other page", "Unrelated text.")}
    repo = make_project(tmp_path, src={"src/app.py": "def fetchUserData():\n    return 1\n"}, pages=pages, v3=v3)
    base = git(repo, "rev-parse", "HEAD").strip()
    shas = [commit(repo, {"src/app.py": "def loadProfile():\n    return {}\n".format(i + 1)}, "rename {}".format(i))
            for i in range(commits)]
    return repo, base, shas


def go(repo, agent="run-1", deadline=None):
    return run.run(str(repo), agent, str(repo / "llake/.state/agents" / agent), deadline or time.time() + 600,
                   today="2026-10-03")


def cursor(repo):
    return (repo / "llake/last-ingest-sha").read_text().strip()


def hooks(repo):
    return (repo / "llake/.state/hooks.log").read_text()


def seed_failures(repo, base, count, last):
    dump_json(str(repo / "llake/.state/ingest-failures.json"),
              {"base": base, "count": count, "lastHead": last, "plugin": plugin_version()})


def test_range_run_finalizes(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    page = (repo / "llake/wiki/arch/client.md").read_text()
    assert "the corrected statement" in page and "updated: 2026-10-03" in page
    doc = load_json(str(repo / "llake/ingest-gaps.json"))
    assert gaps.validate(doc, str(repo / "llake")) == [] and doc["gaps"] == []
    assert "ingest | {}..{}: v3 — 1 updated, 0 created, 0 gaps (0 major)".format(base[:7], shas[-1][:7]) in \
        (repo / "llake/log.md").read_text()
    assert stages() == ["analysis", "recall", "writer-b01"]
    assert "**FAIL**" not in (repo / "llake/.state/agents/run-1/report.md").read_text()
    assert "completed: agent run-1 v3 range" in hooks(repo)
    assert hooks(repo).count("agent-done") == 1
    assert not (repo / "llake/.state/ingest-failures.json").exists()


def test_empty_brief_finalizes(tmp_path, stages):
    repo, base, shas = project(tmp_path, pages={"arch/other.md": page_text("Other", "Other page", "Unrelated.")})
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    assert "v3 — 0 updated, 0 created, 0 gaps (0 major)" in (repo / "llake/log.md").read_text()
    assert stages() == ["analysis", "recall"]


def test_no_watched_changes_is_empty_row_1(tmp_path, stages):
    repo, base, _ = project(tmp_path, commits=0)
    head = commit(repo, {"README.md": "docs\n"}, "docs")
    assert go(repo) == 0
    assert cursor(repo) == head and stages() == []
    assert "empty: nothing to ingest" in hooks(repo)
    assert not (repo / "llake/.state/agents/run-1/pre").exists()


def owed_gap(repo, attempts=1, cause="declared"):
    dump_json(str(repo / "llake/ingest-gaps.json"), {"version": 1, "asOf": "x", "agent": "old", "date": "d",
        "ranges": [], "gaps": [{"page": "wiki/arch/client.md", "severity": "major", "cause": cause, "since": "s0",
                                "attempts": attempts, "stuck": attempts >= 3,
                                "claims": [{"quote": CLIENT, "head": "h", "severity": "major", "source": "brief"}]}]})


def test_gap_only_run_row_2(tmp_path, stages):
    repo, base, _ = project(tmp_path, commits=0)
    owed_gap(repo)
    head = commit(repo, {"README.md": "docs\n"}, "docs")
    assert go(repo) == 0
    assert cursor(repo) == head and stages() == ["writer-b01"]
    log = (repo / "llake/log.md").read_text()
    assert "ingest | gap-only at {}: v3".format(head[:7]) in log and "Carried gaps resolved: [[client]]" in log


def test_analysis_work_failure_holds_row_5(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_FAIL", "analysis")
    assert go(repo) == 1
    assert cursor(repo) == base
    assert load_json(str(repo / "llake/.state/ingest-failures.json")) == {
        "base": base, "count": 1, "lastHead": shas[-1], "plugin": plugin_version()}
    assert "held: agent run-1 v3 (analysis work failure" in hooks(repo)


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
    assert "range with one watched commit" in hooks(repo)


def test_analysis_infra_failure_holds_without_counting_row_6(tmp_path, stages, monkeypatch):
    repo, base, _ = project(tmp_path)
    monkeypatch.setenv("V3_STUB_INFRA", "analysis")
    assert go(repo) == 1
    assert cursor(repo) == base and not (repo / "llake/.state/ingest-failures.json").exists()


def test_split_run_advances_to_midpoint_row_4(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path, commits=4)
    seed_failures(repo, base, 2, shas[-1])
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    assert go(repo) == run.EXIT_CONTINUE
    assert "remainder {}..{} pending".format(shas[1][:7], shas[-1][:7]) in \
        (repo / "llake/.state/agents/run-1/agent.log").read_text()
    assert cursor(repo) == shas[1]
    assert not (repo / "llake/.state/ingest-failures.json").exists()
    assert "{}..{}: v3".format(base[:7], shas[1][:7]) in (repo / "llake/log.md").read_text()


def test_single_commit_skip_row_7(tmp_path, stages):
    repo, base, shas = project(tmp_path)
    seed_failures(repo, base, 2, shas[0])
    assert go(repo) == 0
    assert cursor(repo) == shas[0] and stages() == []
    doc = load_json(str(repo / "llake/ingest-gaps.json"))
    assert doc["ranges"][0]["base"] == base and doc["ranges"][0]["leads"][0].startswith(
        "wiki/arch/client.md: `fetchUserData` L")
    assert "skipped: analysis failed twice" in (repo / "llake/log.md").read_text()


def test_writer_failed_twice_advances_with_gap_row_8(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("V3_STUB_FAIL", "writer-b01,writer-b01-s1")
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    g = load_json(str(repo / "llake/ingest-gaps.json"))["gaps"][0]
    assert (g["cause"], g["attempts"], g["severity"]) == ("writer-failed", 1, "major")
    assert CLIENT in (repo / "llake/wiki/arch/client.md").read_text()


def test_run_cap_gaps_row_8(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path, v3={"maxRunBudgetUsd": 2.5})
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    g = load_json(str(repo / "llake/ingest-gaps.json"))["gaps"][0]
    assert (g["cause"], g["attempts"]) == ("run-cap", 0)
    assert "writer-b01" not in stages()


def test_infra_during_writing_row_8(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("V3_STUB_INFRA", "writer-b01")
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    assert load_json(str(repo / "llake/ingest-gaps.json"))["gaps"][0]["cause"] == "infra"


def test_finalize_failure_holds_row_10(tmp_path, stages, monkeypatch):
    repo, base, _ = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(gaps, "save", boom)
    assert go(repo) == 1
    assert cursor(repo) == base
    assert "internal error" in hooks(repo)


def test_stuck_after_three_attempts(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    owed_gap(repo, attempts=2, cause="writer-failed")
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("V3_STUB_FAIL", "writer-b01,writer-b01-s1")
    assert go(repo) == 0
    g = load_json(str(repo / "llake/ingest-gaps.json"))["gaps"][0]
    assert (g["attempts"], g["stuck"]) == (3, True)
    assert "— needs a human" in (repo / "llake/log.md").read_text()


def test_stop_after_and_frozen_brief(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("LLAKE_V3_STOP_AFTER", "brief")
    assert go(repo, agent="run-1") == 1
    assert cursor(repo) == base and stages() == ["analysis", "recall"]
    monkeypatch.delenv("LLAKE_V3_STOP_AFTER")
    monkeypatch.setenv("LLAKE_V3_FROZEN_BRIEF", str(repo / "llake/.state/agents/run-1"))
    assert go(repo, agent="run-2") == 0
    assert stages() == ["analysis", "recall", "recall", "writer-b01"]
    assert cursor(repo) == shas[-1]


def test_recall_failure_is_ignored(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("V3_STUB_RECALL", "{not json")
    assert go(repo) == 0
    assert (repo / "llake/.state/agents/run-1/brief/rejected-recall/recall-1.json").exists()
    monkeypatch.setenv("V3_STUB_FAIL", "recall")
    monkeypatch.setenv("V3_STUB_RECALL", json.dumps([{"path": "llake/wiki/arch/other.md", "severity": "minor",
                                                      "reason": "r", "stale": []}]))
    commit(repo, {"src/app.py": "def loadProfile():\n    return 9\n"}, "again")
    assert go(repo, agent="run-2") == 0
    assert (repo / "llake/.state/agents/run-2/brief/rejected-recall/recall-1.json").exists()


def agent_log(repo, agent="run-1"):
    return (repo / "llake/.state/agents" / agent / "agent.log").read_text()


OTHER = json.dumps({"path": "llake/wiki/arch/other.md", "kind": "direct", "severity": "minor", "themes": ["T1"],
                    "reason": "wording", "stale": [{"quote": "Unrelated text", "head": "src/app.py:1",
                                                    "severity": "minor"}]})
# client.md keeps a second mention of the removed name the brief does not quote: the stub writer leaves it, so
# the removed-name check flags the page after writing and the fix round has work.
RESIDUE_PAGES = {"arch/client.md": page_text("Client", "The HTTP client",
                                             CLIENT + ".\n\nOn failure it retries fetchUserData once."),
                 "arch/other.md": page_text("Other", "Other page", "Unrelated text.")}


def two_bundle_brief():
    return "[" + BRIEF[1:-1] + ", " + OTHER + "]"


def test_flagged_page_gets_a_fixer(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path, pages=RESIDUE_PAGES)
    monkeypatch.setenv("V3_STUB_BRIEF", two_bundle_brief())
    assert go(repo) == 0
    assert stages() == ["analysis", "recall", "writer-b01", "writer-b02", "fixer-b01"]
    assert cursor(repo) == shas[-1]


def test_infra_during_writing_skips_the_fix_round(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path, pages=RESIDUE_PAGES)
    monkeypatch.setenv("V3_STUB_BRIEF", two_bundle_brief())
    monkeypatch.setenv("V3_STUB_INFRA", "writer-b02")
    assert go(repo) == 0
    assert stages() == ["analysis", "recall", "writer-b01", "writer-b02"]
    assert "fix round skipped: writing stopped (infra)" in agent_log(repo)
    assert cursor(repo) == shas[-1]


def test_fix_round_off_spawns_no_fixer(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path, pages=RESIDUE_PAGES, v3={"fixRound": "off"})
    monkeypatch.setenv("V3_STUB_BRIEF", two_bundle_brief())
    assert go(repo) == 0
    assert stages() == ["analysis", "recall", "writer-b01", "writer-b02"]


def test_recall_skipped_when_it_would_pass_the_run_cap(tmp_path, stages, monkeypatch):
    # analysis spends $0.05; recall's $1.00 budget on top passes the $1.00 cap, so recall is not spawned
    repo, base, shas = project(tmp_path, v3={"maxRunBudgetUsd": 1.0})
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    assert go(repo) == 0
    assert stages() == ["analysis"]
    assert "recall skipped: run cap ($0.05 spent + $1.00 budget > $1.00 cap)" in agent_log(repo)
    assert cursor(repo) == shas[-1]


def test_recall_runs_when_it_exactly_fits_the_run_cap(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path, v3={"maxRunBudgetUsd": 1.05})
    assert go(repo) == 0
    assert stages() == ["analysis", "recall"]


def test_dir_rule_grants_exactly_one_run_owned_directory(tmp_path):
    repo = make_project(tmp_path)
    agent_dir = repo / "llake/.state/agents/run-1"
    state = RunState(str(repo), str(agent_dir))
    brief_dir = agent_dir / "brief"
    brief_dir.mkdir(parents=True)
    assert run._dir_rule(state, str(brief_dir)) == "Edit(/" + str(repo) + "/llake/.state/agents/run-1/brief/**)"
    for bad in (agent_dir, repo / "llake/wiki", repo / "llake/.state/agents/run-2/brief", agent_dir / "br*ef"):
        with pytest.raises(ValueError):
            run._dir_rule(state, str(bad))


def test_dir_rule_refuses_an_agent_dir_outside_the_agents_dir(tmp_path):
    repo = make_project(tmp_path)
    state = RunState(str(repo), str(repo / "llake/wiki"))
    with pytest.raises(ValueError):
        run._dir_rule(state, str(repo / "llake/wiki/arch"))


def test_split_lands_on_a_watched_commit_not_on_trivial_ones(tmp_path, stages):
    repo, base, _ = project(tmp_path, commits=0)
    commit(repo, {"README.md": "one\n"}, "docs 1")
    commit(repo, {"README.md": "two\n"}, "docs 2")
    c3 = commit(repo, {"src/app.py": "def loadProfile():\n    return 1\n"}, "rename")
    c4 = commit(repo, {"src/app.py": "def loadProfile():\n    return 2\n"}, "tweak")
    seed_failures(repo, base, 2, c4)
    assert go(repo) == run.EXIT_CONTINUE
    assert cursor(repo) == c3 and stages() == ["analysis", "recall", "writer-b01"]
    assert "ingest | {}..{}: v3".format(base[:7], c3[:7]) in (repo / "llake/log.md").read_text()
    assert not (repo / "llake/.state/ingest-failures.json").exists()
    assert "completed: agent run-1 v3 split" in hooks(repo)


def test_killed_skip_is_revertible(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    seed_failures(repo, base, 2, shas[0])
    log_before = (repo / "llake/log.md").read_text()

    def killed(*a, **k):
        raise RuntimeError("killed before the cursor write")
    monkeypatch.setattr(finalize, "write_cursor", killed)
    assert go(repo) == 1
    assert (repo / "llake/ingest-gaps.json").exists()
    result = snapshots.revert_run(str(repo), str(repo / "llake/.state/agents/run-1"))
    assert result["unrestored"] == []
    assert not (repo / "llake/ingest-gaps.json").exists()
    assert (repo / "llake/log.md").read_text() == log_before
    assert cursor(repo) == base


def test_unrecovered_dead_run_is_logged(tmp_path, stages):
    repo, base, _ = project(tmp_path, commits=0)
    dump_json(str(repo / "llake/.state/agents/old-run/run.json"),
              {"kind": "range", "inFlight": {"wiki/arch/client.md": str(tmp_path / "gone-snapshot")},
               "finalized": False, "aborted": False})
    commit(repo, {"README.md": "docs\n"}, "docs")
    assert go(repo) == 0
    assert "dead run old-run left unrecovered: wiki/arch/client.md" in agent_log(repo)


def test_cli_run_returns_the_run_exit_code(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_INFRA", "analysis")

    def cli(agent):
        return subprocess.run([sys.executable, str(REPO_ROOT / "hooks/lib/ingest-v3.py"), "run",
                               "--project-root", str(repo), "--agent-id", agent,
                               "--agent-dir", str(repo / "llake/.state/agents" / agent),
                               "--deadline", str(time.time() + 600)], capture_output=True, text=True)
    held = cli("cli-1")
    assert held.returncode == 1, held.stderr
    monkeypatch.delenv("V3_STUB_INFRA")
    done = cli("cli-2")
    assert done.returncode == 0, done.stderr
    assert cursor(repo) == shas[-1]


def test_report_failure_after_finalize_still_completes(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)

    def boom(*a, **k):
        raise OSError("report disk full")
    monkeypatch.setattr(run, "write_report", boom)
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    assert "completed: agent run-1 v3 range" in hooks(repo) and "held" not in hooks(repo)
    assert "report.md not written" in agent_log(repo)


def test_recall_clobbering_an_analysis_file_cannot_fail_the_run(tmp_path, stages, monkeypatch):
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("V3_STUB_RECALL_CLOBBER", "1")
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    assert stages() == ["analysis", "recall", "writer-b01"]
    assert (repo / "llake/.state/agents/run-1/brief/pages/batch-1.json").read_text() == BRIEF
    assert "the corrected statement" in (repo / "llake/wiki/arch/client.md").read_text()
    assert "recall changed analysis file batch-1.json: restored" in agent_log(repo)


def test_recall_file_not_named_recall_is_set_aside(tmp_path, stages, monkeypatch):
    # schema-valid, but a new page without title and description: assemble rejects it as an analysis error
    repo, base, shas = project(tmp_path)
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("V3_STUB_RECALL_EXTRA", json.dumps([{"path": "llake/wiki/arch/fresh.md", "kind": "new",
                                                            "severity": "minor", "reason": "r", "stale": []}]))
    assert go(repo) == 0
    assert cursor(repo) == shas[-1]
    assert stages() == ["analysis", "recall", "writer-b01"]
    assert (repo / "llake/.state/agents/run-1/brief/rejected-recall/extra.json").exists()
    assert not (repo / "llake/.state/agents/run-1/brief/pages/extra.json").exists()
    assert "recall file extra.json set aside (not named recall-*)" in agent_log(repo)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.parametrize("sig,stage", [("SIGTERM", "writer-b01"), ("SIGHUP", "writer-b01"),
                                       ("SIGINT", "writer-b01"), ("SIGTERM", "analysis")])
def test_signal_to_a_running_run_kills_its_agents_and_reverts(tmp_path, stages, monkeypatch, sig, stage):
    """A kill signal to ingest-v3.py run (no bash trap involved) must leave no agent behind (they run in
    their own session, out of reach of a tree kill once the run is gone) and restore the run's pages."""
    import signal as _signal
    repo, base, _ = project(tmp_path)
    mark, pidfile = tmp_path / "mark", tmp_path / "stub.pid"
    monkeypatch.setenv("V3_STUB_BRIEF", BRIEF)
    monkeypatch.setenv("V3_STUB_SLEEP", stage + ":60")
    monkeypatch.setenv("V3_STUB_MARK", str(mark))
    monkeypatch.setenv("V3_STUB_PIDFILE", str(pidfile))
    monkeypatch.setenv("V3_STUB_STAY", "1")
    agent_dir = repo / "llake/.state/agents/run-1"
    proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "hooks/lib/ingest-v3.py"), "run", "--project-root",
                             str(repo), "--agent-id", "run-1", "--agent-dir", str(agent_dir),
                             "--deadline", str(time.time() + 600)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stub = None
    try:
        t0 = time.time()
        while not (mark.exists() and pidfile.exists() and pidfile.read_text().strip()) and time.time() - t0 < 30:
            time.sleep(0.1)
        assert mark.exists(), "the stub never reached its sleep"
        stub = int(pidfile.read_text())
        proc.send_signal(getattr(_signal, sig))
        assert proc.wait(timeout=60) != 0
        t1 = time.time()
        while _alive(stub) and time.time() - t1 < 3:
            time.sleep(0.1)
        assert not _alive(stub), "an agent survived its run"
        assert CLIENT in (repo / "llake/wiki/arch/client.md").read_text()
        assert cursor(repo) == base
        assert RunState(repo, str(agent_dir)).journal["aborted"] is True
        assert "killed by" in (agent_dir / "agent.log").read_text()
    finally:
        if proc.poll() is None:
            proc.kill()
        if stub is not None and _alive(stub):
            os.kill(stub, _signal.SIGKILL)
