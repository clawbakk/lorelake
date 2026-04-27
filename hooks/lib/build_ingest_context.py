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
import math
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

    # Per-file numstat — added / removed line counts. `git show --numstat`
    # emits "<added>\t<removed>\t<path>" per file. Binary files emit "-\t-\t<path>";
    # we treat those as 0/0.
    numstat_out = git(repo, "show", "--numstat", "--format=", sha, "--", *include)
    by_path_lines = {}
    for ln in numstat_out.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        added_s, removed_s, path = parts[0], parts[1], parts[-1]
        added = int(added_s) if added_s.isdigit() else 0
        removed = int(removed_s) if removed_s.isdigit() else 0
        by_path_lines[path] = (added, removed)

    for entry in files:
        added, removed = by_path_lines.get(entry["path"], (0, 0))
        entry["added"] = added
        entry["removed"] = removed

    return {
        "sha": full_sha, "short": short, "author": author, "date": date,
        "subject": subject, "body": body, "files": files,
    }


# Allow `frontmatter` import when this script is run with the lib dir on PYTHONPATH
_LIB_DIR = Path(__file__).resolve().parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))
import frontmatter


def write_wiki_index(wiki_root, out_dir):
    """Walk wiki/**/*.md and dump frontmatter into wiki-index.json."""
    wiki_root = Path(wiki_root)
    if not wiki_root.exists():
        (out_dir / "wiki-index.json").write_text("{}")
        return
    catalog = {}
    for page in wiki_root.rglob("*.md"):
        slug = page.stem
        # category = first path component after wiki_root
        try:
            rel = page.relative_to(wiki_root)
        except ValueError:
            continue
        parts = rel.parts
        category = parts[0] if len(parts) > 1 else ""
        try:
            text = page.read_text()
        except (IOError, OSError):
            continue
        fm_text, _ = frontmatter.split(text)
        try:
            fm_dict = frontmatter.parse(fm_text) if fm_text else {}
        except frontmatter.FrontmatterParseError:
            fm_dict = {}
        catalog[slug] = {
            "path": str(rel),
            "category": category,
            "title": fm_dict.get("title", slug),
            "description": fm_dict.get("description", ""),
            "tags": fm_dict.get("tags", []),
            "related": fm_dict.get("related", []),
            "updated": fm_dict.get("updated", ""),
        }
    (out_dir / "wiki-index.json").write_text(json.dumps(catalog, indent=2, sort_keys=True))


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


def churn_score(stats):
    """Composite churn score for a single file.

    Weights commits-touching heavily — a file in 5 separate commits is
    almost certainly evolving. Line counts are log-dampened so a single
    monster patch doesn't dominate.

    Args:
        stats: dict with at least 'commits' (int), 'added' (int), 'removed' (int).
    Returns:
        float score; larger means higher priority for forced reading.
    """
    commits = stats.get("commits", 0)
    added = stats.get("added", 0)
    removed = stats.get("removed", 0)
    return commits * 10 + math.log1p(added + removed)


def compute_file_churn(commits):
    """Aggregate per-commit file lists into per-file churn stats.

    Args:
        commits: list of dicts as produced by commit_metadata, each with a
            'files' list. Each file may carry 'added' / 'removed' line counts
            (added by Task 5); missing keys default to 0.
    Returns:
        list of dicts: [{"path", "commits", "added", "removed", "score"}, ...]
        sorted by score descending.
    """
    by_path = {}
    for c in commits:
        for f in c.get("files", []):
            path = f["path"]
            entry = by_path.setdefault(path, {
                "path": path,
                "commits": 0,
                "added": 0,
                "removed": 0,
            })
            entry["commits"] += 1
            entry["added"] += f.get("added", 0)
            entry["removed"] += f.get("removed", 0)
    for entry in by_path.values():
        entry["score"] = churn_score(entry)
    return sorted(by_path.values(), key=lambda e: -e["score"])


