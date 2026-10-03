"""$0 checks: removed names, quote residue, anchors, status/diff, frontmatter, links, concurrent changes."""
import json

import pytest

from v3_helpers import make_project, page_text, write
from ingest_v3 import checks, snapshots
from ingest_v3.common import dump_json
from ingest_v3.state import RunState

QUOTE = "The client calls fetchUserData on start"


@pytest.fixture
def proj(tmp_path):
    repo = make_project(tmp_path, src={"src/app.py": "def loadProfile():\n    return 1\n"},
                        pages={"arch/client.md": page_text("Client", "client", QUOTE + "."),
                               "gotchas/hang.md": page_text("Hang", "hang", "fetchUserData can hang."),
                               "decisions/adr-x.md": page_text("ADR", "adr", QUOTE + "."),
                               "arch/plain.md": "No frontmatter here.\n"})
    state = RunState(repo, repo / "llake/.state/agents/run")
    snapshots.take_pre(state)
    dump_json(state.dir + "/inputs/names.json", {"removed": ["fetchUserData"], "added": ["loadProfile"]})
    return repo, state


def written(repo, state, page, new_text, status="corrected", rejected=()):
    snap = state.dir + "/bundles/b01/snapshot"
    snapshots.snapshot(state, [page], snap)
    write(repo, "llake/" + page, new_text)
    snapshots.settle(state, [page])
    state.pages[page] = {"page": page, "severity": "major", "outcome": {"corrected": "corrected",
                         "no-change": "no-change", "declared-gap": "declared"}[status], "status": status,
                         "changed": snapshots.changed_since(state, page, snap), "rejected": list(rejected),
                         "snap": snap, "history": [], "bundle": "b01"}


def brief(*pages, quote=QUOTE):
    return {"pages": [{"page": p, "severity": "major", "claims": [
        {"quote": quote, "head": "src/app.py:1", "severity": "major", "quoteFound": True}]} for p in pages],
        "themes": []}


def test_removed_name_on_state_page_not_gotcha(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", page_text("Client", "client", "Still mentions fetchUserData."))
    written(repo, state, "wiki/gotchas/hang.md", page_text("Hang", "hang", "fetchUserData can hang forever."))
    res = checks.run_checks(state, brief("wiki/arch/client.md", "wiki/gotchas/hang.md", quote="zzz"), "write")
    assert [f["source"] for f in res["flags"]["wiki/arch/client.md"]] == ["check:removed-name"]
    assert "wiki/gotchas/hang.md" not in res["flags"]


def test_quote_residue_and_exemptions(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", page_text("Client", "client", QUOTE + ". Added a line."))
    written(repo, state, "wiki/decisions/adr-x.md", page_text("ADR", "adr", QUOTE + ". Noted.", status="stale"))
    res = checks.run_checks(state, brief("wiki/arch/client.md", "wiki/decisions/adr-x.md"), "write")
    sources = [f["source"] for f in res["flags"]["wiki/arch/client.md"]]
    assert "check:quote-residue" in sources
    assert "check:quote-residue" not in [f["source"] for f in res["flags"].get("wiki/decisions/adr-x.md", [])]


def test_rejected_claim_is_not_residue(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", page_text("Client", "client", QUOTE + ". Extra."), rejected=[QUOTE])
    res = checks.run_checks(state, brief("wiki/arch/client.md"), "write")
    assert "check:quote-residue" not in [f["source"] for f in res["flags"].get("wiki/arch/client.md", [])]


def test_anchor_on_added_line(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", page_text("Client", "client", "Uses `loadProfile` at `src/app.py:1`.\n\n"
                                                                              "`otherThing` is at `src/app.py:2`."))
    flags = checks.run_checks(state, brief("wiki/arch/client.md", quote="zzz"), "write")["flags"]["wiki/arch/client.md"]
    assert [f["source"] for f in flags] == ["check:anchor"] and "otherThing" in flags[0]["head"]


def test_corrected_but_unchanged_flags_brief_claims(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", page_text("Client", "client", QUOTE + "."))
    res = checks.run_checks(state, brief("wiki/arch/client.md"), "write")
    assert res["status_mismatch"] and res["flags"]["wiki/arch/client.md"][0]["quote"] == QUOTE


def test_broken_frontmatter_is_reverted(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", "---\ntitle: [unclosed\n---\nbody\n")
    res = checks.run_checks(state, brief("wiki/arch/client.md"), "write")
    assert res["reverted"] == ["wiki/arch/client.md"]
    assert state.pages["wiki/arch/client.md"]["outcome"] == "reverted"
    assert QUOTE in (repo / "llake/wiki/arch/client.md").read_text()


def test_page_without_frontmatter_at_base_is_not_reverted(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/plain.md", "Still no frontmatter, but updated.\n")
    res = checks.run_checks(state, brief("wiki/arch/plain.md", quote="zzz"), "write")
    assert res["reverted"] == [] and state.pages["wiki/arch/plain.md"]["outcome"] == "corrected"


def test_fix_pass_frontmatter_break_restores_post_write(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", page_text("Client", "client", "Post-write text."))
    fix_snap = state.dir + "/bundles/b01/fix-snapshot"
    snapshots.snapshot(state, ["wiki/arch/client.md"], fix_snap)
    snapshots.settle(state, ["wiki/arch/client.md"])
    state.pages["wiki/arch/client.md"]["fixSnap"] = fix_snap
    write(repo, "llake/wiki/arch/client.md", "---\ntitle: [broken\n---\n")
    res = checks.run_checks(state, brief("wiki/arch/client.md", quote="zzz"), "fix")
    assert res["reverted"] == [] and "Post-write text." in (repo / "llake/wiki/arch/client.md").read_text()
    assert state.pages["wiki/arch/client.md"]["outcome"] == "corrected"


def test_added_dangling_link_is_unlinked(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md",
            page_text("Client", "client", "See [[nowhere|the nowhere page]] and [[hang]] and [[limits]]."))
    b = brief("wiki/arch/client.md", quote="zzz")
    b["pages"].append({"page": "wiki/arch/limits.md", "severity": "minor", "claims": [], "create": True})
    res = checks.run_checks(state, b, "write")
    assert res["unlinked"] == {"wiki/arch/client.md": ["nowhere"]}
    text = (repo / "llake/wiki/arch/client.md").read_text()
    assert "the nowhere page" in text and "[[nowhere" not in text and "[[hang]]" in text and "[[limits]]" in text


def test_concurrent_change_is_reported_not_reverted(proj):
    repo, state = proj
    write(repo, "llake/wiki/gotchas/new-capture.md", "written by a session capture\n")
    res = checks.run_checks(state, brief(quote="zzz"), "write")
    assert res["concurrent"] == ["wiki/gotchas/new-capture.md"]
    assert (repo / "llake/wiki/gotchas/new-capture.md").exists()
    assert json.loads(open(state.dir + "/checks.write.json").read())["concurrent"] == res["concurrent"]


def test_unrestorable_frontmatter_revert_is_recorded_not_assumed(proj):
    repo, state = proj
    written(repo, state, "wiki/arch/client.md", "---\ntitle: [unclosed\n---\nbody\n")
    (repo / "llake/.state/agents/run/bundles/b01/snapshot/manifest.json").unlink()
    res = checks.run_checks(state, brief("wiki/arch/client.md", quote="zzz"), "write")
    st = state.pages["wiki/arch/client.md"]
    assert res["reverted"] == [] and st["unrestored"] is True and st["outcome"] == "corrected"
