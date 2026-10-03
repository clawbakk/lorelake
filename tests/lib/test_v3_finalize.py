"""Finalize: updated:, indexes, gap record, log entry, cursor; skip record; failure holds the cursor."""
import json
import os

import pytest

from v3_helpers import Cfg, make_project, page_text, write
from ingest_v3 import common, finalize, gaps, snapshots
from ingest_v3.common import dump_json
from ingest_v3.state import RunState

X, Y, NEW = "wiki/arch/x.md", "wiki/arch/y.md", "wiki/arch/new.md"
IDX = "wiki/arch/arch.md"
B, H = "a" * 40, "b" * 40

CFG = Cfg({"verifierMode": "off"})


@pytest.fixture
def run(tmp_path):
    repo = make_project(tmp_path, pages={"arch/x.md": page_text("X", "old desc x", "The loop is unbounded."),
                                         "arch/y.md": page_text("Y", "y desc", "Body y stays."),
                                         "arch/z.md": page_text("Z", "z desc", "Z text.")})
    state = RunState(repo, repo / "llake/.state/agents/run-7")
    snapshots.take_pre(state)
    snap = state.dir + "/bundles/b01/snapshot"
    snapshots.snapshot(state, [X, Y, NEW], snap)
    write(repo, "llake/" + X, page_text("X", "new desc x", "The loop is capped."))
    write(repo, "llake/" + NEW, page_text("New", "a new page", "Fresh."))
    snapshots.settle(state, [X, Y, NEW])
    base = {"bundle": "b01", "snap": snap, "history": [], "rejected": [], "claimsLeft": [], "unverified": False,
            "verifier": None, "attempts": 0, "since": None, "carried": False}
    state.pages = {X: dict(base, page=X, severity="major", outcome="corrected", status="corrected", changed=True),
                   NEW: dict(base, page=NEW, severity="minor", outcome="corrected", status="corrected",
                             changed=True, create=True),
                   Y: dict(base, page=Y, severity="minor", outcome="no-change", status="no-change", changed=False)}
    state.save()
    dump_json(state.dir + "/checks.json", {"flags": {}})
    brief = {"themes": [{"id": "T1", "title": "Loop capped", "summary": "s"}], "blurbs": [], "pages": [
        {"page": X, "severity": "major", "claims": [{"quote": "The loop is unbounded", "head": "src/l.py:3",
                                                      "severity": "major", "quoteFound": True}]},
        {"page": NEW, "severity": "minor", "claims": [], "create": True},
        {"page": Y, "severity": "minor", "claims": [{"quote": "Body y stays", "head": "h", "severity": "minor",
                                                      "quoteFound": True}]}]}
    return repo, state, brief


def gap_doc(repo):
    return json.loads((repo / "llake/ingest-gaps.json").read_text())


def test_set_updated():
    text = page_text("T", "d", "b")
    assert "updated: 2026-10-03" in finalize.set_updated(text, "2026-10-03")
    no_updated = text.replace("updated: 2026-01-01\n", "")
    out = finalize.set_updated(no_updated, "2026-10-03")
    assert "created: 2026-01-01\nupdated: 2026-10-03\n" in out
    assert "updated: 2026-10-03\r\n" in finalize.set_updated(text.replace("\n", "\r\n"), "2026-10-03")


def test_set_updated_leaves_page_without_frontmatter():
    assert finalize.set_updated("Just text.\n", "2026-10-03") == "Just text.\n"
    unterminated = "---\ntitle: x\nupdated: 2026-01-01\n"
    assert finalize.set_updated(unterminated, "2026-10-03") == unterminated


def test_changed_page_without_frontmatter_is_left_untouched(run):
    repo, state, brief = run
    write(repo, "llake/" + X, "No frontmatter here.\n\nThe loop is capped.\n")
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    assert (repo / "llake" / X).read_text() == "No frontmatter here.\n\nThe loop is capped.\n"
    assert "[[x]] (updated)" in (repo / "llake/log.md").read_text()


