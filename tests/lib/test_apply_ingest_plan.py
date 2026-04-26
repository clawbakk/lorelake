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
