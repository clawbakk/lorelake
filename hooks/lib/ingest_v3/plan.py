"""Run planning (spec §3): run kinds, the analysis failure counter, range split and skip, dead-run recovery.

Failure counter, .state/ingest-failures.json: {base, count, lastHead, plugin}. It applies only while its base
and plugin version match the current ones. Work failures increment it and record the head that failed;
infra failures change nothing; a pipeline failure (an invalid brief: deterministic, fails the same on any
range) clears it. With count >= 2 the next run splits base..lastHead at its churn midpoint (split_point); a
range whose watched changes sit in one first-parent commit cannot shrink and is skipped.
"""
import collections
import os
import shutil

from . import gaps
from .common import SPLIT_AFTER_FAILURES, dump_json, git, load_json, plugin_version

FAILURES_REL = os.path.join(".state", "ingest-failures.json")
RunPlan = collections.namedtuple("RunPlan", "kind head")


def _failures_path(llake_root):
    return os.path.join(llake_root, FAILURES_REL)


def load_failures(llake_root):
    f = load_json(_failures_path(llake_root), None)
    return f if isinstance(f, dict) else {}


def active_failures(llake_root, base):
    """The counter, only while it belongs to this base and this plugin version; else {}."""
    f = load_failures(llake_root)
    if f.get("base") == base and f.get("plugin") == plugin_version():
        return f
    return {}


def record_failure(llake_root, base, head, cls):
    if cls == "pipeline":
        clear_failures(llake_root)
        return {}
    if cls != "work":
        return load_failures(llake_root)
    prev = active_failures(llake_root, base)
    f = {"base": base, "count": int(prev.get("count", 0)) + 1, "lastHead": head, "plugin": plugin_version()}
    dump_json(_failures_path(llake_root), f)
    return f


def clear_failures(llake_root):
    try:
        os.remove(_failures_path(llake_root))
    except OSError:
        pass


def first_parent_chain(repo, base, head):
    return git(repo, "rev-list", "--first-parent", "{}..{}".format(base, head)).split()


def watched_changes(repo, base, head, include):
    if base == head:
        return False
    return bool(git(repo, "diff", "--name-only", base, head, "--", *include).strip())


def commit_churn(repo, sha, include):
    """Watched churn of one commit against its first parent: added + deleted lines, at least 1 per file (a pure
    rename or a binary file counts 1). For a merge commit this is the merged branch's content."""
    out = git(repo, "diff", "--numstat", sha + "^1", sha, "--", *include)
    total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        n = int(added) + int(deleted) if added.isdigit() and deleted.isdigit() else 0
        total += max(n, 1)
    return total


def split_point(repo, base, target, include):
    """The split head for base..target (spec §3): the watched first-parent commit whose cumulative churn is
    closest to half the total, ties to the earlier. Never the last watched commit (the remainder keeps watched
    content) and never a commit where base..commit nets to no watched change. None when the range cannot
    shrink: at most one first-parent commit carries watched changes."""
    commits = list(reversed(first_parent_chain(repo, base, target)))
    watched = [(c, w) for c, w in ((c, commit_churn(repo, c, include)) for c in commits) if w > 0]
    if len(watched) < 2:
        return None
    total = sum(w for _, w in watched)
    best, best_d, cum = None, None, 0
    for c, w in watched[:-1]:
        cum += w
        if not watched_changes(repo, base, c, include):
            continue
        d = abs(2 * cum - total)
        if best is None or d < best_d:
            best, best_d = c, d
    return best


def plan_run(repo, llake_root, include, base, head):
    if not watched_changes(repo, base, head, include):
        return RunPlan("gap-only" if gaps.owed_major(gaps.load(llake_root)) else "empty", head)
    f = active_failures(llake_root, base)
    if int(f.get("count", 0)) < SPLIT_AFTER_FAILURES:
        return RunPlan("range", head)
    target = f.get("lastHead") or head
    try:
        point = split_point(repo, base, target, include)
    except RuntimeError:  # lastHead no longer resolves (rewritten history): work from the trigger head
        target, point = head, split_point(repo, base, head, include)
    if point is not None:
        return RunPlan("split", point)
    if watched_changes(repo, base, target, include):
        return RunPlan("skip", target)
    return RunPlan("range", head)


def recover_dead_runs(llake_root, exclude_dir=None):
    """Restore the in-flight pages of earlier runs that died mid-write (spec §3 "Run journal")."""
    agents = os.path.join(llake_root, ".state", "agents")
    recovered = []
    for aid in sorted(os.listdir(agents)) if os.path.isdir(agents) else []:
        run_dir = os.path.join(agents, aid)
        if exclude_dir and os.path.abspath(run_dir) == os.path.abspath(str(exclude_dir)):
            continue
        jp = os.path.join(run_dir, "run.json")
        j = load_json(jp, None)
        if not isinstance(j, dict) or j.get("finalized") or j.get("aborted") or not j.get("inFlight"):
            continue
        remaining = {}
        for page, snap in sorted(j["inFlight"].items()):
            if not str(page).startswith("wiki/") or ".." in str(page).split("/"):
                continue
            manifest = load_json(os.path.join(snap, "manifest.json"), None)
            existed = manifest.get(page) if isinstance(manifest, dict) else None
            dst = os.path.join(llake_root, page)
            copy = os.path.join(snap, page)
            if existed is True and os.path.exists(copy):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(copy, dst)
            elif existed is False:  # only pages the dead run created are ever deleted
                if os.path.exists(dst):
                    os.remove(dst)
            else:  # manifest missing/silent, or the snapshot copy is gone: cannot restore safely
                remaining[page] = snap
        j["inFlight"] = remaining
        if remaining:
            j["unrecovered"] = sorted(remaining)
            dump_json(jp, j)
            continue
        j.pop("unrecovered", None)
        j["recovered"] = True
        dump_json(jp, j)
        recovered.append(aid)
    return recovered