def test_finalize_range_run(run):
    repo, state, brief = run
    root_index = (repo / "llake/index.md").read_text()
    summary = finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03", now=1234)
    llake = repo / "llake"
    assert "updated: 2026-10-03" in (llake / X).read_text()
    assert "updated: 2026-10-03" in (llake / NEW).read_text()
    assert "updated: 2026-01-01" in (llake / Y).read_text()
    index = (llake / IDX).read_text()
    assert "| [[x]] | new desc x |" in index and "| [[new]] | a new page |" in index
    assert "\n4 pages.\n" in index and "updated: 2026-10-03" in index
    assert (llake / "index.md").read_text() == root_index
    doc = gap_doc(repo)
    assert gaps.validate(doc, str(llake)) == [] and doc["gaps"] == [] and doc["asOf"] == H
    log = (llake / "log.md").read_text()
    assert "## [2026-10-03] ingest | aaaaaaa..bbbbbbb: v3 — 1 updated, 1 created, 0 gaps (0 major)" in log
    assert "Agent `run-7`, $0.00 list estimate. Themes: T1 Loop capped." in log
    assert "Pages affected: [[x]] (updated), [[new]] (created), [[arch]]" in log
    assert (llake / "last-ingest-sha").read_text() == H + "\n"
    assert (llake / ".state/last-ingest-at").read_text() == "1234\n"
    j = RunState(repo, state.dir).journal
    assert j["finalized"] is True and j["logEntry"] in log
    assert {X, NEW, IDX, "ingest-gaps.json", "last-ingest-sha"} <= set(j["finalizeWrites"])
    assert summary["updated"] == [X] and summary["created"] == [NEW]
    assert json.loads(open(os.path.join(state.dir, "finalize.json")).read())["head"] == H


def test_every_write_is_journaled_before_it_lands(run, monkeypatch):
    """Kill safety: each file is in finalizeWrites (log: logEntry set) on disk before it changes, and finalized
    stays false until after the cursor write."""
    repo, state, brief = run
    llake = str(repo / "llake")
    run_json = os.path.join(state.dir, "run.json")
    seen = []
    real = common.write_text

    def spy(path, text):
        rel = os.path.relpath(path, llake).replace(os.sep, "/")
        if not rel.startswith(".state/agents/"):
            j = json.loads(open(run_json).read())
            seen.append((rel, rel in j["finalizeWrites"], j["logEntry"], j["finalized"]))
        real(path, text)
    monkeypatch.setattr(common, "write_text", spy)
    monkeypatch.setattr(finalize, "write_text", spy)
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03", now=5)
    order = [s[0] for s in seen]
    assert order == [NEW, X, IDX, "ingest-gaps.json", "log.md", "last-ingest-sha", ".state/last-ingest-at"]
    for rel, journaled, entry, finalized in seen:
        assert finalized is False, rel
        if rel == "log.md":
            assert entry.startswith("\n## [2026-10-03]")
        elif rel != ".state/last-ingest-at":
            assert journaled, rel
    assert RunState(repo, state.dir).journal["finalized"] is True


def test_failure_after_the_log_then_kill_revert_undoes_finalize(run, monkeypatch):
    repo, state, brief = run
    llake = repo / "llake"
    pre_index, pre_x = (llake / IDX).read_text(), (state.dir + "/pre/" + X)
    before = (llake / "last-ingest-sha").read_text()

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(finalize, "write_cursor", boom)
    with pytest.raises(RuntimeError):
        finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    j = RunState(repo, state.dir).journal
    assert j["finalized"] is False and j["logEntry"] in (llake / "log.md").read_text()
    assert snapshots.revert_run(repo, state.dir)["unrestored"] == []
    assert (llake / IDX).read_text() == pre_index
    assert (llake / X).read_text() == open(pre_x).read()
    assert not (llake / NEW).exists() and not (llake / "ingest-gaps.json").exists()
    assert (llake / "log.md").read_text() == "# LoreLake Activity Log\n"
    assert (llake / "last-ingest-sha").read_text() == before


def test_gap_lines_stuck_and_resolved(run):
    repo, state, brief = run
    dump_json(str(repo / "llake/ingest-gaps.json"), {"version": 1, "asOf": B, "agent": "old", "date": "d",
        "ranges": [], "gaps": [
            {"page": Y, "severity": "minor", "cause": "writer-failed", "since": "s0", "attempts": 2, "stuck": False,
             "claims": [{"quote": "Body y stays", "head": "h", "severity": "minor", "source": "brief"}]},
            {"page": X, "severity": "major", "cause": "declared", "since": "s0", "attempts": 1, "stuck": False,
             "claims": [{"quote": "The loop is unbounded", "head": "h", "severity": "major", "source": "brief"}]}]})
    state.pages[Y].update({"outcome": "writer-failed", "carried": True})
    state.pages[X]["carried"] = True
    state.save()
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    log = (repo / "llake/log.md").read_text()
    assert '- [[y]] — minor, cause: writer-failed: "Body y stays" → h — needs a human' in log
    assert "Carried gaps resolved: [[x]]" in log
    g = gap_doc(repo)["gaps"][0]
    assert (g["page"], g["attempts"], g["stuck"]) == (Y, 3, True)


