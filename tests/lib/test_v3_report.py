"""report.md: stage table and the shakedown run checks."""
import json

import pytest

from v3_helpers import make_project, page_text, write
from ingest_v3 import report, snapshots
from ingest_v3.state import RunState

X = "wiki/arch/x.md"


class Cfg(dict):
    def get(self, key):
        return self[key]


CFG = Cfg({"maxRunBudgetUsd": 40.0, "timeoutSeconds": 3600, "analysisBudgetUsd": 10.0, "recallBudgetUsd": 1.0,
           "writerBudgetUsd": 3.0, "verifierBudgetUsd": 1.0})
BRIEF = {"themes": [], "pages": [{"page": X, "severity": "major", "claims": []}]}


def gap_doc(*gaps):
    return {"version": 1, "asOf": "h", "agent": "run", "date": "d", "gaps": list(gaps), "ranges": []}


def x_gap(cause):
    return {"page": X, "severity": "major", "cause": cause, "since": "s0", "attempts": 1, "stuck": False,
            "claims": [{"quote": "x body", "head": "src/x.py:1", "severity": "major", "source": "brief"}]}


@pytest.fixture
def run(tmp_path):
    repo = make_project(tmp_path, pages={"arch/x.md": page_text("X", "x", "x body")})
    state = RunState(repo, repo / "llake/.state/agents/run")
    snapshots.take_pre(state)
    state.journal["owned"] = [X]
    state.record_stage({"stage": "writer-b01", "class": "none", "reason": "success", "cost_usd": 0.5, "turns": 3,
                        "tokens": {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4},
                        "first_turn": {"cache_read": 0, "cache_write": 4}, "peak_context": 1000, "wall_s": 9.0},
                       "writer")
    state.pages = {X: {"page": X, "severity": "major", "outcome": "corrected", "bundle": "b01", "history": []}}
    state.save()
    write(repo, "llake/ingest-gaps.json", json.dumps(gap_doc()))
    return repo, state


def checks(state, cfg=CFG, summary=None, wall=100.0):
    return report.run_check_lines(state, cfg, BRIEF, summary or {"resolved": []}, "range", "a" * 40, "b" * 40,
                                  [], [], wall)


def test_clean_run_passes(run):
    repo, state = run
    text = report.write_report(state, CFG, BRIEF, {"resolved": []}, "range", "a" * 40, "b" * 40, [], [], 100.0)
    assert "**FAIL**" not in text
    assert "**PASS** gap record valid" in text
    assert "| writer-b01 | writer | none | 0.500 |" in text
    assert "cursor aaaaaaa \u2192 bbbbbbb (range run)" in text
    assert (repo / "llake/.state/agents/run/report.md").read_text() == text


def test_failures_are_reported(run):
    repo, state = run
    state.ledger["stages"][0]["deniedPaths"] = [state.abs(X)]
    state.ledger["surface"] = [{"stage": "writer-b01", "path": "/p/src/rogue.py", "action": "reported-outside-llake"}]
    state.pages[X]["outcome"] = "run-cap"
    lines = report.run_check_lines(state, Cfg(dict(CFG, maxRunBudgetUsd=0.1)), BRIEF, {"resolved": []}, "range",
                                   "a" * 40, "b" * 40, [], [" M src/app.py"], 100.0)
    fails = [l for l in lines if l.startswith("- **FAIL**")]
    joined = "\n".join(fails)
    assert "run cap" in joined and "permission denials" in joined and "outside llake/: /p/src/rogue.py" in joined
    assert "every major brief page" in joined


def test_major_no_change_is_a_warning(run):
    repo, state = run
    state.pages[X]["outcome"] = "no-change"
    lines = checks(state)
    assert any(l.startswith("- **WARN** major pages ended no-change") for l in lines)


def test_git_status_outside_llake(run):
    repo, state = run
    write(repo, "llake/log.md", "changed\n")
    write(repo, "src/app.py", "changed\n")
    assert report.git_status_outside_llake(str(repo)) == [" M src/app.py"]


@pytest.mark.parametrize("outcome", ["corrected", "no-change"])
def test_corrected_page_with_a_failure_gap_fails(run, outcome):
    repo, state = run
    state.pages[X]["outcome"] = outcome
    write(repo, "llake/ingest-gaps.json", json.dumps(gap_doc(x_gap("writer-failed"))))
    fails = [l for l in checks(state) if l.startswith("- **FAIL**")]
    assert any("corrected or no-change" in l and X in l and "writer-failed" in l for l in fails)


@pytest.mark.parametrize("cause", ["flagged", "unverified"])
def test_corrected_page_with_flag_gap_passes(run, cause):
    repo, state = run
    write(repo, "llake/ingest-gaps.json", json.dumps(gap_doc(x_gap(cause))))
    assert not [l for l in checks(state) if l.startswith("- **FAIL**")]


def test_unrestored_pages_are_warned(run):
    repo, state = run
    state.ledger["unrestored"] = [{"stage": "writer-b01", "page": X}, {"stage": "checks-final", "page": "wiki/a/y.md"}]
    lines = checks(state)
    warn = [l for l in lines if l.startswith("- **WARN**") and "unrestored" in l]
    assert len(warn) == 2
    assert any("writer-b01" in l and X in l for l in warn)
    assert any("checks-final" in l and "wiki/a/y.md" in l for l in warn)


def test_no_unrestored_passes(run):
    repo, state = run
    assert any(l.startswith("- **PASS**") and "restored" in l for l in checks(state))


def test_analysis_budget_above_run_cap_warns(run):
    repo, state = run
    lines = checks(state, Cfg(dict(CFG, maxRunBudgetUsd=5.0)))
    assert any(l.startswith("- **WARN**") and "analysis" in l and "not capped" in l for l in lines)
    assert not any("not capped" in l for l in checks(state))


def test_git_status_change_outside_llake_is_a_warning_not_a_failure(run):
    """The user may edit the project during a long run: a git-status change no v3 stream wrote is reported,
    never failed (only stream-attributed writes outside llake/ fail)."""
    repo, state = run
    lines = report.run_check_lines(state, CFG, BRIEF, {"resolved": []}, "range", "a" * 40, "b" * 40,
                                   [" M README.md"], [" M README.md", " M src/app.py", "?? notes.txt"], 100.0)
    assert not any(l.startswith("- **FAIL**") for l in lines)
    assert "- **PASS** no writes outside llake/" in lines
    warn = [l for l in lines if "not attributed to v3" in l]
    assert len(warn) == 1 and warn[0].startswith("- **WARN**")
    assert "src/app.py" in warn[0] and "notes.txt" in warn[0] and "README.md" not in warn[0]


def test_stream_attributed_write_outside_llake_fails(run):
    repo, state = run
    state.ledger["surface"] = [{"stage": "writer-b01", "path": "/p/src/rogue.py", "action": "reported-outside-llake"}]
    lines = checks(state)
    assert "- **FAIL** no writes outside llake/: /p/src/rogue.py" in lines
    assert not any("not attributed to v3" in l for l in lines)
