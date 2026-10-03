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


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


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
