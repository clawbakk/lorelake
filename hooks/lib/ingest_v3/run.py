"""The ingest v3 orchestrator (spec §2, design §5). Exit 0: finalized, or nothing to do. Exit 1: the cursor holds.
Exit 3 (EXIT_CONTINUE): a split or skip finalized short of HEAD; post-merge.sh runs the remainder at once.

One run: recover dead runs → plan → (empty | skip) or pre-run copy → inputs → analysis → recall → brief →
bundles → writers → checks → fix round → checks → finalize → report. Every outcome writes exactly one
`agent-done` line to llake/.state/hooks.log; the run's own trace goes to <agent dir>/agent.log.

Benchmark-only environment hooks (spec §13), never config: LLAKE_V3_FROZEN_BRIEF=<earlier agent dir>
copies that run's brief/ and skips the analysis spawn; LLAKE_V3_STOP_AFTER=<stage> stops after
analysis | recall | brief | bundle | write | fix with the cursor held.

Kill signals (SIGTERM, SIGHUP, SIGINT) raise Killed wherever the run is: the pool kills and reverts its
running bundles on the way out, then the run kills any other live agent (analysis, recall) and reverts its
own writes (snapshots.revert_run, idempotent with the bash trap's revert-run that follows), cursor held.
"""
import os
import shutil
import signal
import time
import traceback

from . import names, plan, snapshots, stage
from .agent import GLOB_CHARS, build_argv, clip_timeout, defer_signal, kill_live
from .brief import MISSING_THEMES, InvalidBrief, assemble, fill_defaults
from .bundle import make_bundles
from .checks import run_checks
from .common import dump_json, git, load_json, read_text
from .config import V3Config
from .dispatch import READ_TOOLS, WRITER_TOOLS, run_writers, start_agent
from .finalize import finalize, record_skip, write_cursor
from .fix import run_fix_round
from .report import git_status_outside_llake, write_report
from .schema import load_schema, validate
from .state import RunState

STOP_STAGES = ("analysis", "recall", "brief", "bundle", "write", "fix")
EXIT_CONTINUE = 3  # a finalized split or skip stopped short of HEAD: the hook continues with the remainder (§3)


class HoldRun(Exception):
    """The run stops before finalize; the cursor holds."""


class Killed(BaseException):
    """A kill signal reached the run. A BaseException, so no `except Exception` on the way swallows it."""

    def __init__(self, signum):
        BaseException.__init__(self, signum)
        self.signum = signum

    @property
    def name(self):
        try:
            return signal.Signals(self.signum).name
        except ValueError:
            return str(self.signum)


KILL_SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT")


