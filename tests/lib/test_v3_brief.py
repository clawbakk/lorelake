"""Brief assembly: validation, quotes, sweep accounting, carried gaps, blurbs, dropped entries."""
import json

import pytest

from v3_helpers import page_text, write
from ingest_v3 import brief
from ingest_v3.common import dump_json, load_json

STALE = "The client retries forever on error"


@pytest.fixture
def llake(tmp_path):
    root = tmp_path / "llake"
    write(root, "wiki/arch/arch.md", page_text("Arch", "Category index for arch.", "Arch pages talk polling."))
    write(root, "wiki/arch/client.md", page_text("Client", "client", STALE + ".\n\nUses fetchUserData."))
    write(root, "wiki/arch/cache.md", page_text("Cache", "cache", "Also calls fetchUserData."))
    write(root, "wiki/gotchas/gotchas.md", page_text("Gotchas", "Category index for gotchas.", ""))
    write(root, "wiki/gotchas/retry.md", page_text("Retry", "retry", "fetchUserData can hang."))
    write(root, "wiki/decisions/decisions.md", page_text("Decisions", "Category index for decisions.", ""))
    write(root, "wiki/decisions/adr-retry.md", page_text("ADR", "adr", "fetchUserData is still live."))
    write(root, "wiki/discussions/2026-01-01-x.md", page_text("D", "d", "x"))
    return root


def agent_dir(tmp_path, pages=None, themes=None, considered=None, hits=None, raw_files=None):
    d = tmp_path / "agent"
    (d / "brief" / "pages").mkdir(parents=True)
    (d / "inputs").mkdir()
    if themes is not False:
        (d / "brief" / "themes.json").write_text(json.dumps(
            themes or [{"id": "T1", "title": "Retries capped", "summary": "s"}]))
    if pages is not None:
        (d / "brief" / "pages" / "batch-1.json").write_text(json.dumps(pages))
    for name, text in (raw_files or {}).items():
        (d / "brief" / "pages" / name).write_text(text)
    (d / "brief" / "considered.json").write_text(json.dumps(considered or []))
    dump_json(str(d / "inputs" / "hits.json"), hits or {})
    return d


def entry(path="llake/wiki/arch/client.md", severity="major", stale=None, **kw):
    e = {"path": path, "kind": "direct", "severity": severity, "themes": ["T1"], "reason": "r",
         "stale": stale if stale is not None else [{"quote": STALE, "head": "src/c.py:3 caps at 3", "severity": "major"}]}
    e.update(kw)
    return e


def test_valid_brief(tmp_path, llake):
    d = agent_dir(tmp_path, [entry()])
    b = brief.assemble(str(d), str(llake), "h1")
    assert [p["page"] for p in b["pages"]] == ["wiki/arch/client.md"]
    p = b["pages"][0]
    assert p["claims"][0]["quoteFound"] is True and p["weight_kb"] > 0 and p["create"] is False
    assert load_json(str(d / "brief.json"))["head"] == "h1"


def test_unparseable_page_file_is_invalid(tmp_path, llake):
    d = agent_dir(tmp_path, raw_files={"batch-1.json": "{oops"})
    with pytest.raises(brief.InvalidBrief):
        brief.assemble(str(d), str(llake), "h")
    assert load_json(str(d / "brief-report.json"))["errors"]


@pytest.mark.parametrize("bad", [
    {"path": "llake/wiki/arch/client.md", "reason": "r", "stale": []},
    entry(stale=[{"quote": "q", "severity": "minor"}]),
    entry(path="llake/wiki/arch/new.md", kind="new", stale=[]),
    entry(path="not/a/page.txt/../x"),
])
def test_missing_required_field_is_invalid(tmp_path, llake, bad):
    with pytest.raises(brief.InvalidBrief):
        brief.assemble(str(agent_dir(tmp_path, [bad])), str(llake), "h")


def test_missing_or_empty_themes_is_invalid(tmp_path, llake):
    with pytest.raises(brief.InvalidBrief):
        brief.assemble(str(agent_dir(tmp_path, [entry()], themes=False)), str(llake), "h")


def test_quote_not_found_is_kept_but_marked(tmp_path, llake):
    d = agent_dir(tmp_path, [entry(stale=[{"quote": "never said", "head": "h", "severity": "minor"}], severity="minor")])
    b = brief.assemble(str(d), str(llake), "h")
    assert b["pages"][0]["claims"][0]["quoteFound"] is False


def test_page_severity_is_major_if_any_claim_is(tmp_path, llake):
    d = agent_dir(tmp_path, [entry(severity="minor")])
    assert brief.assemble(str(d), str(llake), "h")["pages"][0]["severity"] == "major"


