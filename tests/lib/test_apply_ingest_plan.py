"""Tests for apply_ingest_plan.py — applies an ingest v2 plan to a wiki."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks" / "lib"))
import apply_ingest_plan as applier


def test_replace_single_anchor_works():
    original = "alpha beta gamma\ndelta epsilon\n"
    ops = [{"op": "replace", "find": "beta", "with": "BETA"}]
    result = applier.apply_replace_ops(original, ops)
    assert result == "alpha BETA gamma\ndelta epsilon\n"


def test_replace_two_non_overlapping_anchors():
    original = "alpha beta gamma delta\n"
    ops = [
        {"op": "replace", "find": "alpha", "with": "AAA"},
        {"op": "replace", "find": "delta", "with": "DDD"},
    ]
    result = applier.apply_replace_ops(original, ops)
    assert result == "AAA beta gamma DDD\n"


def test_replace_anchor_not_found_raises():
    with pytest.raises(applier.AnchorNotFound):
        applier.apply_replace_ops("hello", [{"op": "replace", "find": "world", "with": "x"}])


def test_replace_anchor_ambiguous_raises():
    original = "foo bar foo\n"
    with pytest.raises(applier.AnchorAmbiguous):
        applier.apply_replace_ops(original, [{"op": "replace", "find": "foo", "with": "FOO"}])


def test_replace_overlap_pinned_apple_example():
    """Pinned: from spec discussion. Two anchors that share bytes in the original."""
    original = "I want to eat the apple that grows on that tree\n"
    ops = [
        {"op": "replace", "find": "I want to eat the apple", "with": "I want to eat the orange"},
        {"op": "replace", "find": "the apple that grows on that tree", "with": "the apple that grows on that hill"},
    ]
    with pytest.raises(applier.EditOverlap):
        applier.apply_replace_ops(original, ops)


def test_replace_reverse_order_preserves_offsets():
    """If applied front-to-back naively, the second find's offset would shift.
    Reverse-order application avoids this."""
    original = "AAA-BBB-CCC\n"
    ops = [
        {"op": "replace", "find": "AAA", "with": "alpha-numeric-substantial"},
        {"op": "replace", "find": "CCC", "with": "ZZZ"},
    ]
    result = applier.apply_replace_ops(original, ops)
    assert result == "alpha-numeric-substantial-BBB-ZZZ\n"


PAGE_WITH_SECTIONS = """# Top

## Section A
A line.

## Section B
B line.

### B sub
Sub line.

## Section C
C line.
"""


def test_append_section_inserts_before_next_same_level_heading():
    ops = [{"op": "append_section", "after_heading": "## Section A", "content": "Appended.\n"}]
    result = applier.apply_section_ops(PAGE_WITH_SECTIONS, ops)
    # New content lands at the end of Section A, before "## Section B".
    assert "A line.\nAppended.\n\n## Section B" in result


def test_append_section_inserts_before_next_higher_level_when_subsection():
    ops = [{"op": "append_section", "after_heading": "### B sub", "content": "Sub-appended.\n"}]
    result = applier.apply_section_ops(PAGE_WITH_SECTIONS, ops)
    # ### B sub is followed by ## Section C (higher level), so we land before that.
    assert "Sub line.\nSub-appended.\n\n## Section C" in result


def test_append_section_at_eof_when_no_following_heading():
    ops = [{"op": "append_section", "after_heading": "## Section C", "content": "EOF append.\n"}]
    result = applier.apply_section_ops(PAGE_WITH_SECTIONS, ops)
    assert result.endswith("C line.\nEOF append.\n")


def test_append_section_heading_not_found_raises():
    with pytest.raises(applier.HeadingNotFound):
        applier.apply_section_ops(PAGE_WITH_SECTIONS,
            [{"op": "append_section", "after_heading": "## No Such", "content": "x"}])


def test_body_replace_returns_new_content():
    result = applier.apply_body_replace("anything", [{"op": "body_replace", "content": "REPLACED\n"}])
    assert result == "REPLACED\n"


def test_body_replace_no_op_passthrough():
    """No body_replace in ops → returns the original."""
    result = applier.apply_body_replace("original", [{"op": "replace", "find": "x", "with": "y"}])
    assert result == "original"


import frontmatter as fm

SAMPLE_PAGE = """---
title: "Sample"
description: "A sample."
tags: [hooks, sample]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[other]]"
---
# Body

