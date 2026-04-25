#!/usr/bin/env python3
"""LoreLake plugin — prompt renderer.

Composes a final agent prompt by substituting:
  - Runtime vars from CLI args (KEY=value pairs)
  - Custom slots from <config>.prompts.<template-name>.<KEY>
  - Fallback files via {{KEY|fallback:path}} markers

Usage:
  render-prompt.py <template-path> <config-json-path> [VAR=value ...]

Output: rendered prompt to stdout.
Exits nonzero if any unresolved {{VAR}} or {{VAR|fallback:...}} remains.
"""
import argparse
import json
import re
import sys
from pathlib import Path


# Match {{NAME}} OR {{NAME|fallback:path}} (path may contain slashes/dots)
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)(?:\|fallback:([^}]+))?\}\}")


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (IOError, OSError, json.JSONDecodeError):
        return {}


def template_section_name(template_path):
    """ingest.md.tmpl → 'ingest', capture.md.tmpl → 'capture'."""
    name = Path(template_path).name
    if name.endswith(".md.tmpl"):
        return name[: -len(".md.tmpl")]
    if name.endswith(".tmpl"):
        return name[: -len(".tmpl")]
    return name


def parse_runtime_vars(argv):
    out = {}
    for arg in argv:
        if "=" not in arg:
            continue
        k, v = arg.split("=", 1)
        out[k] = v
    return out


def main():
    parser = argparse.ArgumentParser(description="LoreLake prompt renderer")
    parser.add_argument("--templates-dir", default=None,
                        help="Directory to resolve relative fallback paths against")
    parser.add_argument("template", help="Path to .md.tmpl file")
    parser.add_argument("config", help="Path to config.json")
    parser.add_argument("vars", nargs="*", help="VAR=value runtime substitutions")
    args = parser.parse_args()

    try:
        template = Path(args.template).read_text()
    except (IOError, OSError) as e:
        print(f"render-prompt: cannot read template {args.template}: {e}", file=sys.stderr)
        sys.exit(2)

    config = load_json(args.config)
    section_name = template_section_name(args.template)
    custom_slots = (config.get("prompts", {}) or {}).get(section_name, {}) or {}
    runtime_vars = parse_runtime_vars(args.vars)

    unresolved = set()

    def resolve(match):
        name = match.group(1)
        fallback_path = match.group(2)

        if name in runtime_vars:
            return runtime_vars[name]

        if name in custom_slots and custom_slots[name]:
            return str(custom_slots[name])

        if fallback_path:
            resolved = Path(fallback_path)
            if not resolved.is_absolute() and args.templates_dir:
                resolved = Path(args.templates_dir) / fallback_path
            try:
                return resolved.read_text()
            except (IOError, OSError) as exc:
                print(f"render-prompt: fallback read failed ({resolved}): {exc}", file=sys.stderr)

        unresolved.add(name)
        return match.group(0)

    rendered = PLACEHOLDER_RE.sub(resolve, template)

    if unresolved:
        names = ", ".join(sorted(unresolved))
        print(f"render-prompt: unresolved placeholders: {names}", file=sys.stderr)
        sys.exit(1)

    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