def test_sweep_accounting(tmp_path, llake):
    hits = {"wiki/arch/cache.md": {"fetchUserData": [11]}, "wiki/gotchas/retry.md": {"fetchUserData": [11]},
            "wiki/decisions/adr-retry.md": {"fetchUserData": [11]}, "wiki/arch/arch.md": {"fetchUserData": [11]}}
    d = agent_dir(tmp_path, [entry()], hits=hits,
                  considered=[{"path": "llake/wiki/decisions/adr-retry.md", "reason": "framed as history"}])
    b = brief.assemble(str(d), str(llake), "h")
    by = {p["page"]: p for p in b["pages"]}
    assert by["wiki/arch/cache.md"]["autoAdded"] and by["wiki/arch/cache.md"]["severity"] == "minor"
    assert by["wiki/arch/cache.md"]["claims"][0]["source"] == "check:removed-name"
    assert "wiki/gotchas/retry.md" in by
    assert "wiki/decisions/adr-retry.md" not in by
    assert "wiki/arch/arch.md" not in by
    report = load_json(str(d / "brief-report.json"))
    assert report["auto_added_state_pages"] == ["wiki/arch/cache.md"]
    assert report["unaccounted_record_or_gotcha_pages"] == ["wiki/gotchas/retry.md"]


def test_carried_gaps_merge_and_gap_only_scope(tmp_path, llake):
    dump_json(str(llake / "ingest-gaps.json"), {"version": 1, "asOf": "h0", "agent": "a", "date": "d", "ranges": [],
        "gaps": [{"page": "wiki/arch/client.md", "severity": "major", "cause": "declared", "since": "s0",
                  "attempts": 1, "stuck": False, "claims": [{"quote": "Uses fetchUserData", "head": "h",
                                                             "severity": "major", "source": "brief"}]},
                 {"page": "wiki/arch/cache.md", "severity": "minor", "cause": "run-cap", "since": "s0",
                  "attempts": 0, "stuck": False, "claims": [{"quote": "Also calls", "head": "h",
                                                             "severity": "minor", "source": "brief"}]}]})
    b = brief.assemble(str(agent_dir(tmp_path, [entry()])), str(llake), "h")
    by = {p["page"]: p for p in b["pages"]}
    assert by["wiki/arch/client.md"]["carried"] and len(by["wiki/arch/client.md"]["claims"]) == 2
    assert by["wiki/arch/cache.md"]["carried"]
    g = brief.assemble(str(agent_dir(tmp_path / "g", themes=False)), str(llake), "h", gap_only=True)
    assert [p["page"] for p in g["pages"]] == ["wiki/arch/client.md"]


def test_carried_gap_for_missing_page_is_dropped(tmp_path, llake):
    dump_json(str(llake / "ingest-gaps.json"), {"version": 1, "gaps": [
        {"page": "wiki/arch/gone.md", "severity": "major", "cause": "declared", "since": "s", "attempts": 1,
         "stuck": False, "claims": []}], "ranges": []})
    d = agent_dir(tmp_path, [entry()])
    b = brief.assemble(str(d), str(llake), "h")
    assert "wiki/arch/gone.md" not in {p["page"] for p in b["pages"]}
    assert load_json(str(d / "brief-report.json"))["carried_dropped_missing"] == ["wiki/arch/gone.md"]


def test_excluded_and_root_index_entries_are_dropped(tmp_path, llake):
    d = agent_dir(tmp_path, [entry(), entry(path="llake/wiki/discussions/2026-01-01-x.md"),
                             entry(path="llake/index.md")])
    b = brief.assemble(str(d), str(llake), "h")
    assert [p["page"] for p in b["pages"]] == ["wiki/arch/client.md"]
    assert len(load_json(str(d / "brief-report.json"))["warnings"]) == 2


def test_index_entry_becomes_blurb(tmp_path, llake):
    d = agent_dir(tmp_path, [entry(), entry(path="llake/wiki/arch/arch.md", kind="index", severity="minor",
                                            stale=[{"quote": "Arch pages talk polling", "head": "h",
                                                    "severity": "minor"}])])
    b = brief.assemble(str(d), str(llake), "h")
    assert [p["page"] for p in b["blurbs"]] == ["wiki/arch/arch.md"]
    assert "wiki/arch/arch.md" not in {p["page"] for p in b["pages"]}


def test_new_page_needs_existing_category(tmp_path, llake):
    ok = entry(path="llake/wiki/arch/limits.md", kind="new", severity="minor", stale=[], title="Limits",
               description="Retry limits", newFacts=["cap is 3 (src/c.py:3)"])
    bad = dict(ok, path="llake/wiki/nowhere/limits.md")
    d = agent_dir(tmp_path, [ok, bad])
    b = brief.assemble(str(d), str(llake), "h")
    pages = {p["page"]: p for p in b["pages"]}
    assert pages["wiki/arch/limits.md"]["create"] is True
    assert "wiki/nowhere/limits.md" not in pages
    assert load_json(str(d / "brief-report.json"))["dropped_new_pages"] == ["wiki/nowhere/limits.md"]


def test_recall_files_and_single_object_files_are_read(tmp_path, llake):
    d = agent_dir(tmp_path, [entry()], raw_files={
        "recall-1.json": json.dumps([entry(path="llake/wiki/arch/cache.md", severity="minor",
                                           stale=[{"quote": "Also calls", "head": "h", "severity": "minor"}])]),
        "single.json": json.dumps(entry(path="llake/wiki/gotchas/retry.md", severity="minor",
                                        stale=[{"quote": "can hang", "head": "h", "severity": "minor"}]))})
    b = brief.assemble(str(d), str(llake), "h")
    assert {p["page"] for p in b["pages"]} == {"wiki/arch/client.md", "wiki/arch/cache.md", "wiki/gotchas/retry.md"}
