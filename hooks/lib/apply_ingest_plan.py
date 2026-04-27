#!/usr/bin/env python3
"""LoreLake ingest v2 plan applier.

Reads a validated plan.json and applies it to a project's LoreLake wiki under
<llake-root>/wiki/. Pure stdlib.

Public API (importable):
    apply_replace_ops(content, ops)               — surgical replace ops
    apply_section_ops(content, ops)               — append_section ops
    apply_body_replace(content, ops)              — escape-hatch full-body
    apply_frontmatter_ops(fm_dict, ops)           — frontmatter_* ops
    apply_update(page_path, update, today, ...)   — one updates[] entry
    apply_create(wiki_root, create, today, ...)   — one creates[] entry
    apply_delete(wiki_root, slug, today, ...)     — one deletes[] entry + cascade
    apply_bidirectional_link(wiki_root, a, b)     — one bidirectional_links[] entry
    check_write_path(target, llake_root, ...)     — forbidden-write-surface guard

Public exceptions: ApplyError (base), AnchorNotFound, AnchorAmbiguous, EditOverlap,
HeadingNotFound, AlreadyExists, SlugNotFound, ForbiddenPath. Anything else (OSError,
UnicodeDecodeError, FrontmatterParseError) is mapped to reason='IOError' or
'FrontmatterParseError' by _classify_error and lands in failed.json.

CLI:
    python3 apply_ingest_plan.py --plan PATH --wiki-root PATH --llake-root PATH \\
        --applied-out PATH --failed-out PATH --today YYYY-MM-DD [--no-log-entry]

Exit codes:
    0  — applied (with or without per-op failures captured in failed.json)
    1  — schema-invalid plan or bidir-link existence check failed (cursor held)
    2  — plan file unreadable / not valid JSON
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# --- Sibling imports (require the lib dir on sys.path; the post-merge hook
#     invokes this script from a wrapper that sets PYTHONPATH; tests insert
#     the parent dir before importing.) ---
_LIB_DIR = Path(__file__).resolve().parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

import frontmatter
import plan_schema


# --- Exception classes ---

class ApplyError(Exception):
    """Base for all per-op apply errors. Subclasses map to failed.json reasons."""
    reason = "ApplyError"


class AnchorNotFound(ApplyError):
    reason = "AnchorNotFound"


class AnchorAmbiguous(ApplyError):
    reason = "AnchorAmbiguous"


class EditOverlap(ApplyError):
    reason = "EditOverlap"


class HeadingNotFound(ApplyError):
    reason = "HeadingNotFound"


class AlreadyExists(ApplyError):
    reason = "AlreadyExists"


class SlugNotFound(ApplyError):
    reason = "SlugNotFound"


class ForbiddenPath(ApplyError):
    reason = "ForbiddenPath"


# --- Constants ---

_FORBIDDEN_SUBTREES = (".state", "schema")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


# --- Internal helpers ---

def _atomic_write(path, content):
    """Write `content` to `path` atomically via a sibling tempfile + os.replace."""
    parent = os.path.dirname(str(path)) or "."
    tmp = os.path.join(parent, f".{os.path.basename(str(path))}.tmp")
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, str(path))


def _walk_wiki_pages(wiki_root):
    """Yield every wiki page file EXCEPT those under wiki_root/discussions/.

    Discussions are owned by session-capture (CLAUDE.md three-writer model);
    ingest must never read them as scrub/index targets. Tests pin this.
    Skips non-files (directories named X.md, dangling symlinks, etc.).
    """
    for page in wiki_root.rglob("*.md"):
        if not page.is_file():
            continue
        try:
            rel_parts = page.relative_to(wiki_root).parts
        except ValueError:
            continue
        if rel_parts and rel_parts[0] == "discussions":
            continue
        yield page


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


def _resolve_slug_path(wiki_root, slug):
    matches = [p for p in _walk_wiki_pages(wiki_root) if p.name == f"{slug}.md"]
    if not matches:
        raise SlugNotFound(f"slug not found in wiki: {slug}")
    matches.sort()
    return matches[0]


def _ensure_related_contains(page_path, token):
    text = page_path.read_text()
    fm_text, body = frontmatter.split(text)
    fm_dict = frontmatter.parse(fm_text) if fm_text else {}
    related = list(fm_dict.get("related") or [])
    if token not in related:
        related.append(token)
        fm_dict["related"] = related
        new_text = "---\n" + frontmatter.serialize(fm_dict) + "---\n" + body
        _atomic_write(page_path, new_text)


def _scrub_related(wiki_root, deleted_slug):
    """Remove `deleted_slug` from every wiki page's frontmatter `related:` list.
    Idempotent. Returns count of pages modified."""
    target_token = f"[[{deleted_slug}]]"
    count = 0
    for page in _walk_wiki_pages(wiki_root):
        try:
            text = page.read_text()
        except (IOError, OSError):
            continue
        fm_text, body = frontmatter.split(text)
        if not fm_text:
            continue
        try:
            fm_dict = frontmatter.parse(fm_text)
        except frontmatter.FrontmatterParseError:
            continue
        related = fm_dict.get("related") or []
        if target_token not in related:
            continue
        fm_dict["related"] = [r for r in related if r != target_token]
        new_text = "---\n" + frontmatter.serialize(fm_dict) + "---\n" + body
        _atomic_write(page, new_text)
        count += 1
    return count


def _scan_inline_links(wiki_root, deleted_slug):
    """Return a list of {slug, page, line, line_text} for every body occurrence
    of [[<deleted_slug>]]."""
    pattern = re.compile(r"\[\[" + re.escape(deleted_slug) + r"\]\]")
    out = []
    for page in _walk_wiki_pages(wiki_root):
        try:
            text = page.read_text()
        except (IOError, OSError):
            continue
        _, body = frontmatter.split(text)
        for lineno, line in enumerate(body.splitlines(), start=1):
            if pattern.search(line):
                # Multiple matches on the same line still surface as one entry per match.
                for _ in pattern.findall(line):
                    out.append({
                        "slug": deleted_slug,
                        "page": str(page),
                        "line": lineno,
                        "line_text": line,
                    })
    return out


# --- Per-op functions ---

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


# --- Per-update orchestration ---

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


def apply_create(wiki_root, create, today, llake_root=None):
    """Write a new wiki page under <wiki_root>/<category>/<slug>.md."""
    target = wiki_root / create["category"] / f"{create['slug']}.md"
    if llake_root is not None:
        check_write_path(target, llake_root, wiki_root)
    if target.exists():
        raise AlreadyExists(f"target already exists: {target}")
    fm = dict(create["front_matter"])
    fm["updated"] = today
    text = "---\n" + frontmatter.serialize(fm) + "---\n" + create["body"]
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, text)


def apply_delete(wiki_root, slug, today, llake_root=None):
    """Remove the page for `slug` from the wiki. Returns a dict describing what happened.

    On no-op (target absent): returns {"note": "target_already_absent", "dangling_inline_links": []}.
    On real delete: returns {"dangling_inline_links": [...]}; cascades scrub the slug
    from every other page's `related:` and surface inline [[slug]] mentions as warnings.
    """
    # Resolve the page by walking the wiki for <slug>.md.
    matches = [p for p in _walk_wiki_pages(wiki_root) if p.name == f"{slug}.md"]
    if not matches:
        return {"note": "target_already_absent", "dangling_inline_links": []}
    if len(matches) > 1:
        # Two pages with the same slug is a wiki integrity bug, but not the applier's
        # to resolve. Pick the first deterministically and surface in the result.
        matches.sort()
    target = matches[0]
    if llake_root is not None:
        check_write_path(target, llake_root, wiki_root)
    target.unlink()
    _scrub_related(wiki_root, slug)
    inline = _scan_inline_links(wiki_root, slug)
    return {"dangling_inline_links": inline}


def apply_bidirectional_link(wiki_root, slug_a, slug_b):
    """Ensure page_a's related: contains [[b]] and page_b's contains [[a]].
    Idempotent. Raises SlugNotFound if either page is missing."""
    a_path = _resolve_slug_path(wiki_root, slug_a)
    b_path = _resolve_slug_path(wiki_root, slug_b)
    _ensure_related_contains(a_path, f"[[{slug_b}]]")
    _ensure_related_contains(b_path, f"[[{slug_a}]]")


# --- Path-guard ---

def check_write_path(target, llake_root, wiki_root, allow_log_md=False):
    """Raise ForbiddenPath unless `target` is a permitted destination.

    Permitted: <wiki_root>/<not-discussions>/**, plus <llake_root>/log.md if
    allow_log_md=True. Forbidden: discussions/**, .state/**, schema/** (anywhere),
    config.json, last-ingest-sha, anything outside <llake_root>/.

    Symlink escapes are defeated via realpath.
    """
    real_target = os.path.realpath(str(target))
    real_llake = os.path.realpath(str(llake_root))
    real_wiki = os.path.realpath(str(wiki_root))
    sep = os.sep

    if not real_target.startswith(real_llake + sep) and real_target != real_llake:
        raise ForbiddenPath(f"target outside llake_root: {real_target}")

    rel = real_target[len(real_llake) + 1:] if real_target != real_llake else ""

    if allow_log_md and rel == "log.md":
        return

    forbidden_files = {"config.json", "last-ingest-sha"}
    if rel in forbidden_files:
        raise ForbiddenPath(f"forbidden file: {rel}")

    parts = rel.split(sep)
    if parts and parts[0] in _FORBIDDEN_SUBTREES:
        raise ForbiddenPath(f"forbidden subtree: {parts[0]}")

    # Wiki writes must be inside wiki_root and not in discussions/.
    if not real_target.startswith(real_wiki + sep):
        raise ForbiddenPath(f"non-wiki target: {real_target}")
    wiki_rel = real_target[len(real_wiki) + 1:]
    wiki_parts = wiki_rel.split(sep)
    if wiki_parts and wiki_parts[0] == "discussions":
        raise ForbiddenPath("wiki/discussions/** is owned by session-capture")


# --- Internal CLI helpers ---

def _classify_error(exc):
    """Map an exception to the failed.json `reason` string. Per spec line 525,
    non-ApplyError I/O failures land here too."""
    if isinstance(exc, ApplyError):
        return exc.reason
    if isinstance(exc, frontmatter.FrontmatterParseError):
        return "FrontmatterParseError"
    return "IOError"


def _normalize_plan_text(text):
    """Strip markdown fences and prose wrapping from a planner-emitted plan.

    Tries, in order:
      1. The text as-is (after .strip()).
      2. Strip a leading ```json or ``` fence and matching trailing ```.
      3. Find the first balanced top-level JSON object {...} in the text.

    Returns the normalized JSON text. Does NOT validate JSON — caller does that
    via json.loads. Raises ValueError if no JSON-looking object is found at all.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty plan text")

    # Step 1: as-is
    if text.startswith("{"):
        return text

    # Step 2: strip a code fence
    if text.startswith("```"):
        # Remove first line (the ``` or ```json marker)
        first_newline = text.find("\n")
        if first_newline != -1:
            inner = text[first_newline + 1:]
        else:
            inner = ""
        # Remove trailing fence if present
        inner = inner.rstrip()
        if inner.endswith("```"):
            inner = inner[:-3].rstrip()
        if inner.startswith("{"):
            return inner

    # Step 3: find first balanced { ... } object
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                return text[start:i + 1]

    raise ValueError("no JSON object found in planner output")


def _append_log_entry(llake_root, today, log_entry, has_failures, skip_reason=None, failures=None):
    log_path = llake_root / "log.md"
    if skip_reason:
        skip_line = (
            f"## [{today}] ingest | v2 | {log_entry['commit_range']} "
            f"| skipped: {skip_reason}\n"
        )
        with open(log_path, "a") as f:
            f.write("\n" + skip_line + "\n")
        return
    line = f"## [{today}] ingest | v2 | {log_entry['commit_range']} | {log_entry['summary']}\n"
    pages = log_entry.get("pages_affected") or []
    pages_line = "Pages affected: " + ", ".join(f"[[{p}]]" for p in pages) + "\n" if pages else ""
    suffix = "\n" + line + pages_line + "\n"
    with open(log_path, "a") as f:
        f.write(suffix)
    if has_failures:
        fails = failures or []
        fail_header = f"## [{today}] ingest-failures | v2 | {len(fails)} remaining\n"
        with open(log_path, "a") as f:
            f.write(fail_header)
            for fail in fails:
                slug = fail.get("slug", "?")
                reason = fail.get("reason", "?")
                detail = (fail.get("detail") or "")
                if len(detail) > 120:
                    detail = detail[:117] + "..."
                f.write(f"- [[{slug}]]: {reason} — {detail}\n")
            f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--wiki-root", required=True)
    ap.add_argument("--llake-root", required=True)
    ap.add_argument("--applied-out", required=True)
    ap.add_argument("--failed-out", required=True)
    ap.add_argument("--today", required=True, help="ISO date for `updated:` bumps and log entry")
    ap.add_argument("--no-log-entry", action="store_true",
                    help="Skip appending to log.md. Used by the orchestrator on the fix-pass "
                         "invocation so a single ingest run produces a single log entry.")
    ap.add_argument("--changes-json",
                    help="Path to context/changes.json. When set, the applier "
                         "verifies that commits_addressed ∪ commits_skipped equals "
                         "the commit range. When unset (e.g. fixer pass), the check "
                         "is skipped — fixer plans only carry the failed-update slice.")
    args = ap.parse_args()

    llake_root = Path(args.llake_root)
    wiki_root = Path(args.wiki_root)

    try:
        raw = Path(args.plan).read_text()
    except (IOError, OSError) as e:
        print(f"apply_ingest_plan: cannot read plan {args.plan}: {e}", file=sys.stderr)
        sys.exit(2)
    try:
        normalized = _normalize_plan_text(raw)
    except ValueError as e:
        print(f"apply_ingest_plan: non-JSON planner output ({e}); first 200 chars: "
              f"{raw[:200]!r}", file=sys.stderr)
        sys.exit(2)
    try:
        plan = json.loads(normalized)
    except json.JSONDecodeError as e:
        print(f"apply_ingest_plan: schema-invalid JSON in plan {args.plan}: {e}",
              file=sys.stderr)
        sys.exit(2)

    schema_errors = plan_schema.validate(plan)
    if schema_errors:
        for e in schema_errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    # Skip-reason early return must come BEFORE the --changes-json cross-check.
    # A skip plan has empty commits_addressed/skipped (by spec) — the cross-check
    # would incorrectly flag every commit in the range as uncovered. Schema
    # validation still runs above, so commits_addressed/commits_skipped must still
    # be present (as empty arrays) to pass schema; but the cross-check is bypassed.
    if plan.get("skip_reason"):
        Path(args.applied_out).write_text(json.dumps(
            {"updates": [], "creates": [], "deletes": [], "bidirectional_links": []}))
        Path(args.failed_out).write_text("[]")
        if not args.no_log_entry:
            _append_log_entry(llake_root, args.today, plan["log_entry"],
                              has_failures=False, skip_reason=plan["skip_reason"])
        return 0

    # Per-commit accountability (intervention B). Skipped on the fixer pass
    # because that plan only covers the failed-update slice, not the whole range.
    if args.changes_json:
        try:
            changes = json.loads(Path(args.changes_json).read_text())
        except (IOError, OSError, json.JSONDecodeError) as e:
            print(f"apply_ingest_plan: cannot read --changes-json {args.changes_json}: {e}",
                  file=sys.stderr)
            sys.exit(1)
        # Use the 'short' SHAs as canonical because that's what the planner sees in
        # changes.json. We also accept full SHAs that prefix-match a known short.
        expected_shorts = {c["short"] for c in changes.get("commits", [])}
        expected_full = {c["sha"] for c in changes.get("commits", [])}

        def _normalize(sha):
            if sha in expected_shorts:
                return sha
            if sha in expected_full:
                # Map full back to short
                for c in changes["commits"]:
                    if c["sha"] == sha:
                        return c["short"]
            # Prefix match — planner may have used a longer-than-short SHA
            for short in expected_shorts:
                if sha.startswith(short) or short.startswith(sha):
                    return short
            return None

        addressed_norm = set()
        skipped_norm = set()
        unknown = []
        for entry in plan["commits_addressed"]:
            n = _normalize(entry["sha"])
            if n is None:
                unknown.append(entry["sha"])
            else:
                addressed_norm.add(n)
        for entry in plan["commits_skipped"]:
            n = _normalize(entry["sha"])
            if n is None:
                unknown.append(entry["sha"])
            else:
                skipped_norm.add(n)
        if unknown:
            for sha in unknown:
                print(f"apply_ingest_plan: commit {sha!r} not in range "
                      f"{changes['range']}", file=sys.stderr)
            sys.exit(1)
        uncovered = expected_shorts - addressed_norm - skipped_norm
        if uncovered:
            print(f"apply_ingest_plan: {len(uncovered)} commits in range "
                  f"{changes['range']} are uncovered (missing from "
                  f"commits_addressed and commits_skipped):", file=sys.stderr)
            for sha in sorted(uncovered):
                print(f"  - {sha}", file=sys.stderr)
            sys.exit(1)

    # Bidirectional-link existence check (treated as schema-level — cursor held on
    # failure). The plan_schema validator can't see the wiki; the CLI can.
    known_slugs = {p.stem for p in wiki_root.rglob("*.md")} | {c["slug"] for c in plan["creates"]}
    ref_errors = []
    for i, link in enumerate(plan["bidirectional_links"]):
        for side in ("a", "b"):
            slug = link.get(side)
            if slug not in known_slugs:
                ref_errors.append(
                    f"bidirectional_links[{i}].{side}: slug {slug!r} does not exist "
                    "in wiki and is not in creates[]"
                )
    if ref_errors:
        for e in ref_errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    applied = {"updates": [], "creates": [], "deletes": [], "bidirectional_links": []}
    failed = []

    for upd in plan["updates"]:
        slug = upd["slug"]
        try:
            page_path = _resolve_slug_path(wiki_root, slug)
            apply_update(page_path, upd, today=args.today,
                         llake_root=llake_root, wiki_root=wiki_root)
            applied["updates"].append({"slug": slug, "ops_applied": len(upd["ops"])})
        except (ApplyError, OSError, UnicodeDecodeError, frontmatter.FrontmatterParseError) as e:
            failed.append({"slug": slug, "reason": _classify_error(e), "detail": str(e)})

    for c in plan["creates"]:
        slug = c["slug"]
        try:
            apply_create(wiki_root, c, today=args.today, llake_root=llake_root)
            applied["creates"].append({"slug": slug, "category": c["category"]})
        except (ApplyError, OSError, UnicodeDecodeError, frontmatter.FrontmatterParseError) as e:
            failed.append({"slug": slug, "reason": _classify_error(e), "detail": str(e)})

    for d in plan["deletes"]:
        slug = d["slug"]
        try:
            res = apply_delete(wiki_root, slug, today=args.today, llake_root=llake_root)
            entry = {"slug": slug, "dangling_inline_links": res.get("dangling_inline_links", [])}
            if res.get("note"):
                entry["note"] = res["note"]
            applied["deletes"].append(entry)
        except (ApplyError, OSError, UnicodeDecodeError, frontmatter.FrontmatterParseError) as e:
            failed.append({"slug": slug, "reason": _classify_error(e), "detail": str(e)})

    # Spec line 289: silently skip bidirectional_links where either side is in deletes[].
    deleted_slugs = {d["slug"] for d in plan["deletes"]}
    for link in plan["bidirectional_links"]:
        if link["a"] in deleted_slugs or link["b"] in deleted_slugs:
            applied["bidirectional_links"].append({
                "a": link["a"], "b": link["b"],
                "note": "skipped_partner_deleted",
            })
            continue
        try:
            apply_bidirectional_link(wiki_root, link["a"], link["b"])
            applied["bidirectional_links"].append({"a": link["a"], "b": link["b"]})
        except ApplyError as e:
            failed.append({"slug": f"{link['a']}<->{link['b']}",
                           "reason": _classify_error(e), "detail": str(e)})

    Path(args.applied_out).write_text(json.dumps(applied, indent=2))
    Path(args.failed_out).write_text(json.dumps(failed, indent=2))

    if not args.no_log_entry:
        _append_log_entry(llake_root, args.today, plan["log_entry"],
                          has_failures=bool(failed), failures=failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
