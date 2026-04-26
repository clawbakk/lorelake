#!/usr/bin/env python3
"""LoreLake ingest v2 plan validator.

Validates plan.json against the ingest v2 schema. Run as CLI or import.

CLI:
    python3 plan_schema.py <plan-path>
    Exit 0 if valid; nonzero with diagnostics on stderr if invalid.

Import:
    import plan_schema
    errors = plan_schema.validate(plan_dict)  # returns list[str]; empty if valid.
"""
import json
import re
import sys
from pathlib import Path

REQUIRED_TOP_LEVEL = ["version", "skip_reason", "summary", "updates",
                      "creates", "deletes", "bidirectional_links",
                      "commits_addressed", "commits_skipped", "log_entry"]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
ALLOWED_OPS = {"replace", "append_section", "frontmatter_set",
               "frontmatter_add_related", "body_replace"}


def _check_slug(slug, where, errors):
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        errors.append(f"{where}: invalid slug {slug!r} (must match {SLUG_RE.pattern})")


def _check_update(idx, upd, errors):
    base = f"updates[{idx}]"
    if not isinstance(upd, dict):
        errors.append(f"{base}: expected object")
        return
    _check_slug(upd.get("slug"), f"{base}.slug", errors)
    if "ops" not in upd or not isinstance(upd["ops"], list):
        errors.append(f"{base}.ops: required list (slug={upd.get('slug')!r})")
        return
    ops = upd["ops"]
    op_types = []
    for j, op in enumerate(ops):
        if not isinstance(op, dict) or "op" not in op:
            errors.append(f"{base}.ops[{j}]: missing 'op' key")
            continue
        if op["op"] not in ALLOWED_OPS:
            errors.append(f"{base}.ops[{j}]: unknown op {op['op']!r}; allowed: {sorted(ALLOWED_OPS)}")
        op_types.append(op["op"])
    if "body_replace" in op_types and "replace" in op_types:
        errors.append(f"{base}: body_replace and replace are mutually exclusive in the same update")


def _check_create(idx, c, errors):
    base = f"creates[{idx}]"
    if not isinstance(c, dict):
        errors.append(f"{base}: expected object")
        return
    _check_slug(c.get("slug"), f"{base}.slug", errors)
    if "category" not in c or not isinstance(c["category"], str):
        errors.append(f"{base}.category: required string")
    if "front_matter" not in c or not isinstance(c["front_matter"], dict):
        errors.append(f"{base}.front_matter: required object")
    if "body" not in c or not isinstance(c["body"], str):
        errors.append(f"{base}.body: required string")


def _check_delete(idx, d, errors):
    base = f"deletes[{idx}]"
    if not isinstance(d, dict):
        errors.append(f"{base}: expected object")
        return
    _check_slug(d.get("slug"), f"{base}.slug", errors)


def _check_bidir_link(idx, link, errors):
    base = f"bidirectional_links[{idx}]"
    if not isinstance(link, dict):
        errors.append(f"{base}: expected object")
        return
    if "a" not in link:
        errors.append(f"{base}: missing key 'a'")
    else:
        _check_slug(link["a"], f"{base}.a", errors)
    if "b" not in link:
        errors.append(f"{base}: missing key 'b'")
    else:
        _check_slug(link["b"], f"{base}.b", errors)
    if "a" in link and "b" in link and link["a"] == link["b"]:
        errors.append(f"{base}: self-loop ({link['a']!r} == {link['b']!r})")


def _check_commit_addressed(idx, entry, known_slugs, errors):
    base = f"commits_addressed[{idx}]"
    if not isinstance(entry, dict):
        errors.append(f"{base}: expected object")
        return None
    sha = entry.get("sha")
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        errors.append(f"{base}.sha: invalid SHA {sha!r} (must match {SHA_RE.pattern})")
    if "pages" not in entry or not isinstance(entry["pages"], list):
        errors.append(f"{base}.pages: required list (sha={sha!r})")
        return sha
    for j, slug in enumerate(entry["pages"]):
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            errors.append(f"{base}.pages[{j}]: invalid slug {slug!r}")
        elif slug not in known_slugs:
            errors.append(f"{base}.pages[{j}]: slug {slug!r} not in updates/creates/deletes "
                          f"(every page mentioned must appear in one of those buckets)")
    return sha


