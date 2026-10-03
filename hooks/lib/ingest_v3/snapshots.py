"""Snapshots, the pre-run copy, write attribution and the kill revert (spec §9, §12 row 9; design §5-§6).

Only paths under llake/ are ever restored or deleted. A v3 write outside llake/ is reported and never
touched: pre/ holds only llake/, and plugin code never writes outside <project>/llake/.
"""
import difflib
import hashlib
import os
import shutil

from .common import dump_json, load_json, read_text, write_text
from .state import RunState

PRE = "pre"
FINALIZE_SNAP = "finalize-snapshot"


def _manifest(llake_root):
    out = {}
    for root, dirs, files in os.walk(llake_root):
        rel_root = os.path.relpath(root, llake_root)
        if rel_root.split(os.sep)[0] == ".state":
            dirs[:] = []
            continue
        for f in files:
            path = os.path.join(root, f)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, llake_root).replace(os.sep, "/")] = hashlib.sha1(fh.read()).hexdigest()
    return out


def take_pre(state):
    pre = os.path.join(state.dir, PRE)
    if os.path.exists(pre):
        shutil.rmtree(pre)
    top = os.path.abspath(state.llake)
    shutil.copytree(state.llake, pre,
                    ignore=lambda d, names: [".state"] if os.path.abspath(d) == top else [])
    # Hash the copy, not the live tree: a file created after copytree must not look like pre-run state.
    dump_json(os.path.join(state.dir, "pre-manifest.json"), _manifest(pre))


def current_manifest(state):
    return _manifest(state.llake)


def _restore_llake(state, rel, source):
    """Restore llake/<rel> from `source`, or delete it when source is None (explicit evidence of absence).

    A source that is given but missing is not evidence of absence: the file is left alone and False returned.
    Refuses anything outside llake/."""
    root = os.path.abspath(state.llake)
    target = os.path.abspath(os.path.join(root, rel))
    if not target.startswith(root + os.sep):
        raise ValueError("refusing to touch {} outside llake/".format(target))
    if source is not None:
        if not os.path.exists(source):
            return False
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    elif os.path.exists(target):
        os.remove(target)
    return True


def mark_owned(state, pages):
    owned = state.journal.setdefault("owned", [])
    for p in pages:
        if p not in owned:
            owned.append(p)


def snapshot(state, pages, dest):
    # Merge into an existing manifest, so two snapshots into one dir never forget a page.
    manifest = load_json(os.path.join(dest, "manifest.json"), {}) or {}
    for p in pages:
        src = state.abs(p)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(dest, p)), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, p))
            manifest[p] = True
        else:
            manifest[p] = False
    dump_json(os.path.join(dest, "manifest.json"), manifest)
    for p in pages:
        state.journal["inFlight"][p] = dest
    mark_owned(state, pages)
    state.save()
    return manifest


def restore_page(state, page, snap_dir):
    """Three-state restore: "restored", "deleted" (manifest value exactly False) or "unrestored" (left alone)."""
    manifest = load_json(os.path.join(snap_dir, "manifest.json"), None)
    existed = manifest.get(page) if isinstance(manifest, dict) else None
    if existed is True:
        ok = _restore_llake(state, page, os.path.join(snap_dir, page))
        return "restored" if ok else "unrestored"
    if existed is False:
        _restore_llake(state, page, None)
        return "deleted"
    return "unrestored"


def revert(state, pages, dest):
    """Restore each page from its snapshot; returns the pages that could not be restored (kept in inFlight)."""
    unrestored = []
    for p in pages:
        if restore_page(state, p, dest) == "unrestored":
            unrestored.append(p)
        else:
            state.journal["inFlight"].pop(p, None)
    state.save()
    return unrestored


def settle(state, pages):
    """Keep the edits; remember which pages the run really changed (revert_run restores only those) and the
    snapshot taken before the run first changed each one (`changedFrom`): that snapshot, not pre/, is the
    page's revert point, so a concurrent writer's edit that landed before it survives a kill. A later settle
    (a fixer's) never moves it."""
    changed = state.journal.setdefault("changed", [])
    changed_from = state.journal.setdefault("changedFrom", {})
    for p in pages:
        dest = state.journal["inFlight"].pop(p, None)
        if dest is not None and p not in changed and changed_since(state, p, dest):
            changed.append(p)
            changed_from.setdefault(p, dest)
    state.save()


def snapshot_finalize_write(state, rel):
    """Copy llake/<rel> as it stands just before finalize first writes it (manifest False when absent), so
    the kill revert restores that state rather than pre/: a row a concurrent writer added to an index during
    the run survives. Only the first copy of a file counts. Not a page snapshot: no inFlight, no owned."""
    dest = os.path.join(state.dir, FINALIZE_SNAP)
    manifest = load_json(os.path.join(dest, "manifest.json"), {}) or {}
    if rel in manifest:
        return
    src = state.abs(rel)
    if os.path.exists(src):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copy2(src, os.path.join(dest, rel))
        manifest[rel] = True
    else:
        manifest[rel] = False
    dump_json(os.path.join(dest, "manifest.json"), manifest)


