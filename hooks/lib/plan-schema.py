#!/usr/bin/env python3
"""LoreLake ingest v2 plan validator.

Validates plan.json against the ingest v2 schema. Run as CLI or import.

CLI:
    python3 plan-schema.py <plan-path>
    Exit 0 if valid; nonzero with diagnostics on stderr if invalid.

Import:
    from importlib import import_module; mod = import_module('plan-schema')
    errors = mod.validate(plan_dict)  # returns list[str]; empty if valid.
"""
import json
import re
import sys
from pathlib import Path

REQUIRED_TOP_LEVEL = ["version", "skip_reason", "summary", "updates",
                      "creates", "deletes", "bidirectional_links", "log_entry"]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
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
    ops = upd.get("ops") or []
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

    # bidirectional_links: structural check only. Links where one side is in deletes[]
    # are silently skipped by the CLI (spec line 289) — not a schema error here.
    # Existence-against-wiki is the applier's job.
    for i, link in enumerate(plan["bidirectional_links"]):
        if not isinstance(link, dict):
            errors.append(f"bidirectional_links[{i}]: expected object")
            continue

    # log_entry.pages_affected must equal the union of touched slugs
    le = plan["log_entry"]
    if not isinstance(le, dict):
        errors.append("log_entry: expected object")
    else:
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
        print("usage: plan-schema.py <plan-path>", file=sys.stderr)
        sys.exit(2)
    try:
        plan = json.loads(Path(sys.argv[1]).read_text())
    except (IOError, OSError, json.JSONDecodeError) as e:
        print(f"plan-schema: cannot parse {sys.argv[1]}: {e}", file=sys.stderr)
        sys.exit(2)
    errors = validate(plan)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