def _check_commit_skipped(idx, entry, errors):
    base = f"commits_skipped[{idx}]"
    if not isinstance(entry, dict):
        errors.append(f"{base}: expected object")
        return None
    sha = entry.get("sha")
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        errors.append(f"{base}.sha: invalid SHA {sha!r} (must match {SHA_RE.pattern})")
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append(f"{base}.reason: required non-empty string (sha={sha!r})")
    return sha


def validate(plan):
    """Return list of error strings; empty list = valid."""
    errors = []
    if not isinstance(plan, dict):
        return ["plan: expected JSON object at top level"]
    for key in REQUIRED_TOP_LEVEL:
        if key not in plan:
            errors.append(f"plan: missing required key '{key}'")
    if errors:
        return errors

    for i, u in enumerate(plan["updates"]):
        _check_update(i, u, errors)
    for i, c in enumerate(plan["creates"]):
        _check_create(i, c, errors)
    for i, d in enumerate(plan["deletes"]):
        _check_delete(i, d, errors)

    # Slug uniqueness across buckets
    update_slugs = [u.get("slug") for u in plan["updates"] if isinstance(u, dict)]
    create_slugs = [c.get("slug") for c in plan["creates"] if isinstance(c, dict)]
    delete_slugs = [d.get("slug") for d in plan["deletes"] if isinstance(d, dict)]
    seen = {}
    for bucket, slugs in [("updates", update_slugs), ("creates", create_slugs), ("deletes", delete_slugs)]:
        for s in slugs:
            if s in seen:
                errors.append(f"slug {s!r} appears in both {seen[s]} and {bucket} (must appear in at most one)")
            else:
                seen[s] = bucket

    # commits_addressed / commits_skipped
    known_page_slugs = set(update_slugs) | set(create_slugs) | set(delete_slugs)
    addressed_shas = []
    for i, entry in enumerate(plan["commits_addressed"]):
        sha = _check_commit_addressed(i, entry, known_page_slugs, errors)
        if sha:
            addressed_shas.append(sha)
    skipped_shas = []
    for i, entry in enumerate(plan["commits_skipped"]):
        sha = _check_commit_skipped(i, entry, errors)
        if sha:
            skipped_shas.append(sha)
    addressed_set = set(addressed_shas)
    skipped_set = set(skipped_shas)
    overlap = addressed_set & skipped_set
    for sha in sorted(overlap):
        errors.append(f"commit {sha!r} appears in both commits_addressed and "
                      f"commits_skipped (overlap not allowed)")

    # bidirectional_links: structural check only. Links where one side is in deletes[]
    # are silently skipped by the CLI (spec line 289) — not a schema error here.
    # Existence-against-wiki is the applier's job.
    for i, link in enumerate(plan["bidirectional_links"]):
        _check_bidir_link(i, link, errors)

    # log_entry sub-fields and pages_affected consistency
    le = plan["log_entry"]
    if not isinstance(le, dict):
        errors.append("log_entry: expected object")
    else:
        for required in ("operation", "commit_range", "summary"):
            if required not in le:
                errors.append(f"log_entry.{required}: required key missing")
            elif not isinstance(le[required], str):
                errors.append(f"log_entry.{required}: must be string")
        actual = set(update_slugs) | set(create_slugs) | set(delete_slugs)
        claimed = set(le.get("pages_affected") or [])
        if actual != claimed:
            missing = actual - claimed
            extra = claimed - actual
            msg = "log_entry.pages_affected mismatch:"
            if missing:
                msg += f" missing {sorted(missing)};"
            if extra:
                msg += f" extra {sorted(extra)};"
            errors.append(msg)

    return errors


def main():
    if len(sys.argv) != 2:
        print("usage: plan_schema.py <plan-path>", file=sys.stderr)
        sys.exit(2)
    try:
        plan = json.loads(Path(sys.argv[1]).read_text())
    except (IOError, OSError, json.JSONDecodeError) as e:
        print(f"plan_schema: cannot parse {sys.argv[1]}: {e}", file=sys.stderr)
        sys.exit(2)
    errors = validate(plan)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
