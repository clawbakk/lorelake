"""The subset JSON-Schema validator and the five v3 schema files."""
import pytest

import v3_helpers  # noqa: F401
from ingest_v3.schema import load_schema, validate

GOOD_PAGE = {"path": "llake/wiki/a/b.md", "severity": "major", "reason": "r",
             "stale": [{"quote": "q", "head": "src/x.py:1", "severity": "minor"}]}
GOOD_STATUS = {"pages": [{"page": "llake/wiki/a/b.md", "status": "corrected", "claimsLeft": [],
                          "rejected": [], "note": ""}], "otherStale": []}
GOOD_FINDINGS = {"pages": [{"page": "wiki/a/b.md", "accuracy": [], "residual": [
    {"quote": "q", "head": "h", "severity": "major"}]}]}
GOOD_GAPS = {"version": 1, "asOf": "abc", "agent": "a", "date": "2026-10-02",
             "gaps": [{"page": "wiki/a/b.md", "severity": "minor", "cause": "declared", "since": "abc",
                       "attempts": 1, "stuck": False,
                       "claims": [{"quote": "q", "head": "h", "severity": "minor", "source": "brief"}]}],
             "ranges": [{"base": "a", "head": "b", "cause": "analysis-failed", "leads": ["x"]}]}


def test_type_required_enum():
    s = {"type": "object", "required": ["a"], "properties": {"a": {"enum": [1, 2]}}}
    assert validate({"a": 1}, s) == []
    assert any("missing required 'a'" in e for e in validate({}, s))
    assert any("not one of" in e for e in validate({"a": 3}, s))
    assert any("expected object" in e for e in validate([], s))


def test_bool_is_not_integer():
    assert validate(True, {"type": "integer"}) != []
    assert validate(3, {"type": ["integer", "null"]}) == []


def test_additional_properties_false_and_ref_and_min():
    s = {"type": "object", "additionalProperties": False,
         "properties": {"xs": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/s"}}},
         "$defs": {"s": {"type": "string", "minLength": 2}}}
    assert validate({"xs": ["ab"]}, s) == []
    assert validate({"xs": []}, s) != []
    assert validate({"xs": ["a"]}, s) != []
    assert any("unexpected property 'y'" in e for e in validate({"xs": ["ab"], "y": 1}, s))


@pytest.mark.parametrize("name,good", [
    ("brief-page", GOOD_PAGE), ("writer-status", GOOD_STATUS),
    ("verifier-findings", GOOD_FINDINGS), ("gap-record", GOOD_GAPS),
    ("brief-themes", [{"id": "T1", "title": "t", "summary": "s"}]),
])
def test_schema_accepts_good_example(name, good):
    assert validate(good, load_schema(name)) == []


@pytest.mark.parametrize("name,bad", [
    ("brief-page", {"path": "p", "severity": "huge", "reason": "r", "stale": []}),
    ("brief-page", {"path": "p", "severity": "major", "reason": "r",
                    "stale": [{"quote": "q", "severity": "minor"}]}),
    ("brief-themes", []),
    ("writer-status", {"pages": [{"page": "p", "status": "done", "claimsLeft": [], "rejected": [], "note": ""}],
                       "otherStale": []}),
    ("gap-record", dict(GOOD_GAPS, version=2)),
    ("gap-record", dict(GOOD_GAPS, gaps=[dict(GOOD_GAPS["gaps"][0], cause="bored")])),
    ("gap-record", dict(GOOD_GAPS, gaps=[dict(GOOD_GAPS["gaps"][0], claims=[])])),
    ("gap-record", dict(GOOD_GAPS, ranges=[{"base": "a", "head": "b", "cause": "analysis-failed", "leads": []}])),
])
def test_schema_rejects_bad_example(name, bad):
    assert validate(bad, load_schema(name)) != []