def _install_kill_handlers():
    """Raise Killed on the first kill signal (later ones are ignored while the run cleans up). Returns the
    previous handlers; nothing is installed off the main thread."""
    fired = []

    def handler(signum, _frame):
        if fired or defer_signal(signum):
            return
        fired.append(signum)
        raise Killed(signum)
    prev = {}
    for name in KILL_SIGNALS:
        sig = getattr(signal, name)
        try:
            prev[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
    return prev


def _on_killed(project, agent_dir, exc):
    """Stop every agent still alive, then undo the run's writes under llake/. Exit 0 only when the kill came
    after finalize (the run stands); otherwise 1, cursor held."""
    n = kill_live("killed")
    os.makedirs(agent_dir, exist_ok=True)
    log = _AgentLog(os.path.join(agent_dir, "agent.log"))
    try:
        res = snapshots.revert_run(project, agent_dir)
    except Exception:
        log.line("killed by {}: {} agent(s) stopped; revert failed, cursor held (the kill trap's revert-run "
                 "or the next run's recovery retries it):\n{}".format(exc.name, n, traceback.format_exc()))
        return 1
    if res.get("reason") == "run already finalized":
        log.line("killed by {} after finalize: the run stands".format(exc.name))
        return 0
    log.line("killed by {}: {} agent(s) stopped, run reverted ({}), cursor held".format(
        exc.name, n, "restored {}, unrestored {}".format(len(res.get("restored") or []),
                                                         len(res.get("unrestored") or []))
        if not res.get("skipped") else res.get("reason")))
    return 1


class _AgentLog:
    def __init__(self, path):
        self.path = path

    def line(self, msg):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("[{}] {}\n".format(time.strftime("%H:%M:%S"), msg))


def hooks_log(llake, msg):
    """One `agent-done` line in hooks.log, in the hook_log_line format (hooks/lib/hook-log.sh)."""
    path = os.path.join(llake, ".state", "hooks.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{} | {:<13} | {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), "agent-done", msg))


def _stop(stop_after, name, log):
    if stop_after == name:
        log.line("LLAKE_V3_STOP_AFTER={}: stopping, cursor held".format(name))
        raise HoldRun("LLAKE_V3_STOP_AFTER={}".format(name))


def _dir_rule(state, directory):
    """Edit permission on one run-owned directory and everything under it (analysis: brief/; recall:
    brief/pages/). agent.allow_rule grants single files only, so this is the one place a directory rule is
    built: only for a directory strictly inside this run's agent dir, itself directly under llake/.state/agents/."""
    d = os.path.abspath(str(directory))
    run_dir = os.path.abspath(state.dir)
    agents = os.path.join(os.path.abspath(state.llake), ".state", "agents")
    if os.path.dirname(run_dir) != agents:
        raise ValueError("agent dir {} is not directly under {}".format(run_dir, agents))
    if not d.startswith(run_dir + os.sep) or GLOB_CHARS & set(d):
        raise ValueError("not a directory inside this run's agent dir: {}".format(d))
    return "Edit(/{}/**)".format(d)


def _spawn_and_wait(state, cfg, name, model, effort, budget, timeout_key, prompt, write_dir, deadline):
    """Analysis or recall: read-only plus Edit/Write on one run-owned dir; writes elsewhere are reverted."""
    argv = build_argv(model, effort, budget, WRITER_TOOLS, ",".join([READ_TOOLS, _dir_rule(state, write_dir)]))
    s = start_agent(state, cfg, name, argv, prompt, clip_timeout(cfg.get(timeout_key), deadline)).wait()
    state.record_stage(s, name)
    inside, outside = snapshots.out_of_surface(state, s, [], allowed_dirs=[write_dir])
    if inside or outside:
        snapshots.revert_out_of_surface(state, name, inside, outside)
    state.save()
    return s


def _analysis(state, cfg, include, staged, base, head, deadline, log):
    prompt = stage.analysis_prompt(state.project, state.llake, state.dir, base, head, include, staged)
    s = _spawn_and_wait(state, cfg, "analysis", cfg.get("analysisModel"), cfg.get("analysisEffort"),
                        cfg.get("analysisBudgetUsd"), "analysisTimeoutSeconds", prompt,
                        os.path.join(state.dir, "brief"), deadline)
    log.line("analysis: {} (${:.2f}, {} turns)".format(s["class"], s["cost_usd"], s["turns"]))
    if s["class"] != "none":
        plan.record_failure(state.llake, base, head, s["class"])
        raise HoldRun("analysis {} failure: {}".format(s["class"], s["reason"][:120]))


def _valid_page_file(path):
    doc = load_json(path, None)
    if doc is None:
        return False
    schema = load_schema("brief-page")
    return all(isinstance(d, dict) and not validate(fill_defaults(d), schema) for d in (doc if isinstance(doc, list) else [doc]))


def _recall(state, cfg, deadline, log):
    """Spec §6: a recall failure of any kind never fails the run; its files are set aside. Recall is optional,
    so when its budget does not fit the run cap it is skipped (nothing else is in flight at this point)."""
    budget, cap = float(cfg.get("recallBudgetUsd")), float(cfg.get("maxRunBudgetUsd"))
    if state.spent + budget > cap:
        log.line("recall skipped: run cap (${:.2f} spent + ${:.2f} budget > ${:.2f} cap)".format(
            state.spent, budget, cap))
        return
    pages_dir = os.path.join(state.dir, "brief", "pages")
    os.makedirs(pages_dir, exist_ok=True)
    before = {}
    for name in os.listdir(pages_dir):
        if os.path.isfile(os.path.join(pages_dir, name)):
            with open(os.path.join(pages_dir, name), "rb") as fh:
                before[name] = fh.read()
    prompt = stage.recall_prompt(state.project, state.llake, state.dir)
    s = _spawn_and_wait(state, cfg, "recall", cfg.get("recallPass"), cfg.get("recallEffort"), budget,
                        "recallTimeoutSeconds", prompt, pages_dir, deadline)
    # Recall may only add recall-*.json files: the files analysis wrote are restored byte for byte, and any
    # other new file is set aside (assemble treats only recall-* problems as warnings).
    for name, data in sorted(before.items()):
        path = os.path.join(pages_dir, name)
        try:
            with open(path, "rb") as fh:
                same = fh.read() == data
        except OSError:
            same = False
        if not same:
            with open(path, "wb") as fh:
                fh.write(data)
            log.line("recall changed analysis file {}: restored".format(name))
    rejected = os.path.join(state.dir, "brief", "rejected-recall")
    for name in sorted(set(os.listdir(pages_dir)) - set(before)):
        path = os.path.join(pages_dir, name)
        if not name.startswith("recall-"):
            why = "not named recall-*"
        elif s["class"] != "none":
            why = s["class"]
        elif not _valid_page_file(path):
            why = "invalid"
        else:
            continue
        os.makedirs(rejected, exist_ok=True)
        os.replace(path, os.path.join(rejected, name))
        log.line("recall file {} set aside ({})".format(name, why))
    log.line("recall: {} (${:.2f}); the brief stands either way".format(s["class"], s["cost_usd"]))


def _since_rank(project, brief):
    rank = {}
    for p in brief["pages"]:
        since = p.get("since")
        if p.get("carried") and since and since not in rank:
            out = git(project, "rev-list", "--count", since, check=False).strip()
            if out.isdigit():
                rank[since] = int(out)
    return rank


def _log_unrecovered(llake, agent_dir, log):
    """plan.recover_dead_runs leaves a dead run's page alone when it has no snapshot to restore from."""
    agents = os.path.join(llake, ".state", "agents")
    for aid in sorted(os.listdir(agents)) if os.path.isdir(agents) else []:
        if os.path.abspath(os.path.join(agents, aid)) == agent_dir:
            continue
        j = load_json(os.path.join(agents, aid, "run.json"), None)
        if isinstance(j, dict) and j.get("unrecovered") and not (j.get("finalized") or j.get("aborted")):
            log.line("dead run {} left unrecovered: {} (no snapshot to restore from; needs a human)".format(
                aid, ", ".join(str(p) for p in j["unrecovered"])))


def _finished(project, rp, log):
    """0, or EXIT_CONTINUE when a finalized split or skip left the cursor short of HEAD."""
    if rp.kind not in ("split", "skip"):
        return 0
    now = git(project, "rev-parse", "HEAD").strip()
    if now == rp.head:
        return 0
    log.line("remainder {}..{} pending: continuing in this invocation".format(rp.head[:7], now[:7]))
    return EXIT_CONTINUE


def run(project_root, agent_id, agent_dir, deadline, environ=None, today=None, clock=time.time):
    prev = _install_kill_handlers()
    try:
        return _run(project_root, agent_id, agent_dir, deadline, environ, today, clock)
    except Killed as exc:
        return _on_killed(os.path.abspath(str(project_root)), os.path.abspath(str(agent_dir)), exc)
    finally:
        for sig, handler in prev.items():
            signal.signal(sig, handler)


def _run(project_root, agent_id, agent_dir, deadline, environ, today, clock):
    env = os.environ if environ is None else environ
    project = os.path.abspath(str(project_root))
    llake = os.path.join(project, "llake")
    agent_dir = os.path.abspath(str(agent_dir))
    os.makedirs(agent_dir, exist_ok=True)
    log = _AgentLog(os.path.join(agent_dir, "agent.log"))
    today = today or time.strftime("%Y-%m-%d")
    stop_after = (env.get("LLAKE_V3_STOP_AFTER") or "").strip()
    frozen = (env.get("LLAKE_V3_FROZEN_BRIEF") or "").strip()
    if stop_after and stop_after not in STOP_STAGES:
        log.line("LLAKE_V3_STOP_AFTER={} ignored: not one of {}".format(stop_after, ", ".join(STOP_STAGES)))
    t0 = clock()
    try:
        cfg = V3Config(os.path.join(llake, "config.json"))
        include = cfg.include()
        for aid in plan.recover_dead_runs(llake, exclude_dir=agent_dir):
            log.line("recovered the in-flight pages of dead run " + aid)
        _log_unrecovered(llake, agent_dir, log)
        base = read_text(os.path.join(llake, "last-ingest-sha")).strip()
        head = git(project, "rev-parse", "HEAD").strip()
        stale = plan.load_failures(llake)
        if stale and not plan.active_failures(llake, base):
            was, now, at = stale.get("plugin") or "unrecorded", plan.plugin_version(), str(stale.get("base") or "")[:7]
            why = "plugin {} -> {}, base {}".format(was, now, at) if was != now else \
                "base {}, cursor {}".format(at, base[:7])
            log.line("failure counter ignored ({}): planning the full range".format(why))
        rp = plan.plan_run(project, llake, include, base, head)
        state = RunState(project, agent_dir)
        state.journal.update({"kind": rp.kind, "base": base, "head": rp.head})
        state.save()
        dump_json(os.path.join(agent_dir, "run-config.json"), cfg.effective())
        log.line("plan: {} {}..{}".format(rp.kind, base[:7], rp.head[:7]))

        if rp.kind == "empty":
            write_cursor(llake, rp.head)
            state.journal["finalized"] = True
            state.save()
            hooks_log(llake, "empty: nothing to ingest, nothing owed (agent {} v3, sha {})".format(agent_id, rp.head[:7]))
            return 0
        snapshots.take_pre(state)  # before any write under llake/, so a kill can revert this run (skip included)
        if rp.kind == "skip":
            n = names.derive(project, base, rp.head, include)
            record_skip(state, agent_id, base, rp.head, names.hit_leads(names.wiki_hits(llake, n["removed"])), today)
            hooks_log(llake, "skipped: range with one watched commit {}..{} failed analysis twice (agent {} v3)".format(
                base[:7], rp.head[:7], agent_id))
            return _finished(project, rp, log)

        git_before = git_status_outside_llake(project)
        inputs = os.path.join(agent_dir, "inputs")
        gap_only = rp.kind == "gap-only"
        if gap_only:
            names.write_empty_inputs(inputs)
        else:  # a range or split head always has watched changes (plan.split_point never picks an empty slice)
            names.write_inputs(project, base, rp.head, include, llake, inputs)
            staged = stage.stage_inputs(project, base, rp.head, include, llake, agent_dir)
            if frozen:
                shutil.copytree(os.path.join(frozen, "brief"), os.path.join(agent_dir, "brief"), dirs_exist_ok=True)
                log.line("analysis skipped: frozen brief from " + frozen)
            else:
                _analysis(state, cfg, include, staged, base, rp.head, deadline, log)
            _stop(stop_after, "analysis", log)
            if cfg.get("recallPass") != "off":
                _recall(state, cfg, deadline, log)
            _stop(stop_after, "recall", log)
        try:
            brief = assemble(agent_dir, llake, rp.head, gap_only=gap_only)
        except InvalidBrief as exc:
            if any(e.startswith(MISSING_THEMES) for e in exc.errors):
                # the analysis agent produced no usable output: work, so the range can still shrink
                plan.record_failure(llake, base, rp.head, "work")
                raise HoldRun("analysis produced no usable brief: {}".format("; ".join(exc.errors[:3])[:120]))
            # deterministic: the same brief fails on any range, so it never counts toward split or skip
            plan.record_failure(llake, base, rp.head, "pipeline")
            raise HoldRun("pipeline error: invalid brief: {} (not counted toward split)".format(
                "; ".join(exc.errors[:3])))
        _stop(stop_after, "brief", log)
        bundles = make_bundles(brief["pages"], llake, cfg.get("bundleMaxPages"), float(cfg.get("bundleMaxWeight")),
                               _since_rank(project, brief))
        dump_json(os.path.join(agent_dir, "bundles.json"), bundles)
        log.line("brief: {} pages in {} bundles".format(len(brief["pages"]), len(bundles)))
        _stop(stop_after, "bundle", log)
        write_stop = run_writers(state, cfg, brief, bundles, base, rp.head, deadline, today=today)
        run_checks(state, brief, "write")
        _stop(stop_after, "write", log)
        if cfg.get("fixRound") != "on":
            log.line("fix round off")
        elif write_stop in ("infra", "timeout"):
            # spec §12: an infra failure during writing stops further dispatch; past the deadline nothing fits
            log.line("fix round skipped: writing stopped ({})".format(write_stop))
        else:
            run_fix_round(state, cfg, brief, base, rp.head, deadline, today=today)
            run_checks(state, brief, "fix")
        _stop(stop_after, "fix", log)
        summary = finalize(state, cfg, brief, agent_id, base, rp.head, rp.kind, today=today)
        log.line("finalized: cursor at {}".format(rp.head[:7]))
        try:
            write_report(state, cfg, brief, summary, rp.kind, base, rp.head, git_before,
                         git_status_outside_llake(project), clock() - t0)
        except Exception:  # the run is finalized; a report failure must not read as a held cursor
            log.line("report.md not written:\n" + traceback.format_exc())
        hooks_log(llake, "completed: agent {} v3 {} (updated {}, created {}, gaps {}/{} major, ${:.2f}, sha {})".format(
            agent_id, rp.kind, len(summary["updated"]), len(summary["created"]), summary["gaps"],
            summary["major_gaps"], state.spent, rp.head[:7]))
        return _finished(project, rp, log)
    except HoldRun as exc:
        log.line("HOLD: {}".format(exc))
        hooks_log(llake, "held: agent {} v3 ({}) — cursor held".format(agent_id, exc))
        return 1
    except Exception as exc:  # any internal error holds the cursor (cursor-table row 10)
        log.line("INTERNAL ERROR, cursor held:\n" + traceback.format_exc())
        hooks_log(llake, "held: agent {} v3 (internal error: {}) — cursor held".format(agent_id, str(exc)[:160]))
        return 1
