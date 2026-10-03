"""The agent pool and the write stage (spec §8 writers, §10 verifier, §11 run cap, §12 failure classes).

Run cap: before each spawn, spent + the budgets of agents in flight + this agent's budget must be
<= maxRunBudgetUsd; otherwise wait for in-flight agents, or skip (`run-cap`) when none is in flight.
Prefix warm-up: the first writer runs alone until its first turn, so the shared prefix is cached once.
A work failure reverts the bundle and retries each page alone once; a second failure is `writer-failed`.
An infra failure stops dispatch: in-flight bundles are killed and reverted, the rest become `infra`.
A page whose snapshot cannot be restored is left as written, recorded as `unrestored` and never retried.
Every finished agent's summary (with its cost) goes to the ledger's per-stage record and to `spent`.
"""
import json
import os
import time

from . import snapshots
from .agent import Agent, allow_rule, build_argv, clip_timeout
from .anchors import Source
from .common import load_json, write_text
from .prompts import bundle_prompt, shared_prefix, verifier_prompt
from .schema import load_schema, validate
from .wiki import norm_page

WRITER_TOOLS = "Read,Glob,Grep,Edit,Write"
READ_TOOLS = "Read,Glob,Grep"
OUTCOME_OF_STATUS = {"corrected": "corrected", "declared-gap": "declared", "no-change": "no-change"}


def budget_of(cfg, job):
    return float(cfg.get("verifierBudgetUsd") if job["kind"] == "verifier" else cfg.get("writerBudgetUsd"))


def statuses(summary, schema_name):
    data = summary.get("structured")
    if data is None or validate(data, load_schema(schema_name)):
        return None
    out = {}
    for e in data.get("pages", []):
        p = norm_page(str(e.get("page", "")))
        if p:
            out[p] = e
    return out


def pool(state, cfg, jobs, deadline, start, on_done, on_skip, clock=time.time, sleep=time.sleep):
    cap, conc = float(cfg.get("maxRunBudgetUsd")), max(1, int(cfg.get("writerConcurrency")))
    queue, running, done, stop, warm = list(jobs), [], [], None, False
    while queue or running:
        if stop is None and clock() > deadline:
            stop = "timeout"
        if stop:
            for _job, ag in running:
                ag.kill("infra-stop" if stop == "infra" else "timeout")
        for job, ag in list(running):
            if ag.poll():
                running.remove((job, ag))
                done.append(job)
                s = ag.summary()
                state.record_stage(s, job["kind"])
                if on_done(job, s, stop, queue) == "infra" and stop is None:
                    stop = "infra"
                state.save()
        if stop:
            while queue:
                on_skip(queue.pop(0), stop)
            if not running:
                break
            sleep(0.25)
            continue
        while queue and len(running) < conc:
            job = queue[0]
            if job["kind"] in ("writer", "fixer") and not warm:
                writers = [a for j, a in running if j["kind"] in ("writer", "fixer")]
                if any(a.first_turn_done() for a in writers) or any(j["kind"] in ("writer", "fixer") for j in done):
                    warm = True
                elif writers:
                    break
            reserved = sum(budget_of(cfg, j) for j, _ in running)
            if state.spent + reserved + budget_of(cfg, job) > cap:
                if running:
                    break
                on_skip(queue.pop(0), "run-cap")
                continue
            queue.pop(0)
            running.append((job, start(job)))
        state.save()
        if queue or running:
            sleep(0.25)
    state.save()
    return stop


class WriterContext:
    def __init__(self, state, cfg, brief, today):
        inputs = os.path.join(state.dir, "inputs")
        self.names = load_json(os.path.join(inputs, "names.json"), {}) or {}
        self.anchor_hits = load_json(os.path.join(inputs, "anchor-hits.json"), {}) or {}
        self.literal_hits = load_json(os.path.join(inputs, "literal-hits.json"), {}) or {}
        self.patches_rel = os.path.relpath(os.path.join(inputs, "patches"), state.project)
        self.prefix_file = os.path.join(state.dir, "stages", "writer.shared.md")
        self.src = Source(state.project)
        if not os.path.exists(self.prefix_file):
            write_text(self.prefix_file, shared_prefix(state.llake, brief, self.names, cfg.get("writeMode"), today,
                                                       os.path.relpath(inputs, state.project)))


