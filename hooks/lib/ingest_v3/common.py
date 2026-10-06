"""Shared helpers for ingest v3: atomic I/O, whitespace normalisation, git, fixed constants.

The constants are choices the benchmark settled (spec §13 "Fixed in code"); they are not knobs.
"""
import json
import os
import subprocess

HIT_INDEX_CAP = 20          # hit-index line numbers shown per page
PATCH_MAX_BYTES = 60000     # per-file patches above this are split at hunk boundaries
SPLIT_AFTER_FAILURES = 2    # analysis work failures on one base before the range is halved
STUCK_ATTEMPTS = 3          # dispatched failures before a gap is stuck
BROKEN_ANCHOR_CAP = 25      # broken-anchor leads per page in a writer's bundle part

# The plugin's own manifest, four levels up from this file (hooks/lib/ingest_v3/common.py).
PLUGIN_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    ".claude-plugin", "plugin.json")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def plugin_version():
    """The running plugin's version from its manifest, or "unknown" when the file or field is missing."""
    meta = load_json(PLUGIN_MANIFEST, None)
    version = meta.get("version") if isinstance(meta, dict) else None
    return str(version) if version else "unknown"


def dump_json(path, obj):
    write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def read_text(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    except OSError:
        return default


def write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def norm_ws(text):
    return " ".join(str(text).split())


def git(repo, *args, check=True):
    res = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                         encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise RuntimeError("git {}: {}".format(" ".join(args[:2]), res.stderr.strip()))
    return res.stdout
