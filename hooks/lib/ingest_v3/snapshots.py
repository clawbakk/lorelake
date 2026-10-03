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
    dump_json(os.path.join(state.dir, "pre-manifest.json"), _manifest(state.llake))


def current_manifest(state):
    return _manifest(state.llake)


def _restore_llake(state, rel, source):
    """Restore llake/<rel> from `source`, or delete it when source is None. Refuses anything outside llake/."""
    root = os.path.abspath(state.llake)
    target = os.path.abspath(os.path.join(root, rel))
    if not target.startswith(root + os.sep):
        raise ValueError("refusing to touch {} outside llake/".format(target))
    if source and os.path.exists(source):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    elif os.path.exists(target):
        os.remove(target)


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
    manifest = load_json(os.path.join(snap_dir, "manifest.json"), {}) or {}
    _restore_llake(state, page, os.path.join(snap_dir, page) if manifest.get(page) else None)


def revert(state, pages, dest):
    for p in pages:
        restore_page(state, p, dest)
        state.journal["inFlight"].pop(p, None)
    state.save()


def settle(state, pages):
    for p in pages:
        state.journal["inFlight"].pop(p, None)
    state.save()


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
    dirs = [os.path.abspath(d) + os.sep for d in allowed_dirs]
    llake = os.path.abspath(state.llake) + os.sep
    inside, outside = [], []
    for w in summary.get("writes") or []:
        path = str(w.get("file_path", ""))
        if not path:
            continue
        path = os.path.abspath(path if os.path.isabs(path) else os.path.join(state.project, path))
        if path in allowed or any(path.startswith(d) for d in dirs):
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
    before = load_json(os.path.join(state.dir, "pre-manifest.json"), {}) or {}
    now = _manifest(state.llake)
    actions = []
    for rel in inside:
        if rel.split("/")[0] == ".state":
            actions.append({"stage": stage, "path": "llake/" + rel, "action": "reported"})
            continue
        if now.get(rel) == before.get(rel):
            continue
        _restore_llake(state, rel, os.path.join(pre, rel) if rel in before else None)
        actions.append({"stage": stage, "path": "llake/" + rel, "action": "reverted"})
    for path in outside:
        actions.append({"stage": stage, "path": path, "action": "reported-outside-llake"})
    if actions:
        state.ledger.setdefault("surface", []).extend(actions)
        state.save()
    return actions


def revert_run(project_root, agent_dir):
    """Kill path (cursor-table row 9): undo this run's writes under llake/; no-op once finalized."""
    state = RunState(project_root, agent_dir)
    j = state.journal
    if j.get("finalized"):
        return {"skipped": True, "reason": "run already finalized"}
    if j.get("aborted"):
        return {"skipped": True, "reason": "run already reverted"}
    pre = os.path.join(state.dir, PRE)
    before = load_json(os.path.join(state.dir, "pre-manifest.json"), None)
    if before is None:
        j["aborted"] = True
        state.save()
        return {"skipped": True, "reason": "no pre-run copy: nothing was written"}
    restored = []
    paths = set(j.get("inFlight") or {}) | set(j.get("owned") or []) | set(j.get("finalizeWrites") or [])
    for rel in sorted(paths):
        if rel == "log.md":
            continue
        _restore_llake(state, rel, os.path.join(pre, rel) if rel in before else None)
        restored.append(rel)
    entry = j.get("logEntry") or ""
    if entry:
        log = os.path.join(state.llake, "log.md")
        text = read_text(log)
        if entry in text:
            write_text(log, text.replace(entry, "", 1))
    j["inFlight"] = {}
    j["aborted"] = True
    state.save()
    return {"skipped": False, "restored": restored}
