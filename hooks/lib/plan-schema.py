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
import sys
from pathlib import Path

REQUIRED_TOP_LEVEL = ["version", "skip_reason", "summary", "updates",
                      "creates", "deletes", "bidirectional_links", "log_entry"]


def validate(plan):
    """Return list of error strings; empty list = valid."""
    errors = []
    if not isinstance(plan, dict):
        return ["plan: expected JSON object at top level"]
    for key in REQUIRED_TOP_LEVEL:
        if key not in plan:
            errors.append(f"plan: missing required key '{key}'")
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
