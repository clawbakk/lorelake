#!/usr/bin/env python3
"""LoreLake plugin — ingest batching gate.

Decides whether a post-merge should spawn an ingest agent now, defer until more
has accumulated, or skip because nothing relevant changed.

The pile is measured as a RANGE-level diff (last..current), not a sum of
per-commit diffs. That makes it the net change: a function rewritten five times
counts once, and code added then reverted counts as zero.

Pure stdlib. See docs/superpowers/specs/2026-09-20-ingest-batching-gate-design.md
"""
import subprocess


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
