#!/usr/bin/env python3
"""LoreLake plugin — config reader.

Reads a value from the project's config.json at a dot-key path. Falls back to
the plugin's templates/config.default.json when the key is missing or the user
config doesn't exist.

Usage:
  read-config.py <user-config-path> <dot.key.path>

Output: the value as a string. Booleans → "true"/"false". null → "".
Arrays/objects → JSON-encoded string.
"""
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent.parent
DEFAULTS_PATH = PLUGIN_ROOT / "templates" / "config.default.json"


def load(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (IOError, OSError, json.JSONDecodeError):
        return {}


def get_nested(node, key_path):
    parts = key_path.split(".")
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def format_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def main():
    if len(sys.argv) < 3:
        print("", end="")
        sys.exit(0)

    user_config_path = sys.argv[1]
    key_path = sys.argv[2]

    user = load(user_config_path)
    defaults = load(DEFAULTS_PATH)

    value, found = get_nested(user, key_path)
    if not found:
        value, found = get_nested(defaults, key_path)
    if not found:
        print("")
        sys.exit(0)

    print(format_value(value))


if __name__ == "__main__":
    main()