def start_agent(state, cfg, stage, argv, prompt, timeout):
    """Start one claude -p for `stage`: cwd the project, output under stages/, agent id <run>_<stage>."""
    return Agent(stage, argv, prompt, state.project, os.path.join(state.dir, "stages"), timeout,
                 cfg.get("cacheTtl"), "{}_{}".format(os.path.basename(state.dir), stage)).start()


def job_spawn_args(state, cfg, brief, deadline, ctx, job):
    """(argv, prompt, timeout) for a writer, fixer or verifier job."""
    if job["kind"] == "verifier":
        diffs = {p: snapshots.page_diff(state, p, job["snap"]) for p in job["pages"]}
        prompt = verifier_prompt(job, brief, diffs, ctx.patches_rel, cfg.get("verifierMode"))
        argv = build_argv(cfg.get("verifierModel"), cfg.get("verifierEffort"), cfg.get("verifierBudgetUsd"),
                          READ_TOOLS, READ_TOOLS, json.dumps(load_schema("verifier-findings")))
        return argv, prompt, clip_timeout(cfg.get("verifierTimeoutSeconds"), deadline)
    prompt = bundle_prompt(job, brief, state.llake, ctx.src, ctx.anchor_hits, ctx.literal_hits,
                           ctx.patches_rel, fixer=job["kind"] == "fixer")
    allowed = ",".join([READ_TOOLS] + [allow_rule(state.abs(p)) for p in job["pages"]])
    argv = build_argv(cfg.get("writerModel"), cfg.get("writerEffort"), cfg.get("writerBudgetUsd"),
                      WRITER_TOOLS, allowed, json.dumps(load_schema("writer-status")), ctx.prefix_file)
    return argv, prompt, clip_timeout(cfg.get("writerTimeoutSeconds"), deadline)


def default_spawn(state, cfg, brief, deadline, ctx):
    def spawn(job):
        argv, prompt, timeout = job_spawn_args(state, cfg, brief, deadline, ctx, job)
        return start_agent(state, cfg, job["stage"], argv, prompt, timeout)
    return spawn


def with_snapshot(state, spawn):
    def start(job):
        if job["kind"] in ("writer", "fixer"):
            snapshots.snapshot(state, job["pages"], job["snap"])
        return spawn(job)
    return start


def surface_check(state, job, summary):
    surface = job["pages"] if job["kind"] in ("writer", "fixer") else []
    inside, outside = snapshots.out_of_surface(state, summary, surface)
    if inside or outside:
        snapshots.revert_out_of_surface(state, job["stage"], inside, outside)


def revert_job(state, job):
    """Restore a job's pages from its snapshot; a page that cannot be restored is recorded, never deleted.

    Returns the unrestored pages (they stay in the journal's inFlight for the kill/recovery path)."""
    unrestored = snapshots.revert(state, job["pages"], job["snap"])
    for p in unrestored:
        state.pages[p]["unrestored"] = True
        state.pages[p]["history"].append("{}: snapshot could not be restored; page left as written".format(
            job["stage"]))
        state.ledger.setdefault("unrestored", []).append({"stage": job["stage"], "page": p})
    return unrestored


