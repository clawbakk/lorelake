"""Tests for frontmatter.py — minimal YAML subset for wiki pages."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks" / "lib"))
import frontmatter  # noqa: E402

SAMPLE = """---
title: "Sample Page"
description: "A test page."
tags: [hooks, sample, test]
created: 2026-04-23
updated: 2026-04-24
status: current
related:
  - "[[other-page]]"
  - "[[another-page]]"
---
# Body

Some body text.
"""


def test_parse_round_trip():
    fm, body = frontmatter.split(SAMPLE)
    parsed = frontmatter.parse(fm)
    assert parsed["title"] == "Sample Page"
    assert parsed["tags"] == ["hooks", "sample", "test"]
    assert parsed["related"] == ["[[other-page]]", "[[another-page]]"]
    serialized = frontmatter.serialize(parsed)
    re_parsed = frontmatter.parse(serialized)
    assert re_parsed == parsed


def test_split_empty_frontmatter():
    text = "Just body, no frontmatter\n"
    fm, body = frontmatter.split(text)
    assert fm == ""
    assert body == text


def test_serialize_preserves_key_order_when_round_tripping():
    fm, _ = frontmatter.split(SAMPLE)
    parsed = frontmatter.parse(fm)
    keys_in_serialized = []
    for line in frontmatter.serialize(parsed).splitlines():
        if ":" in line and not line.startswith(" "):
            keys_in_serialized.append(line.split(":", 1)[0])
    assert keys_in_serialized == ["title", "description", "tags", "created",
                                   "updated", "status", "related"]


def test_parse_unsupported_shape_raises():
    with pytest.raises(frontmatter.FrontmatterParseError):
        frontmatter.parse("nested:\n  inner:\n    key: value\n")
