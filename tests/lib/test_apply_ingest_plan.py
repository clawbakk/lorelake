"""Tests for apply-ingest-plan.py — applies an ingest v2 plan to a wiki."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks" / "lib"))
import importlib
applier = importlib.import_module("apply-ingest-plan")


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


import importlib as _importlib
fm = _importlib.import_module("frontmatter")

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
    llake = tmp_path / "llake"; wiki = llake / "wiki"
    (wiki / "discussions").mkdir(parents=True)
    target = wiki / "discussions" / "captured.md"
    target.write_text("---\ntitle: c\n---\n# c\n")
    with pytest.raises(applier.ForbiddenPath):
        applier.apply_delete(wiki, "captured", today="2026-04-25", llake_root=llake)
    assert target.exists(), "ForbiddenPath must abort before unlink"