def run_writers(state, cfg, brief, bundles, base, head, deadline, spawn=None, today=None,
                clock=time.time, sleep=time.sleep):
    state.ledger["writeStop"] = None
    if not bundles:
        state.save()
        return None
    today = today or time.strftime("%Y-%m-%d")
    if spawn is None:
        spawn = default_spawn(state, cfg, brief, deadline, WriterContext(state, cfg, brief, today))
    by_page = {p["page"]: p for p in brief["pages"]}
    verify = cfg.get("verifierMode") != "off"
    for b in bundles:
        for p in b["pages"]:
            e = by_page[p]
            state.pages[p] = {"page": p, "severity": e["severity"], "carried": bool(e.get("carried")),
                              "since": e.get("since"), "attempts": e.get("attempts", 0),
                              "create": bool(e.get("create")), "bundle": b["id"], "dispatched": False,
                              "outcome": None, "status": None, "claimsLeft": [], "rejected": [], "changed": False,
                              "verifier": None, "unverified": False, "snap": None, "note": "", "history": []}
    jobs = [{"kind": "writer", "stage": "writer-" + b["id"], "bundle": b["id"], "pages": list(b["pages"]),
             "retry": False, "snap": os.path.join(state.dir, "bundles", b["id"], "snapshot"),
             "base": base, "head": head} for b in bundles]

    def on_done(job, s, stop, queue):
        pages = job["pages"]
        surface_check(state, job, s)
        if job["kind"] == "verifier":
            found = statuses(s, "verifier-findings") if s["class"] == "none" else None
            for p in pages:
                if found is not None and p in found:
                    state.pages[p]["verifier"] = {"accuracy": found[p].get("accuracy", []),
                                                  "residual": found[p].get("residual", [])}
                else:
                    state.pages[p]["unverified"] = True
                    state.pages[p]["history"].append("verifier {}: {}".format(s["class"], s["reason"][:80]))
            return "infra" if s["class"] == "infra" else None
        st = statuses(s, "writer-status") if s["class"] == "none" else None
        if st is not None:
            snapshots.settle(state, pages)
            changed = []
            for p in pages:
                e = st.get(p) or {}
                ch = snapshots.changed_since(state, p, job["snap"])
                status = e.get("status") or ("corrected" if ch else "no-change")
                state.pages[p].update({"dispatched": True, "status": status, "changed": ch,
                                       "claimsLeft": e.get("claimsLeft", []), "rejected": e.get("rejected", []),
                                       "note": e.get("note", ""), "snap": job["snap"],
                                       "outcome": OUTCOME_OF_STATUS[status]})
                if not e:
                    state.pages[p]["history"].append("writer returned no status for this page")
                if ch:
                    changed.append(p)
            state.ledger.setdefault("otherStale", []).extend((s.get("structured") or {}).get("otherStale") or [])
            if verify and changed:
                queue.insert(0, {"kind": "verifier", "stage": job["stage"].replace("writer-", "verifier-", 1),
                                 "bundle": job["bundle"], "pages": changed, "snap": job["snap"], "retry": True,
                                 "base": base, "head": head})
            return None
        unrestored = revert_job(state, job)
        reason = s["reason"] if s["class"] != "none" else "missing or invalid structured status"
        for p in pages:
            state.pages[p]["dispatched"] = True
            state.pages[p]["history"].append("{} {}: {}".format(job["stage"], s["class"], reason[:100]))
        if s["class"] == "infra" or stop:
            outcome = "infra" if (s["class"] == "infra" or stop == "infra") else "timeout"
            for p in pages:
                state.pages[p]["outcome"] = outcome
            return "infra" if s["class"] == "infra" else None
        retry = [] if job["retry"] else [p for p in pages if p not in unrestored]
        for i, p in enumerate(retry):
            queue.insert(i, {"kind": "writer", "stage": "{}-s{}".format(job["stage"], i + 1),
                             "bundle": job["bundle"], "pages": [p], "retry": True,
                             "snap": os.path.join(state.dir, "bundles", job["bundle"], "retry{}".format(i + 1)),
                             "base": base, "head": head})
        for p in pages:
            if p not in retry:
                state.pages[p]["outcome"] = "writer-failed"
        return None

    def on_skip(job, why):
        for p in job["pages"]:
            if job["kind"] == "verifier":
                state.pages[p]["unverified"] = True
            else:
                state.pages[p]["outcome"] = why
            state.pages[p]["history"].append("{} not dispatched: {}".format(job["stage"], why))

    stop = pool(state, cfg, jobs, deadline, with_snapshot(state, spawn), on_done, on_skip, clock, sleep)
    for rec in state.pages.values():
        if rec["outcome"] is None:
            rec["outcome"] = "timeout"
    state.ledger["writeStop"] = stop
    state.save()
    return stop