def changed_since(state, page, dest):
    snap, cur = os.path.join(dest, page), state.abs(page)
    if not os.path.exists(snap):
        return os.path.exists(cur)
    return not os.path.exists(cur) or read_text(snap) != read_text(cur)


def page_diff(state, page, dest):
    before = read_text(os.path.join(dest, page)).splitlines()
    after = read_text(state.abs(page)).splitlines()
    return "\n".join(difflib.unified_diff(before, after, "a/" + page, "b/" + page, n=1, lineterm=""))


def out_of_surface(state, summary, surface_pages, allowed_dirs=()):
    allowed = {os.path.abspath(state.abs(p)) for p in surface_pages}
    allowed |= {os.path.abspath(state.abs(p)) for p in state.journal.get("owned") or []}
    denied = set()
    for d in summary.get("permission_denials") or []:
        fp = str(((d or {}).get("tool_input") or {}).get("file_path") or "") if isinstance(d, dict) else ""
        if fp:
            denied.add(os.path.abspath(fp if os.path.isabs(fp) else os.path.join(state.project, fp)))
    dirs = [os.path.abspath(d) + os.sep for d in allowed_dirs]
    llake = os.path.abspath(state.llake) + os.sep
    inside, outside = [], []
    for w in summary.get("writes") or []:
        path = str(w.get("file_path", ""))
        if not path:
            continue
        path = os.path.abspath(path if os.path.isabs(path) else os.path.join(state.project, path))
        if path in allowed or path in denied or any(path.startswith(d) for d in dirs):
            continue
        if path.startswith(llake):
            rel = path[len(llake):].replace(os.sep, "/")
            if rel not in inside:
                inside.append(rel)
        elif path not in outside:
            outside.append(path)
    return inside, outside


def revert_out_of_surface(state, stage, inside, outside):
    pre = os.path.join(state.dir, PRE)
    before = load_json(os.path.join(state.dir, "pre-manifest.json"), None)
    now = _manifest(state.llake)
    actions = []
    for rel in inside:
        path = "llake/" + rel
        if rel.split("/")[0] == ".state" or not isinstance(before, dict):
            actions.append({"stage": stage, "path": path, "action": "reported"})
            continue
        if now.get(rel) == before.get(rel):
            continue
        if rel in before:
            ok = _restore_llake(state, rel, os.path.join(pre, rel))
        else:
            ok = _restore_llake(state, rel, None)
        actions.append({"stage": stage, "path": path, "action": "reverted" if ok else "reported"})
    for path in outside:
        actions.append({"stage": stage, "path": path, "action": "reported-outside-llake"})
    if actions:
        state.ledger.setdefault("surface", []).extend(actions)
        state.save()
    return actions


def revert_run(project_root, agent_dir):
    """Kill path (cursor-table row 9): undo what this run's writers changed under llake/; no-op once finalized.

    Restores inFlight, changed and finalizeWrites paths, each from its own revert point: a changed page from
    the snapshot taken before the run first changed it (journal changedFrom), an in-flight page from its
    bundle snapshot, a file only finalize wrote from the finalize snapshot; pre/ only when a path has none of
    these. Anything without evidence (missing copy/manifest) is left alone, reported as unrestored and keeps
    the run un-aborted so dead-run recovery can retry it."""
    state = RunState(project_root, agent_dir)
    j = state.journal
    if j.get("finalized"):
        return {"skipped": True, "reason": "run already finalized"}
    if j.get("aborted"):
        return {"skipped": True, "reason": "run already reverted"}
    pre = os.path.join(state.dir, PRE)
    before = load_json(os.path.join(state.dir, "pre-manifest.json"), None)
    in_flight = dict(j.get("inFlight") or {})
    changed_from = dict(j.get("changedFrom") or {})
    fin_dir = os.path.join(state.dir, FINALIZE_SNAP)
    fin_manifest = load_json(os.path.join(fin_dir, "manifest.json"), None)
    fin_manifest = fin_manifest if isinstance(fin_manifest, dict) else {}
    paths = set(in_flight) | set(j.get("changed") or []) | set(j.get("finalizeWrites") or [])
    paths.discard("log.md")
    if before is None and not paths:
        j["aborted"] = True
        state.save()
        return {"skipped": True, "reason": "no pre-run copy: nothing was written"}
    restored, unrestored = [], []
    for rel in sorted(paths):
        # The earliest revert point wins: a writer's snapshot precedes a later fixer's in-flight one.
        snap = changed_from.get(rel) or in_flight.get(rel) or (fin_dir if rel in fin_manifest else None)
        if snap is not None:
            ok = restore_page(state, rel, snap) != "unrestored"
        elif isinstance(before, dict):
            ok = _restore_llake(state, rel, os.path.join(pre, rel) if rel in before else None)
        else:
            ok = False
        (restored if ok else unrestored).append(rel)
        if ok:
            j["inFlight"].pop(rel, None)
    entry = j.get("logEntry") or ""
    if entry:
        log = os.path.join(state.llake, "log.md")
        text = read_text(log)
        if entry in text:
            write_text(log, text.replace(entry, "", 1))
    if not unrestored:
        j["aborted"] = True
    state.save()
    return {"skipped": False, "restored": restored, "unrestored": unrestored}
