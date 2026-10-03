"""The fix round (spec §10): one fresh writer per bundle with flagged pages, given the flags (and verifier
findings when the verifier is on). One round only. A failed fixer reverts its pages to the post-write
state; checks then run again and a page still flagged becomes a `flagged` gap at finalize.
"""
import os
import time

from . import snapshots
from .common import load_json, norm_ws, read_text
from .dispatch import WriterContext, default_spawn, pool, revert_job, statuses, surface_check, with_snapshot

FIXABLE = ("corrected", "declared", "no-change")


def fix_findings(state):
    checks = load_json(os.path.join(state.dir, "checks.json"), {}) or {}
    flags = checks.get("flags") or {}
    out = {}
    for p, st in sorted(state.pages.items()):
        if st.get("outcome") not in FIXABLE or not st.get("changed"):
            continue
        found = list(flags.get(p, []))
        text = norm_ws(read_text(state.abs(p)))
        for kind in ("accuracy", "residual"):
            for f in (st.get("verifier") or {}).get(kind, []):
                if f.get("quote") and norm_ws(f["quote"]) in text:
                    found.append({"quote": f["quote"], "head": f.get("head", ""),
                                  "severity": f.get("severity", "major"), "source": "verifier:" + kind})
        if found:
            out[p] = found
    return out


def run_fix_round(state, cfg, brief, base, head, deadline, spawn=None, today=None, clock=time.time,
                  sleep=time.sleep):
    findings = fix_findings(state)
    state.ledger["fixStop"] = None
    if not findings:
        state.save()
        return None
    today = today or time.strftime("%Y-%m-%d")
    if spawn is None:
        spawn = default_spawn(state, cfg, brief, deadline, WriterContext(state, cfg, brief, today))
    groups = {}
    for p in sorted(findings):
        groups.setdefault(state.pages[p]["bundle"], []).append(p)
    jobs = []
    for bid, pages in sorted(groups.items()):
        snap = os.path.join(state.dir, "bundles", bid, "fix-snapshot")
        for p in pages:
            state.pages[p]["fixSnap"] = snap
        jobs.append({"kind": "fixer", "stage": "fixer-" + bid, "bundle": bid, "pages": pages, "retry": True,
                     "findings": {p: findings[p] for p in pages}, "snap": snap, "base": base, "head": head})
    state.save()

    def on_done(job, s, stop, queue):
        surface_check(state, job, s)
        st = statuses(s, "writer-status") if s["class"] == "none" else None
        if st is not None:
            snapshots.settle(state, job["pages"])
            for p in job["pages"]:
                e = st.get(p) or {}
                rec = state.pages[p]
                rec["fixed"] = True
                rec["rejected"] = list(rec.get("rejected") or []) + list(e.get("rejected") or [])
                if e.get("status") == "declared-gap":
                    rec["outcome"], rec["status"] = "declared", "declared-gap"
                    rec["claimsLeft"] = e.get("claimsLeft", [])
                rec["history"].append("fixer: {} {}".format(e.get("status"), (e.get("note") or "")[:80]))
            return None
        unrestored = revert_job(state, job)
        for p in job["pages"]:
            tail = "left as written" if p in unrestored else "reverted to the post-write state"
            state.pages[p]["history"].append("fixer {}: {} ({})".format(s["class"], s["reason"][:80], tail))
        return "infra" if s["class"] == "infra" else None

    def on_skip(job, why):
        for p in job["pages"]:
            state.pages[p]["history"].append("fixer not dispatched: {}".format(why))

    stop = pool(state, cfg, jobs, deadline, with_snapshot(state, spawn), on_done, on_skip, clock, sleep)
    state.ledger["fixStop"] = stop
    state.save()
    return stop