def test_gap_only_heading(run):
    repo, state, brief = run
    brief["themes"] = []
    finalize.finalize(state, CFG, brief, "run-7", B, H, "gap-only", today="2026-10-03")
    log = (repo / "llake/log.md").read_text()
    assert "## [2026-10-03] ingest | gap-only at bbbbbbb: v3 —" in log
    assert "No new range: carried gaps only." in log


def test_split_head_moves_cursor_to_midpoint_and_clears_the_counter(run):
    repo, state, brief = run
    mid = "c" * 40
    failures = repo / "llake/.state/ingest-failures.json"
    dump_json(str(failures), {"base": B, "count": 2, "lastHead": H})
    finalize.finalize(state, CFG, brief, "run-7", B, mid, "split", today="2026-10-03")
    assert (repo / "llake/last-ingest-sha").read_text() == mid + "\n"
    assert "aaaaaaa..ccccccc: v3" in (repo / "llake/log.md").read_text()
    assert not failures.exists()


def test_dropped_new_page_is_noted(run):
    repo, state, brief = run
    dump_json(state.dir + "/brief-report.json", {"dropped_new_pages": ["wiki/nowhere/a.md"]})
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    assert "Note: new page `wiki/nowhere/a.md` dropped: its category does not exist" in \
        (repo / "llake/log.md").read_text()


def test_other_stale_notes_reach_the_log(run):
    repo, state, brief = run
    state.ledger["otherStale"] = [{"page": "wiki/arch/z.md", "quote": "not on the z page", "head": "h",
                                   "severity": "minor"}]
    state.save()
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    assert "Note: a writer reported `wiki/arch/z.md` stale, but its quote is not on the page; not recorded" in \
        (repo / "llake/log.md").read_text()


def test_out_of_surface_writes_are_noted(run):
    repo, state, brief = run
    state.ledger["surface"] = [{"stage": "writer-b01", "path": "llake/wiki/arch/z.md", "action": "reverted"}]
    state.save()
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    assert "Note: out-of-surface write to `llake/wiki/arch/z.md` by writer-b01: reverted" in \
        (repo / "llake/log.md").read_text()


def test_blurb_flag_becomes_a_minor_blurb_gap(run):
    repo, state, brief = run
    brief["blurbs"] = [{"page": IDX, "severity": "major", "reason": "the blurb is false",
                        "claims": [{"quote": "Pages about arch", "head": "src/l.py:1", "severity": "major"}]}]
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    g = gap_doc(repo)["gaps"][0]
    assert (g["page"], g["cause"], g["severity"], g["claims"][0]["quote"]) == \
        (IDX, "blurb", "minor", "Pages about arch")
    assert "[[arch]] — minor, cause: blurb" in (repo / "llake/log.md").read_text()


def test_unrestored_page_is_a_reverted_gap(run):
    repo, state, brief = run
    state.pages[X]["unrestored"] = True
    state.save()
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    doc = gap_doc(repo)
    g = doc["gaps"][0]
    assert (g["page"], g["cause"], g["attempts"]) == (X, "reverted", 1)
    assert gaps.validate(doc, str(repo / "llake")) == []


def _failed_fixer(repo, state, fixed):
    state.pages[X]["fixSnap"] = state.dir + "/bundles/b01/fix-snapshot"
    if fixed:
        state.pages[X]["fixed"] = True
    state.save()
    dump_json(state.dir + "/checks.write.json", {"flags": {X: [
        {"quote": "The loop is capped", "head": "src/l.py:99 is out of range", "severity": "minor",
         "source": "check:anchor"}]}})


def test_failed_fixer_leaves_a_flagged_gap_with_its_findings(run):
    repo, state, brief = run
    _failed_fixer(repo, state, fixed=False)
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    g = gap_doc(repo)["gaps"][0]
    assert (g["page"], g["cause"], g["attempts"]) == (X, "flagged", 1)
    assert [(c["quote"], c["source"]) for c in g["claims"]] == [("The loop is capped", "check:anchor")]


