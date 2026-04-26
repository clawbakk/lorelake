#!/usr/bin/env python3
"""LoreLake ingest v2 plan applier.

Reads a validated plan.json and applies it to the wiki. Pure stdlib.

This module is incrementally built up across several tasks. This file currently
provides only the `replace` op semantics (Task 6). Subsequent tasks add
append_section, frontmatter ops, body_replace, creates, deletes, and the CLI.
"""


class ApplyError(Exception):
    """Base for all per-op apply errors. Subclasses map to failed.json reasons."""
    reason = "ApplyError"


class AnchorNotFound(ApplyError):
    reason = "AnchorNotFound"


class AnchorAmbiguous(ApplyError):
    reason = "AnchorAmbiguous"


class EditOverlap(ApplyError):
    reason = "EditOverlap"


def apply_replace_ops(original, ops):
    """Apply a list of {op: replace, find, with} ops to `original` content.

    Anchors are resolved against the ORIGINAL (not the running result), then
    applied in reverse-position order so offsets don't shift.

    Raises:
        AnchorNotFound — a `find` doesn't appear in the original.
        AnchorAmbiguous — a `find` appears more than once.
        EditOverlap — two anchors share any bytes in the original.
    """
    spans = []
    for op in ops:
        if op.get("op") != "replace":
            continue
        anchor = op["find"]
        count = original.count(anchor)
        if count == 0:
            raise AnchorNotFound(f"anchor not found: {anchor!r}")
        if count > 1:
            raise AnchorAmbiguous(f"anchor appears {count} times (must be unique): {anchor!r}")
        idx = original.find(anchor)
        spans.append((idx, idx + len(anchor), op["with"]))
    spans.sort(key=lambda s: s[0])
    for (s1, e1, _), (s2, _, _) in zip(spans, spans[1:]):
        if e1 > s2:
            raise EditOverlap(f"overlap between span ending at {e1} and span starting at {s2}")
    result = original
    for start, end, replacement in reversed(spans):
        result = result[:start] + replacement + result[end:]
    return result
