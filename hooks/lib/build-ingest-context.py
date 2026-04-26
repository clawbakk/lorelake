#!/usr/bin/env python3
"""LoreLake ingest v2 — Stage 1: structured context builder.

Reads the commit range, applies ingest.include filtering, and writes:
  - <out-dir>/changes.json    (per-commit metadata + union of files)
  - <out-dir>/diffs/*.patch   (per-file diff chunks; later task)
  - <out-dir>/wiki-index.json (catalog of every wiki page; later task)

Pure stdlib. Exits nonzero on git/IO error.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def git(repo, *args):
    res = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def list_commits(repo, last, current, include):
    """Return shas (oldest first) in last..current."""
    args = ["log", "--reverse", "--format=%H", f"{last}..{current}", "--"] + list(include)
    out = git(repo, *args)
    return [s for s in out.splitlines() if s]


def commit_metadata(repo, sha, include):
    raw = git(repo, "show", "-s", "--format=%H%n%h%n%an%n%aI%n%s%n%b---END---", sha)
    lines = raw.split("\n")
    full_sha, short, author, date, subject = lines[0], lines[1], lines[2], lines[3], lines[4]
    body_lines = []
    for ln in lines[5:]:
        if ln == "---END---":
            break
        body_lines.append(ln)
    body = "\n".join(body_lines).rstrip()
    # Per-file status with --name-status, filtered by include paths.
    status_out = git(repo, "show", "--name-status", "--format=", sha, "--", *include)
    files = []
    for ln in status_out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t")
        status, path = parts[0], parts[-1]
        files.append({"path": path, "status": status[0]})
    return {
        "sha": full_sha, "short": short, "author": author, "date": date,
        "subject": subject, "body": body, "files": files,
    }


def _safe_name(path):
    return path.replace("/", "__")


def _split_unified_diff_into_hunks(diff_text):
    """Return [header, hunk_1, hunk_2, ...] where header is the file header
    (lines through the last `+++` or `---`), and each hunk starts with `@@`."""
    lines = diff_text.splitlines(keepends=True)
    header = []
    rest = []
    found_first_hunk = False
    for ln in lines:
        if not found_first_hunk and ln.startswith("@@"):
            found_first_hunk = True
            rest.append(ln)
        elif not found_first_hunk:
            header.append(ln)
        else:
            rest.append(ln)
    if not rest:
        return ["".join(header)] if header else []
    hunks = []
    cur = []
    for ln in rest:
        if ln.startswith("@@") and cur:
            hunks.append("".join(cur)); cur = [ln]
        else:
            cur.append(ln)
    if cur:
        hunks.append("".join(cur))
    return ["".join(header)] + hunks


def write_per_file_diffs(repo, files, last, current, out_dir, chunk_bytes):
    diffs_dir = out_dir / "diffs"; diffs_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        diff = git(repo, "diff", f"{last}..{current}", "--", path)
        if not diff:
            continue
        safe = _safe_name(path)
        if len(diff.encode()) <= chunk_bytes:
            (diffs_dir / f"{safe}.patch").write_text(diff)
            continue
        parts = _split_unified_diff_into_hunks(diff)
        if len(parts) <= 1:
            # No hunks (or just header) — emit as one
            (diffs_dir / f"{safe}.patch").write_text(diff)
            continue
        header, hunks = parts[0], parts[1:]
        chunks = []
        cur = header; cur_hunks = []
        for h in hunks:
            tentative = cur + h
            if len(tentative.encode()) > chunk_bytes and cur_hunks:
                chunks.append(cur); cur = header + h; cur_hunks = [h]
            else:
                cur = tentative; cur_hunks.append(h)
        if cur_hunks:
            chunks.append(cur)
        names = []
        for i, ch in enumerate(chunks, start=1):
            name = f"{safe}.{i:03d}.patch"
            (diffs_dir / name).write_text(ch)
            names.append(name)
        (diffs_dir / f"{safe}.index.json").write_text(
            json.dumps({"file": path, "chunks": names}, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--wiki-root", required=True)
    ap.add_argument("--last-sha", required=True)
    ap.add_argument("--current-sha", required=True)
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--diff-chunk-bytes", type=int, default=2000)
    args = ap.parse_args()

    repo = Path(args.project_root)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    try:
        shas = list_commits(repo, args.last_sha, args.current_sha, args.include)
        commits = [commit_metadata(repo, s, args.include) for s in shas]
    except RuntimeError as e:
        print(f"build-ingest-context: {e}", file=sys.stderr); sys.exit(2)

    files_touched = sorted({f["path"] for c in commits for f in c["files"]})
    changes = {
        "range": f"{args.last_sha}..{args.current_sha}",
        "commits": commits,
        "files_touched": files_touched,
    }
    (out_dir / "changes.json").write_text(json.dumps(changes, indent=2))

    write_per_file_diffs(repo, files_touched, args.last_sha, args.current_sha,
                         out_dir, args.diff_chunk_bytes)


if __name__ == "__main__":
    main()
