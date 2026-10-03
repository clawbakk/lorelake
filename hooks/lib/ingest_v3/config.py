"""ingest.v3 config: the project's llake/config.json over the plugin's templates/config.default.json.

No default lives here (CLAUDE.md: config fallback contract). A key missing from both files is a
bug and raises KeyError, which holds the run.
"""
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULTS_PATH = PLUGIN_ROOT / "templates" / "config.default.json"


def _load(path):
    try:
        with open(str(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _get(node, dotted):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


class V3Config:
    def __init__(self, config_path, defaults_path=DEFAULTS_PATH):
        self.user = _load(config_path)
        self.defaults = _load(defaults_path)

    def lookup(self, dotted):
        value, found = _get(self.user, dotted)
        if not found:
            value, found = _get(self.defaults, dotted)
        if not found:
            raise KeyError("no config key {!r} in the project config or the plugin defaults".format(dotted))
        return value

    def get(self, key):
        return self.lookup("ingest.v3." + key)

    def include(self):
        value = self.lookup("ingest.include")
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError("ingest.include must be a list of paths")
        return list(value)

    def effective(self):
        merged = {}
        for source in (self.defaults, self.user):
            block, found = _get(source, "ingest.v3")
            if found and isinstance(block, dict):
                merged.update(block)
        return {k: v for k, v in merged.items() if not k.startswith("_")}