def select_must_read(churn, range_commit_count, get_bytes,
                     pareto_target, max_bytes, max_bytes_per_file,
                     multi_touch_floor, max_files_override=0):
    """Select the high-priority files whose patches must be inlined into the prompt.

    Algorithm:
      - Compute dynamic max_files = max(5, range_commit_count // 4) unless
        max_files_override > 0.
      - Walk churn in score-descending order. For each file:
          forced  = stats.commits >= multi_touch_floor
          - non-forced files: stop if len(must) >= max_files OR cumulative
            score / total >= pareto_target.
          - byte cap (max_bytes) applies to all files; non-forced files break
            on cap, forced files continue (smaller forced files may still fit).
          - per-file truncation cap (max_bytes_per_file) is applied at the
            inline stage, not here; we use min(get_bytes(path), max_bytes_per_file)
            for budget accounting so a monster file doesn't blow the budget.

    Args:
        churn: list of dicts as produced by compute_file_churn (sorted by score desc).
        range_commit_count: how many commits in the input range; used for dynamic max_files.
        get_bytes: callable(path) -> int, bytes-on-disk for the file's patch.
        pareto_target: float in (0, 1]; stop adding non-forced files when cumulative
            score reaches this fraction of total.
        max_bytes: total byte budget for inlined patches.
        max_bytes_per_file: per-file truncation cap (used here for budget accounting).
        multi_touch_floor: stats.commits >= this forces inclusion past Pareto/file caps.
        max_files_override: if > 0, use this as max_files; otherwise compute dynamically.

    Returns:
        list of dicts (subset of churn) augmented with "priority": "must".
    """
    if not churn:
        return []

    if max_files_override > 0:
        max_files = max_files_override
    else:
        max_files = max(5, range_commit_count // 4)

    total_score = sum(c["score"] for c in churn) or 1.0

    must = []
    cumulative_score = 0.0
    bytes_inlined = 0

    for entry in churn:
        forced = entry["commits"] >= multi_touch_floor

        if not forced:
            if len(must) >= max_files:
                break
            if cumulative_score / total_score >= pareto_target:
                break

        patch_size = min(get_bytes(entry["path"]), max_bytes_per_file)
        if bytes_inlined + patch_size > max_bytes:
            if forced:
                continue  # try smaller forced files
            else:
                break

        out = dict(entry)
        out["priority"] = "must"
        must.append(out)
        cumulative_score += entry["score"]
        bytes_inlined += patch_size

    return must


def build_must_read_patches(must_read, diffs_dir, max_bytes_per_file):
    """Concatenate the patches for must-read files into a single string with
    headers. Truncates each per-file payload at max_bytes_per_file with a
    visible marker.

    Args:
        must_read: list of dicts as returned by select_must_read.
        diffs_dir: Path; the per-file diff directory written by write_per_file_diffs.
        max_bytes_per_file: int; per-file payload cap in bytes.
    Returns:
        str — the concatenated body. Empty string if must_read is empty.
    """
    if not must_read:
        return ""
    parts = [
        "=== HIGH-CHURN FILES (auto-attached for required coverage) ===",
        "",
        "These files changed substantially in this range. Their patches are",
        "inlined below; you do not need to Glob/Read them. Your plan MUST",
        "address every commit that touches them in commits_addressed[].",
        "",
    ]
    for f in must_read:
        path = f["path"]
        safe = _safe_name(path)
        # Single patch first; chunked patch as fallback (.001, .002, ... + .index.json)
        single = diffs_dir / f"{safe}.patch"
        chunks_index = diffs_dir / f"{safe}.index.json"
        if single.exists():
            payload = single.read_text()
        elif chunks_index.exists():
            idx = json.loads(chunks_index.read_text())
            payload = "".join((diffs_dir / name).read_text() for name in idx["chunks"])
        else:
            payload = "(no diff captured for this file)\n"
        truncated = False
        if len(payload.encode()) > max_bytes_per_file:
            payload = payload.encode()[:max_bytes_per_file].decode("utf-8", errors="ignore")
            truncated = True
        header = (f"--- {path} — {f['commits']} commits, "
                  f"+{f['added']}/-{f['removed']}, score={f['score']:.1f} ---")
        parts.append(header)
        parts.append(payload.rstrip())
        if truncated:
            parts.append(f"[... patch truncated at {max_bytes_per_file} bytes; "
                         f"full diff in context/diffs/{safe}.patch]")
        parts.append("")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--wiki-root", required=True)
    ap.add_argument("--last-sha", required=True)
    ap.add_argument("--current-sha", required=True)
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--diff-chunk-bytes", type=int, default=2000)
    ap.add_argument("--churn-pareto-target", type=float, default=0.80,
                    help="Stop adding non-forced files when cumulative score reaches this fraction of total")
    ap.add_argument("--churn-max-files", type=int, default=0,
                    help="Hard cap on must-read files (0 → dynamic max(5, commits//4))")
    ap.add_argument("--churn-max-bytes", type=int, default=150000,
                    help="Total byte budget for inlined patches (0 → no must-read patches)")
    ap.add_argument("--churn-max-bytes-per-file", type=int, default=30000,
                    help="Per-file truncation cap for inlined patches")
    ap.add_argument("--churn-multi-touch-floor", type=int, default=3,
                    help="A file in this many commits is forced past Pareto/file caps")
    args = ap.parse_args()

    repo = Path(args.project_root)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    try:
        shas = list_commits(repo, args.last_sha, args.current_sha, args.include)
        commits = [commit_metadata(repo, s, args.include) for s in shas]
    except RuntimeError as e:
        print(f"build_ingest_context: {e}", file=sys.stderr); sys.exit(2)

    files_touched = sorted({f["path"] for c in commits for f in c["files"]})
    changes = {
        "range": f"{args.last_sha}..{args.current_sha}",
        "commits": commits,
        "files_touched": files_touched,
    }
    (out_dir / "changes.json").write_text(json.dumps(changes, indent=2))

    write_per_file_diffs(repo, files_touched, args.last_sha, args.current_sha,
                         out_dir, args.diff_chunk_bytes)

    # File-churn ranking + must-read selection
    churn_ranked = compute_file_churn(commits)

    diffs_dir = out_dir / "diffs"

    def _patch_bytes(path):
        safe = _safe_name(path)
        single = diffs_dir / f"{safe}.patch"
        chunks_index = diffs_dir / f"{safe}.index.json"
        if single.exists():
            return single.stat().st_size
        if chunks_index.exists():
            idx = json.loads(chunks_index.read_text())
            return sum((diffs_dir / name).stat().st_size for name in idx["chunks"])
        return 0

    must_read = select_must_read(
        churn_ranked,
        range_commit_count=len(commits),
        get_bytes=_patch_bytes,
        pareto_target=args.churn_pareto_target,
        max_bytes=args.churn_max_bytes,
        max_bytes_per_file=args.churn_max_bytes_per_file,
        multi_touch_floor=args.churn_multi_touch_floor,
        max_files_override=args.churn_max_files,
    )

    must_read_paths = {f["path"] for f in must_read}
    files_out = []
    for entry in churn_ranked:
        copy = dict(entry)
        copy["priority"] = "must" if entry["path"] in must_read_paths else "should"
        files_out.append(copy)

    (out_dir / "file_churn.json").write_text(json.dumps({
        "files": files_out,
        "summary": {
            "total_files": len(files_out),
            "must_count": len(must_read),
            "total_score": sum(f["score"] for f in files_out),
        },
    }, indent=2))

    patches_body = build_must_read_patches(must_read, diffs_dir, args.churn_max_bytes_per_file)
    (out_dir / "must-read-patches.txt").write_text(patches_body)

    write_wiki_index(args.wiki_root, out_dir)


if __name__ == "__main__":
    main()
