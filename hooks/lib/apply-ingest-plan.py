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


import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class HeadingNotFound(ApplyError):
    reason = "HeadingNotFound"


def _heading_level(line):
    m = re.match(r"^(#{1,6})\s+", line)
    return len(m.group(1)) if m else 0


def _find_section_end(text, heading_start, heading_level):
    """Return the byte offset where a section ends.

    A section ends at the next heading of the same or higher level (lower or
    equal `#` count), or EOF.
    """
    pos = heading_start
    line_end = text.find("\n", pos)
    pos = line_end + 1 if line_end != -1 else len(text)
    while pos < len(text):
        next_line_end = text.find("\n", pos)
        line = text[pos:next_line_end] if next_line_end != -1 else text[pos:]
        m = re.match(r"^(#{1,6})\s+", line)
        if m and len(m.group(1)) <= heading_level:
            return pos
        if next_line_end == -1:
            return len(text)
        pos = next_line_end + 1
    return len(text)


def apply_section_ops(content, ops):
    """Apply append_section ops in declaration order. Each op operates on the
    running text (not the original), so multiple appends to the same section
    work the way the planner expects."""
    result = content
    for op in ops:
        if op.get("op") != "append_section":
            continue
        target_heading = op["after_heading"]
        # Match the heading at line start.
        pattern = re.compile(r"^" + re.escape(target_heading) + r"\s*$", re.MULTILINE)
        m = pattern.search(result)
        if not m:
            raise HeadingNotFound(f"heading not found: {target_heading!r}")
        level = _heading_level(target_heading)
        end = _find_section_end(result, m.start(), level)
        # Ensure we insert BEFORE the next heading line (no extra blank line collapse).
        # The convention: section content ends with \n; appended content also ends with \n;
        # we keep one blank line between sections.
        before = result[:end].rstrip("\n") + "\n"
        appended = op["content"] if op["content"].endswith("\n") else op["content"] + "\n"
        after = "\n" + result[end:] if result[end:] else ""
        result = before + appended + after
    return result


def apply_body_replace(content, ops):
    """If a body_replace op is present, return its content. Otherwise return the input unchanged."""
    for op in ops:
        if op.get("op") == "body_replace":
            return op["content"]
    return content


import os
import importlib

frontmatter = importlib.import_module("frontmatter")


def apply_frontmatter_ops(fm_dict, ops):
    """Return a new dict with frontmatter_* ops applied. Idempotent for add_related."""
    out = dict(fm_dict)
    for op in ops:
        kind = op.get("op")
        if kind == "frontmatter_set":
            out[op["key"]] = op["value"]
        elif kind == "frontmatter_add_related":
            existing = list(out.get("related") or [])
            for item in op["items"]:
                if item not in existing:
                    existing.append(item)
            out["related"] = existing
    return out


def _atomic_write(path, content):
    """Write `content` to `path` atomically via a sibling tempfile + os.replace."""
    parent = os.path.dirname(str(path)) or "."
    tmp = os.path.join(parent, f".{os.path.basename(str(path))}.tmp")
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, str(path))


def apply_update(page_path, update, today, llake_root=None, wiki_root=None):
    """Apply a single `updates[]` entry to `page_path` atomically.

    `today` is the ISO date string used to bump `updated:`. Tests pass it explicitly
    so they don't depend on the system clock.
    """
    if llake_root is not None and wiki_root is not None:
        check_write_path(page_path, llake_root, wiki_root)
    original = page_path.read_text()
    fm_text, body = frontmatter.split(original)
    fm_dict = frontmatter.parse(fm_text) if fm_text else {}

    ops = update["ops"]

    # Body: body_replace wins over surgical ops in the same update.
    if any(op.get("op") == "body_replace" for op in ops):
        new_body = apply_body_replace(body, ops)
    else:
        new_body = apply_replace_ops(body, ops)
        new_body = apply_section_ops(new_body, ops)

    new_fm = apply_frontmatter_ops(fm_dict, ops)
    new_fm["updated"] = today

    if new_fm:
        new_text = "---\n" + frontmatter.serialize(new_fm) + "---\n" + new_body
    else:
        new_text = new_body
    _atomic_write(page_path, new_text)


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