## Section A
A.

## Section B
B.
"""


def test_frontmatter_set_replaces_value():
    fm_dict = fm.parse(fm.split(SAMPLE_PAGE)[0])
    new = applier.apply_frontmatter_ops(fm_dict,
        [{"op": "frontmatter_set", "key": "description", "value": "New description."}])
    assert new["description"] == "New description."
    assert new["title"] == "Sample"  # unchanged


def test_frontmatter_add_related_idempotent():
    fm_dict = fm.parse(fm.split(SAMPLE_PAGE)[0])
    new = applier.apply_frontmatter_ops(fm_dict,
        [{"op": "frontmatter_add_related", "items": ["[[new-link]]", "[[other]]"]}])
    # [[other]] already there, [[new-link]] new; no duplicates.
    assert new["related"] == ["[[other]]", "[[new-link]]"]


def test_apply_update_full_pipeline_on_disk(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(SAMPLE_PAGE)
    update = {
        "slug": "sample",
        "rationale": "test",
        "ops": [
            {"op": "replace", "find": "A.", "with": "AAA."},
            {"op": "append_section", "after_heading": "## Section B", "content": "BB.\n"},
            {"op": "frontmatter_set", "key": "description", "value": "Updated desc."},
            {"op": "frontmatter_add_related", "items": ["[[adr-005]]"]},
        ],
    }
    applier.apply_update(page, update, today="2026-04-25")
    out = page.read_text()
    assert "AAA." in out
    assert "BB." in out
    assert 'description: "Updated desc."' in out
    assert "[[adr-005]]" in out
    assert "updated: 2026-04-25" in out


def test_apply_update_body_replace_preserves_frontmatter(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(SAMPLE_PAGE)
    update = {
        "slug": "sample",
        "rationale": "rewrite",
        "ops": [
            {"op": "body_replace", "content": "# Brand New Body\nNew text.\n"},
            {"op": "frontmatter_set", "key": "description", "value": "Rewritten."},
        ],
    }
    applier.apply_update(page, update, today="2026-04-25")
    out = page.read_text()
    assert "# Brand New Body" in out
    assert 'title: "Sample"' in out  # frontmatter intact
    assert 'description: "Rewritten."' in out
    assert "## Section A" not in out  # old body gone


def test_apply_update_atomic_on_anchor_failure(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(SAMPLE_PAGE)
    update = {
        "slug": "sample",
        "rationale": "broken",
        "ops": [{"op": "replace", "find": "NOT_THERE", "with": "x"}],
    }
    with pytest.raises(applier.AnchorNotFound):
        applier.apply_update(page, update, today="2026-04-25")
    # File unchanged
    assert page.read_text() == SAMPLE_PAGE


def test_apply_create_writes_new_page(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "decisions").mkdir(parents=True)
    create = {
        "slug": "adr-005-foo",
        "category": "decisions",
        "front_matter": {"title": "ADR-005", "description": "foo",
                          "tags": ["decisions"], "created": "2026-04-25",
                          "updated": "2026-04-25", "status": "current",
                          "related": []},
        "body": "# ADR-005\n\nBody.\n",
    }
    applier.apply_create(wiki, create, today="2026-04-25")
    p = wiki / "decisions" / "adr-005-foo.md"
    assert p.exists()
    assert "ADR-005" in p.read_text()


def test_apply_create_already_exists_raises(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "decisions").mkdir(parents=True)
    (wiki / "decisions" / "exists.md").write_text("---\ntitle: x\n---\n")
    create = {
        "slug": "exists", "category": "decisions",
        "front_matter": {"title": "X", "description": "x", "tags": [],
                          "created": "2026-04-25", "updated": "2026-04-25",
                          "status": "current", "related": []},
        "body": "x",
    }
    with pytest.raises(applier.AlreadyExists):
        applier.apply_create(wiki, create, today="2026-04-25")


def test_apply_delete_removes_file_and_scrubs_related(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "hooks").mkdir(parents=True)
    (wiki / "hooks" / "old-hook.md").write_text(SAMPLE_PAGE.replace("Sample", "Old Hook"))
    (wiki / "hooks" / "other.md").write_text(
        "---\ntitle: Other\nrelated:\n  - \"[[old-hook]]\"\n  - \"[[keeper]]\"\n---\n# Other\n"
    )
    result = applier.apply_delete(wiki, "old-hook", today="2026-04-25")
    assert not (wiki / "hooks" / "old-hook.md").exists()
    other = (wiki / "hooks" / "other.md").read_text()
    assert "[[old-hook]]" not in other
    assert "[[keeper]]" in other
    assert result.get("note") is None  # was present, real delete


def test_apply_delete_target_already_absent_is_noop(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "hooks").mkdir(parents=True)
    result = applier.apply_delete(wiki, "ghost", today="2026-04-25")
    assert result["note"] == "target_already_absent"
    # Cascade is skipped (nothing to scrub).


def test_apply_delete_surfaces_inline_link_warnings(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "hooks").mkdir(parents=True)
    (wiki / "hooks" / "doomed.md").write_text("---\ntitle: D\n---\n# D\n")
    (wiki / "hooks" / "ref.md").write_text(
        "---\ntitle: R\n---\n# R\n\nSee [[doomed]] for context.\nAlso [[doomed]] mentioned twice.\n"
    )
    result = applier.apply_delete(wiki, "doomed", today="2026-04-25")
    links = result["dangling_inline_links"]
    assert len(links) == 2
    assert all(l["page"].endswith("ref.md") for l in links)


def _make_page(path, slug, related):
    related_yaml = "\n".join(f'  - "{r}"' for r in related)
    if related_yaml:
        related_section = f"related:\n{related_yaml}\n"
    else:
        related_section = "related: []\n"
    path.write_text(
        f'---\ntitle: "{slug}"\ndescription: "x"\ntags: []\n'
        f"created: 2026-04-23\nupdated: 2026-04-23\nstatus: current\n"
        f"{related_section}---\n# {slug}\n"
    )


def test_bidirectional_link_adds_both_sides(tmp_path):
    wiki = tmp_path / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _make_page(wiki / "hooks" / "a.md", "a", [])
    _make_page(wiki / "hooks" / "b.md", "b", [])
    applier.apply_bidirectional_link(wiki, "a", "b")
    a_text = (wiki / "hooks" / "a.md").read_text()
    b_text = (wiki / "hooks" / "b.md").read_text()
    assert "[[b]]" in a_text
    assert "[[a]]" in b_text


def test_bidirectional_link_idempotent(tmp_path):
    wiki = tmp_path / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _make_page(wiki / "hooks" / "a.md", "a", ["[[b]]"])
    _make_page(wiki / "hooks" / "b.md", "b", ["[[a]]"])
    applier.apply_bidirectional_link(wiki, "a", "b")
    a_related = applier.frontmatter.parse(applier.frontmatter.split((wiki/"hooks"/"a.md").read_text())[0])["related"]
    assert a_related.count("[[b]]") == 1


def test_bidirectional_link_missing_page_raises(tmp_path):
    wiki = tmp_path / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _make_page(wiki / "hooks" / "a.md", "a", [])
    with pytest.raises(applier.SlugNotFound):
        applier.apply_bidirectional_link(wiki, "a", "ghost")


def test_check_path_inside_wiki_ok(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    target = wiki / "hooks" / "x.md"
    applier.check_write_path(target, llake_root=llake, wiki_root=wiki)


def test_check_path_in_discussions_rejected(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"
    (wiki / "discussions").mkdir(parents=True)
    target = wiki / "discussions" / "x.md"
    with pytest.raises(applier.ForbiddenPath):
        applier.check_write_path(target, llake_root=llake, wiki_root=wiki)


def test_check_path_outside_llake_rejected(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"
    (llake).mkdir()
    target = tmp_path / "elsewhere.md"
    with pytest.raises(applier.ForbiddenPath):
        applier.check_write_path(target, llake_root=llake, wiki_root=wiki)


def test_check_path_state_dir_rejected(tmp_path):
    llake = tmp_path / "llake"; (llake / ".state").mkdir(parents=True)
    target = llake / ".state" / "evil.md"
    with pytest.raises(applier.ForbiddenPath):
        applier.check_write_path(target, llake_root=llake, wiki_root=llake / "wiki")


def test_check_path_config_json_rejected(tmp_path):
    llake = tmp_path / "llake"; llake.mkdir()
    target = llake / "config.json"
    with pytest.raises(applier.ForbiddenPath):
        applier.check_write_path(target, llake_root=llake, wiki_root=llake / "wiki")


def test_check_path_log_md_allowed(tmp_path):
    llake = tmp_path / "llake"; llake.mkdir()
    target = llake / "log.md"
    applier.check_write_path(target, llake_root=llake, wiki_root=llake / "wiki", allow_log_md=True)


def test_check_path_symlink_escape_rejected(tmp_path):
    llake = tmp_path / "llake"; (llake / "wiki" / "hooks").mkdir(parents=True)
    outside = tmp_path / "outside.md"; outside.write_text("evil")
    link = llake / "wiki" / "hooks" / "evil.md"
    link.symlink_to(outside)
    with pytest.raises(applier.ForbiddenPath):
        applier.check_write_path(link, llake_root=llake, wiki_root=llake / "wiki")


def test_apply_delete_into_discussions_rejected_and_file_kept(tmp_path):
    """Discussions pages are invisible to apply_delete (skipped by _walk_wiki_pages).
    The slug resolves to target_already_absent and the file is never touched."""
    llake = tmp_path / "llake"; wiki = llake / "wiki"
    (wiki / "discussions").mkdir(parents=True)
    target = wiki / "discussions" / "captured.md"
    target.write_text("---\ntitle: c\n---\n# c\n")
    result = applier.apply_delete(wiki, "captured", today="2026-04-25", llake_root=llake)
    assert result["note"] == "target_already_absent", "discussions slug must appear absent to ingest"
    assert target.exists(), "_walk_wiki_pages skip must leave file untouched"


import json as _json
import subprocess as _sub

REPO_ROOT_AIP = Path(__file__).resolve().parents[2]
APPLIER_CLI = REPO_ROOT_AIP / "hooks" / "lib" / "apply_ingest_plan.py"


def _run_applier(plan_path, wiki, llake, applied, failed, today="2026-04-25"):
    cmd = ["python3", str(APPLIER_CLI),
           "--plan", str(plan_path),
           "--wiki-root", str(wiki),
           "--llake-root", str(llake),
           "--applied-out", str(applied),
           "--failed-out", str(failed),
           "--today", today]
    return _sub.run(cmd, capture_output=True, text=True)


def _write_log_md(llake):
    (llake / "log.md").write_text("")


def test_cli_applies_simple_update(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _write_log_md(llake)
    page = wiki / "hooks" / "a.md"
    page.write_text(SAMPLE_PAGE)
    plan = {
        "version": "1", "skip_reason": None, "summary": "trivial",
        "updates": [{"slug": "a", "rationale": "x",
                      "ops": [{"op": "frontmatter_set", "key": "description", "value": "Updated."}]}],
        "creates": [], "deletes": [], "bidirectional_links": [],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                       "summary": "y", "pages_affected": ["a"]}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode == 0, res.stderr
    assert "Updated." in (wiki / "hooks" / "a.md").read_text()
    assert _json.loads(applied.read_text())["updates"][0]["slug"] == "a"
    assert _json.loads(failed.read_text()) == []


def test_cli_records_anchor_failure_continues_others(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _write_log_md(llake)
    (wiki / "hooks" / "a.md").write_text(SAMPLE_PAGE)
    (wiki / "hooks" / "b.md").write_text(SAMPLE_PAGE.replace("Sample", "B"))
    plan = {
        "version": "1", "skip_reason": None, "summary": "x",
        "updates": [
            {"slug": "a", "rationale": "broken", "ops": [{"op": "replace", "find": "NOT_THERE", "with": "x"}]},
            {"slug": "b", "rationale": "ok", "ops": [{"op": "frontmatter_set", "key": "status", "value": "draft"}]},
        ],
        "creates": [], "deletes": [], "bidirectional_links": [],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                       "summary": "x", "pages_affected": ["a", "b"]}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode == 0, res.stderr  # best-effort: cursor advances
    f = _json.loads(failed.read_text())
    a_app = _json.loads(applied.read_text())
    assert any(e["slug"] == "a" and e["reason"] == "AnchorNotFound" for e in f)
    assert any(u["slug"] == "b" for u in a_app["updates"])


def test_cli_rejects_schema_invalid_plan(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; wiki.mkdir(parents=True)
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps({"bad": "plan"}))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode != 0
    assert "missing required key" in res.stderr or "version" in res.stderr


def test_cli_appends_log_entry(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _write_log_md(llake)
    page = wiki / "hooks" / "a.md"; page.write_text(SAMPLE_PAGE)
    plan = {
        "version": "1", "skip_reason": None, "summary": "Did the thing",
        "updates": [{"slug": "a", "rationale": "x",
                      "ops": [{"op": "frontmatter_set", "key": "description", "value": "y"}]}],
        "creates": [], "deletes": [], "bidirectional_links": [],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                       "summary": "Did the thing", "pages_affected": ["a"]}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode == 0
    log = (llake / "log.md").read_text()
    assert "## [2026-04-25] ingest | v2 | abc..def" in log
    assert "Did the thing" in log
    assert "[[a]]" in log


def test_cli_skip_reason_writes_skip_log_entry(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; wiki.mkdir(parents=True)
    _write_log_md(llake)
    plan = {
        "version": "1", "skip_reason": "no relevant changes",
        "summary": "n/a", "updates": [], "creates": [], "deletes": [],
        "bidirectional_links": [],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                       "summary": "n/a", "pages_affected": []}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode == 0
    # Skip path still writes a log.md entry (spec: "Always" log an entry).
    log = (llake / "log.md").read_text()
    assert "## [2026-04-25] ingest | v2 | abc..def" in log
    assert "skipped: no relevant changes" in log


def test_cli_partial_failure_writes_inline_failure_list(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _write_log_md(llake)
    (wiki / "hooks" / "broken.md").write_text(SAMPLE_PAGE)
    plan = {
        "version": "1", "skip_reason": None, "summary": "x",
        "updates": [{"slug": "broken", "rationale": "anchor missing",
                      "ops": [{"op": "replace", "find": "NOT_THERE", "with": "z"}]}],
        "creates": [], "deletes": [], "bidirectional_links": [],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                       "summary": "x", "pages_affected": ["broken"]}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode == 0
    log = (llake / "log.md").read_text()
    assert "ingest-failures | v2 | 1 remaining" in log
    assert "AnchorNotFound" in log
    assert "[[broken]]" in log


def test_bidirectional_link_skipped_when_partner_in_deletes(tmp_path):
    # Spec line 289: silently skip bidirectional_links where either side is in deletes[].
    # Verified at the CLI level — the skip logic lives in the orchestrator loop.
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _write_log_md(llake)
    (wiki / "hooks" / "live.md").write_text(SAMPLE_PAGE)
    (wiki / "hooks" / "doomed.md").write_text(SAMPLE_PAGE.replace("Sample", "Doomed"))
    plan = {
        "version": "1", "skip_reason": None, "summary": "x",
        "updates": [], "creates": [],
        "deletes": [{"slug": "doomed", "rationale": "removed"}],
        "bidirectional_links": [{"a": "live", "b": "doomed"}],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                       "summary": "x", "pages_affected": ["doomed"]}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode == 0, res.stderr
    bidir = _json.loads(applied.read_text())["bidirectional_links"]
    assert len(bidir) == 1
    assert bidir[0].get("note") == "skipped_partner_deleted"
    # The live page must NOT have gained a related: pointer to doomed.
    live_text = (wiki / "hooks" / "live.md").read_text()
    assert "[[doomed]]" not in live_text


def test_cli_bidir_ghost_slug_holds_cursor(tmp_path):
    llake = tmp_path / "llake"; wiki = llake / "wiki"; wiki.mkdir(parents=True)
    _write_log_md(llake)
    plan = {
        "version": "1", "skip_reason": None, "summary": "x",
        "updates": [], "creates": [], "deletes": [],
        "bidirectional_links": [{"a": "ghost-1", "b": "ghost-2"}],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                       "summary": "x", "pages_affected": []}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    # Schema-level integrity: cursor held, no log.md or output mutation.
    assert res.returncode != 0
    assert (llake / "log.md").read_text() == ""
    assert not applied.exists() or applied.read_text() == ""
    assert not failed.exists() or failed.read_text() == ""


def test_cli_malformed_frontmatter_routes_to_failed_json(tmp_path):
    """Spec contract: per-page errors land in failed.json with a reason; the
    applier does NOT crash and continue processing remaining ops.
    """
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _write_log_md(llake)
    # Page with frontmatter the strict parser rejects (nested mapping)
    (wiki / "hooks" / "broken.md").write_text(
        "---\nnested:\n  inner: bad\n---\n# B\n"
    )
    # Sibling valid page that should still succeed
    (wiki / "hooks" / "good.md").write_text(SAMPLE_PAGE.replace("Sample", "Good"))
    plan = {
        "version": "1", "skip_reason": None, "summary": "x",
        "updates": [
            {"slug": "broken", "rationale": "should fail",
             "ops": [{"op": "frontmatter_set", "key": "description", "value": "y"}]},
            {"slug": "good", "rationale": "should apply",
             "ops": [{"op": "frontmatter_set", "key": "description", "value": "ok"}]},
        ],
        "creates": [], "deletes": [], "bidirectional_links": [],
        "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                      "summary": "x", "pages_affected": ["broken", "good"]}
    }
    plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
    applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
    res = _run_applier(plan_path, wiki, llake, applied, failed)
    assert res.returncode == 0, f"applier crashed: {res.stderr}"
    f = _json.loads(failed.read_text())
    assert any(e["slug"] == "broken" and e["reason"] in
               ("FrontmatterParseError", "IOError") for e in f), \
        f"broken should be in failed.json with parse-error reason, got: {f}"
    a = _json.loads(applied.read_text())
    assert any(u["slug"] == "good" for u in a["updates"]), \
        f"good should still apply, got applied.json: {a}"


def test_cli_oserror_during_atomic_write_routes_to_failed(tmp_path):
    """Force _atomic_write to fail by making the page parent dir read-only."""
    import os, stat
    llake = tmp_path / "llake"; wiki = llake / "wiki"; (wiki / "hooks").mkdir(parents=True)
    _write_log_md(llake)
    (wiki / "hooks" / "ro.md").write_text(SAMPLE_PAGE)
    (wiki / "hooks" / "ok.md").write_text(SAMPLE_PAGE.replace("Sample", "Ok"))
    # Make the parent dir read-only so the atomic-write tempfile create fails
    parent = wiki / "hooks"
    original_mode = parent.stat().st_mode
    os.chmod(parent, stat.S_IRUSR | stat.S_IXUSR)  # 0o500
    try:
        plan = {
            "version": "1", "skip_reason": None, "summary": "x",
            "updates": [
                {"slug": "ro", "rationale": "ro fails",
                 "ops": [{"op": "frontmatter_set", "key": "description", "value": "y"}]},
            ],
            "creates": [], "deletes": [], "bidirectional_links": [],
            "log_entry": {"operation": "ingest", "commit_range": "abc..def",
                          "summary": "x", "pages_affected": ["ro"]}
        }
        plan_path = tmp_path / "plan.json"; plan_path.write_text(_json.dumps(plan))
        applied = tmp_path / "applied.json"; failed = tmp_path / "failed.json"
        res = _run_applier(plan_path, wiki, llake, applied, failed)
        assert res.returncode == 0, f"applier crashed: {res.stderr}"
        f = _json.loads(failed.read_text())
        assert any(e["slug"] == "ro" and e["reason"] == "IOError" for e in f), \
            f"ro should be in failed.json with IOError, got: {f}"
    finally:
        os.chmod(parent, original_mode)


def test_scrub_related_skips_discussions(tmp_path):
    """Spec: wiki/discussions/** is owned by session-capture; ingest must not
    mutate it even via cascade scrub on delete."""
    wiki = tmp_path / "wiki"
    (wiki / "hooks").mkdir(parents=True)
    (wiki / "discussions").mkdir(parents=True)
    # The page being deleted
    (wiki / "hooks" / "doomed.md").write_text(SAMPLE_PAGE.replace("Sample", "Doomed"))
    # A discussion entry that references the doomed slug
    discussion_text = (
        '---\ntitle: D\nrelated:\n  - "[[doomed]]"\n  - "[[keeper]]"\n---\n# D\n'
    )
    (wiki / "discussions" / "topic.md").write_text(discussion_text)
    # A regular page that also references it (should be scrubbed)
    (wiki / "hooks" / "other.md").write_text(
        '---\ntitle: O\nrelated:\n  - "[[doomed]]"\n---\n# O\n'
    )
    applier.apply_delete(wiki, "doomed", today="2026-04-26")
    # Discussion file: untouched (still contains [[doomed]])
    assert (wiki / "discussions" / "topic.md").read_text() == discussion_text
    # Regular page: scrubbed
    other_text = (wiki / "hooks" / "other.md").read_text()
    assert "[[doomed]]" not in other_text


def test_scan_inline_links_skips_discussions(tmp_path):
    """dangling_inline_links should not include discussion-page bodies."""
    wiki = tmp_path / "wiki"
    (wiki / "hooks").mkdir(parents=True)
    (wiki / "discussions").mkdir(parents=True)
    (wiki / "hooks" / "doomed.md").write_text(SAMPLE_PAGE.replace("Sample", "D"))
    (wiki / "discussions" / "topic.md").write_text(
        "---\ntitle: T\n---\n# T\n\nReferences [[doomed]] inline.\n"
    )
    (wiki / "hooks" / "ref.md").write_text(
        "---\ntitle: R\n---\n# R\n\nAlso [[doomed]] here.\n"
    )
    result = applier.apply_delete(wiki, "doomed", today="2026-04-26")
    pages = {l["page"] for l in result["dangling_inline_links"]}
    assert all("discussions" not in p for p in pages), \
        f"dangling list leaked discussions: {pages}"
    assert any("ref.md" in p for p in pages)


def test_resolve_slug_path_skips_discussions(tmp_path):
    """A page in discussions/ with the same slug as a regular page must not
    win the lexicographic sort — the regular page should resolve."""
    wiki = tmp_path / "wiki"
    (wiki / "hooks").mkdir(parents=True)
    (wiki / "discussions").mkdir(parents=True)
    # discussions/abc.md sorts before hooks/abc.md alphabetically
    (wiki / "discussions" / "abc.md").write_text("---\ntitle: D\n---\n# D\n")
    (wiki / "hooks" / "abc.md").write_text(
        '---\ntitle: H\nrelated: []\n---\n# H\n'
    )
    # Resolve the slug — must pick the hooks/ one, not discussions/
    resolved = applier._resolve_slug_path(wiki, "abc")
    assert "hooks" in str(resolved)
    assert "discussions" not in str(resolved)
