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


def plan_run(repo, llake_root, include, base, head):
    if not watched_changes(repo, base, head, include):
        return RunPlan("gap-only" if gaps.owed_major(gaps.load(llake_root)) else "empty", head)
    f = active_failures(llake_root, base)
    if int(f.get("count", 0)) >= SPLIT_AFTER_FAILURES:
        target = f.get("lastHead") or head
        try:
            chain = first_parent_chain(repo, base, target)
        except RuntimeError:
            target, chain = head, first_parent_chain(repo, base, head)
        if not chain:
            return RunPlan("range", head)
        if len(chain) == 1:
            return RunPlan("skip", target)
        return RunPlan("split", chain[len(chain) // 2])
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
