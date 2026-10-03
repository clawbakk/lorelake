"""Fix round: findings, one fixer per bundle, success and revert-to-post-write."""
import pytest

from v3_helpers import Cfg, make_project, page_text, write
from ingest_v3 import fix, snapshots
from ingest_v3.common import dump_json
from ingest_v3.state import RunState

X, Y = "wiki/arch/x.md", "wiki/arch/y.md"


CFG = Cfg({"maxRunBudgetUsd": 40.0, "writerConcurrency": 4, "writerBudgetUsd": 3.0, "verifierBudgetUsd": 1.0,
           "verifierMode": "off", "writeMode": "edit"})


class FakeAgent:
    def __init__(self, job, cls="none", structured=None, effect=None):
        self.job, self.cls, self.structured, self.effect, self.killed, self.done = job, cls, structured, effect, None, False

    def poll(self):
        if not self.done and self.effect:
            self.effect()
        self.done = True
        return True

    def kill(self, why):
        self.killed = why

    def first_turn_done(self):
        return True

    def summary(self):
        return {"stage": self.job["stage"], "class": self.cls, "reason": "fake", "cost_usd": 0.1,
                "structured": self.structured if self.cls == "none" else None, "writes": [],
                "permission_denials": [], "killed": None}


@pytest.fixture
def proj(tmp_path):
    repo = make_project(tmp_path, pages={"arch/x.md": page_text("X", "x", "old x"),
                                         "arch/y.md": page_text("Y", "y", "old y")})
    state = RunState(repo, repo / "llake/.state/agents/run")
    snapshots.take_pre(state)
    snap = state.dir + "/bundles/b01/snapshot"
    snapshots.snapshot(state, [X, Y], snap)
    write(repo, "llake/" + X, page_text("X", "x", "written x"))
    write(repo, "llake/" + Y, page_text("Y", "y", "written y"))
    snapshots.settle(state, [X, Y])
    for p in (X, Y):
        state.pages[p] = {"page": p, "severity": "major", "outcome": "corrected", "status": "corrected",
                          "changed": True, "bundle": "b01", "snap": snap, "rejected": ["r0"], "history": [],
                          "verifier": None, "claimsLeft": []}
    state.save()
    return repo, state


BRIEF = {"themes": [], "pages": [{"page": X, "severity": "major", "claims": []},
                                 {"page": Y, "severity": "major", "claims": []}]}


def flag(quote):
    return {"quote": quote, "head": "h", "severity": "minor", "source": "check:anchor"}


def test_fix_findings_merge_checks_and_verifier(proj):
    repo, state = proj
    dump_json(state.dir + "/checks.json", {"flags": {X: [flag("written x")]}})
    state.pages[Y]["verifier"] = {"accuracy": [{"quote": "written y", "head": "h", "severity": "major"},
                                               {"quote": "absent", "head": "h", "severity": "major"}],
                                  "residual": []}
    found = fix.fix_findings(state)
    assert found[X] == [flag("written x")]
    assert [f["source"] for f in found[Y]] == ["verifier:accuracy"]


def test_unchanged_page_gets_no_fixer(proj):
    repo, state = proj
    state.pages[X]["changed"] = False
    dump_json(state.dir + "/checks.json", {"flags": {X: [flag("written x")]}})
    assert fix.fix_findings(state) == {}


def test_no_findings_spawns_nothing(proj):
    repo, state = proj
    dump_json(state.dir + "/checks.json", {"flags": {}})

    def spawn(job):
        raise AssertionError("no fixer expected")
    assert fix.run_fix_round(state, CFG, BRIEF, "a", "b", 1e12, spawn=spawn, sleep=lambda _: None) is None


def test_one_fixer_per_bundle_success(proj):
    repo, state = proj
    dump_json(state.dir + "/checks.json", {"flags": {X: [flag("written x")], Y: [flag("written y")]}})
    jobs = []
    structured = {"pages": [
        {"page": "llake/" + X, "status": "corrected", "claimsLeft": [], "rejected": ["r1"], "note": ""},
        {"page": "llake/" + Y, "status": "declared-gap", "rejected": [], "note": "",
         "claimsLeft": [{"quote": "written y", "head": "h", "severity": "minor"}]}], "otherStale": []}

    def spawn(job):
        jobs.append(job)
        return FakeAgent(job, structured=structured,
                         effect=lambda: write(repo, "llake/" + X, page_text("X", "x", "fixed x")))
    fix.run_fix_round(state, CFG, BRIEF, "a", "b", 1e12, spawn=spawn, sleep=lambda _: None)
    assert [j["stage"] for j in jobs] == ["fixer-b01"] and jobs[0]["pages"] == [X, Y]
    assert jobs[0]["findings"][X] == [flag("written x")]
    assert state.pages[X]["fixed"] and state.pages[X]["rejected"] == ["r0", "r1"]
    assert state.pages[X]["fixSnap"].endswith("bundles/b01/fix-snapshot")
    assert state.pages[Y]["outcome"] == "declared" and state.pages[Y]["claimsLeft"][0]["quote"] == "written y"
    assert "fixed x" in (repo / "llake" / X).read_text()


def test_fixer_failure_reverts_to_post_write(proj):
    repo, state = proj
    dump_json(state.dir + "/checks.json", {"flags": {X: [flag("written x")]}})
    fix.run_fix_round(state, CFG, BRIEF, "a", "b", 1e12,
                      spawn=lambda job: FakeAgent(job, cls="work",
                                                  effect=lambda: write(repo, "llake/" + X, "garbage\n")),
                      sleep=lambda _: None)
    assert "written x" in (repo / "llake" / X).read_text()
    assert "reverted to the post-write state" in state.pages[X]["history"][-1]
    assert state.pages[X]["outcome"] == "corrected"
