#!/usr/bin/env python3
"""LoreLake plugin — ingest batching gate.

Decides whether a post-merge should spawn an ingest agent now, defer until more
has accumulated, or skip because nothing relevant changed.

The pile is measured as a RANGE-level diff (last..current), not a sum of
per-commit diffs. That makes it the net change: a function rewritten five times
counts once, and code added then reverted counts as zero.

Pure stdlib. See docs/superpowers/specs/2026-09-20-ingest-batching-gate-design.md
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

TIMESTAMP_FILENAME = "last-ingest-at"


def git_numstat(repo, last_sha, current_sha, include):
    """Raw `git diff --numstat last..current -- <include>` stdout.

    Raises RuntimeError on any git failure so the caller can fail open.
    """
    args = ["git", "-C", str(repo), "diff", "--numstat",
            "{}..{}".format(last_sha, current_sha), "--"] + list(include)
    res = subprocess.run(args, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("git diff --numstat: {}".format(res.stderr.strip()))
    return res.stdout


def summarize(numstat_out):
    """Return (files, lines) for a --numstat blob.

    Binary files print '-' in both numeric columns. They increment the file
    count but contribute no lines, so a binary-only change is never mistaken
    for an empty pile.
    """
    files = 0
    lines = 0
    for row in numstat_out.splitlines():
        if not row.strip():
            continue
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        files += 1
        if added == "-" or deleted == "-":
            continue
        lines += int(added) + int(deleted)
    return files, lines


def age_hours(state_dir):
    """Hours since the last ingest, or None when the timestamp is unusable.

    None is a legitimate answer (fresh install, cleared .state/) and maps to
    RUN reason=no-timestamp — a new install should ingest promptly rather than
    wait out a phantom window.
    """
    path = Path(state_dir) / TIMESTAMP_FILENAME
    try:
        raw = path.read_text().strip()
    except (IOError, OSError):
        return None
    try:
        then = float(raw)
    except ValueError:
        return None
    return max(0.0, (time.time() - then) / 3600.0)


def _fields(files, lines, age_h):
    shown = "none" if age_h is None else "{:.1f}".format(age_h)
    return "lines={} files={} age_h={}".format(lines, files, shown)


def decide(files, lines, age_h, enabled, forced, min_lines, max_age_h):
    """Return (verdict, detail). See the decision table in the plan/spec."""
    base = _fields(files, lines, age_h)
    if files == 0:
        return "EMPTY", base
    if forced:
        return "RUN", base + " reason=forced"
    if not enabled:
        return "RUN", base + " reason=schedule-disabled"
    if age_h is None:
        return "RUN", base + " reason=no-timestamp"
    if lines >= min_lines:
        return "RUN", base + " reason=lines"
    if age_h >= max_age_h:
        return "RUN", base + " reason=age"
    return "WAIT", base + " need_lines={} need_age_h={}".format(
        min_lines, _trim(max_age_h))


def _trim(value):
    """Render 24.0 as '24' so log lines read naturally."""
    if float(value) == int(float(value)):
        return str(int(float(value)))
    return str(value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--last-sha", required=True)
    ap.add_argument("--current-sha", required=True)
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--schedule-enabled", default="true")
    ap.add_argument("--min-changed-lines", type=int, default=1500)
    ap.add_argument("--max-age-hours", type=float, default=24.0)
    args = ap.parse_args()

    enabled = args.schedule_enabled.strip().lower() != "false"
    forced = os.environ.get("LLAKE_IGNORE_SCHEDULE") == "1"

    try:
        out = git_numstat(args.project_root, args.last_sha,
                          args.current_sha, args.include)
    except RuntimeError as e:
        print("ingest_gate: {}".format(e), file=sys.stderr)
        sys.exit(2)

    files, lines = summarize(out)
    verdict, detail = decide(files, lines, age_hours(args.state_dir),
                             enabled, forced,
                             args.min_changed_lines, args.max_age_hours)
    print("{} {}".format(verdict, detail))


if __name__ == "__main__":
    main()