def test_failed_fixer_keeps_verifier_only_findings(run):
    repo, state, brief = run
    _failed_fixer(repo, state, fixed=False)
    dump_json(state.dir + "/checks.write.json", {"flags": {}})
    state.pages[X]["verifier"] = {"accuracy": [{"quote": "new desc x", "head": "src/l.py:5", "severity": "major"}],
                                  "residual": []}
    state.save()
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    g = gap_doc(repo)["gaps"][0]
    assert (g["cause"], g["severity"], g["claims"][0]["source"]) == ("flagged", "major", "verifier:accuracy")


def test_failed_fixer_whose_findings_left_the_page_still_owes_a_gap(run):
    repo, state, brief = run
    _failed_fixer(repo, state, fixed=False)
    dump_json(state.dir + "/checks.write.json", {"flags": {X: [
        {"quote": "a sentence the page no longer has", "head": "h", "severity": "minor", "source": "check:anchor"}]}})
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    doc = gap_doc(repo)
    assert doc["gaps"][0]["cause"] == "flagged" and gaps.validate(doc, str(repo / "llake")) == []


def test_failed_fixer_on_a_declared_page_is_flagged_and_keeps_claims_left(run):
    repo, state, brief = run
    _failed_fixer(repo, state, fixed=False)
    dump_json(state.dir + "/checks.write.json", {"flags": {}})
    state.pages[X].update({"outcome": "declared", "status": "declared-gap",
                           "claimsLeft": [{"quote": "The loop is capped", "head": "src/l.py:7", "severity": "minor"}]})
    state.save()
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    g = gap_doc(repo)["gaps"][0]
    assert (g["cause"], g["claims"][0]["quote"], g["claims"][0]["head"]) == \
        ("flagged", "The loop is capped", "src/l.py:7")


def test_successful_fixer_findings_are_not_carried_over(run):
    repo, state, brief = run
    _failed_fixer(repo, state, fixed=True)
    finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    assert gap_doc(repo)["gaps"] == []


def test_finalize_failure_holds_the_cursor(run, monkeypatch):
    repo, state, brief = run
    before = (repo / "llake/last-ingest-sha").read_text()

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(gaps, "save", boom)
    with pytest.raises(RuntimeError):
        finalize.finalize(state, CFG, brief, "run-7", B, H, "range", today="2026-10-03")
    assert (repo / "llake/last-ingest-sha").read_text() == before
    j = RunState(repo, state.dir).journal
    assert j["finalized"] is False and X in j["finalizeWrites"] and "ingest-gaps.json" in j["finalizeWrites"]


def test_record_skip(run):
    repo, state, brief = run
    failures = repo / "llake/.state/ingest-failures.json"
    dump_json(str(failures), {"base": B, "count": 2, "lastHead": H})
    finalize.record_skip(state, "run-7", B, H, ["wiki/arch/x.md: `oldName` L3"], today="2026-10-03", now=99)
    llake = repo / "llake"
    doc = gap_doc(repo)
    assert doc["ranges"] == [{"base": B, "head": H, "cause": "analysis-failed",
                              "leads": ["wiki/arch/x.md: `oldName` L3"]}]
    assert gaps.validate(doc, str(llake)) == []
    log = (llake / "log.md").read_text()
    assert "## [2026-10-03] ingest | aaaaaaa..bbbbbbb: v3 — skipped: analysis failed twice" in log
    assert "- wiki/arch/x.md: `oldName` L3" in log
    assert (llake / "last-ingest-sha").read_text() == H + "\n"
    assert (llake / ".state/last-ingest-at").read_text() == "99\n"
    j = RunState(repo, state.dir).journal
    assert j["finalized"] is True and j["logEntry"] in log
    assert {"ingest-gaps.json", "last-ingest-sha"} <= set(j["finalizeWrites"])
    assert not failures.exists()


def test_record_skip_without_leads_records_a_placeholder_lead(run):
    repo, state, brief = run
    finalize.record_skip(state, "run-7", B, H, [], today="2026-10-03")
    doc = gap_doc(repo)
    assert doc["ranges"][0]["leads"] == ["(no wiki page names a removed name in this range)"]
    assert gaps.validate(doc, str(repo / "llake")) == []
