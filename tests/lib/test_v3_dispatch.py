"""Pool (run cap, warm-up, deadline) and the write stage (retry, infra stop, surface, verifier)."""
import json
import os

import pytest

from v3_helpers import REPO_ROOT, Cfg, make_project, page_text, write
from ingest_v3 import dispatch, snapshots
from ingest_v3.state import RunState


def cfg(**kw):
    base = {"maxRunBudgetUsd": 40.0, "writerConcurrency": 4, "writerBudgetUsd": 3.0, "verifierBudgetUsd": 1.0,
            "verifierMode": "off", "writeMode": "edit"}
    base.update(kw)
    return Cfg(base)


def full_cfg(**kw):
    """The plugin's real ingest.v3 defaults, for the spawn path that reads every stage knob."""
    with open(str(REPO_ROOT / "templates" / "config.default.json")) as fh:
        base = json.load(fh)["ingest"]["v3"]
    base.update(kw)
    return Cfg(base)


class FakeAgent:
    running = 0
    peak = 0

    def __init__(self, job, cls="none", cost=0.1, polls=1, structured=None, effect=None, turned=True, writes=()):
        self.job, self.cls, self.cost, self.polls = job, cls, cost, polls
        self.structured, self.effect, self.turned, self.writes = structured, effect, turned, list(writes)
        self.killed, self.done, self.poll_count = None, False, 0
        FakeAgent.running += 1
        FakeAgent.peak = max(FakeAgent.peak, FakeAgent.running)

    def poll(self):
        if self.done:
            return True
        self.poll_count += 1
        if self.killed or self.poll_count >= self.polls:
            if not self.killed and self.effect:
                self.effect()
            self.done = True
            FakeAgent.running -= 1
        return self.done

    def kill(self, why):
        self.killed = why

    def first_turn_done(self):
        return self.turned if not callable(self.turned) else self.turned(self)

    def summary(self):
        cls = self.cls if not self.killed else ("infra" if self.killed == "infra-stop" else "work")
        return {"stage": self.job["stage"], "class": cls, "reason": "fake " + cls, "cost_usd": self.cost,
                "structured": self.structured if cls == "none" else None, "writes": self.writes,
                "permission_denials": [], "killed": self.killed, "turns": 1}


@pytest.fixture(autouse=True)
def reset_counts():
    FakeAgent.running = FakeAgent.peak = 0


def jobs(n):
    return [{"kind": "writer", "stage": "writer-b{:02d}".format(i), "pages": [], "retry": False} for i in range(n)]


def test_run_cap_reserves_in_flight_budgets(tmp_path):
    state = RunState(tmp_path, tmp_path / "a")
    spawned, skipped = [], []

    def start(job):
        spawned.append(job["stage"])
        return FakeAgent(job, cost=0.5, polls=2)
    stop = dispatch.pool(state, cfg(maxRunBudgetUsd=7.0), jobs(4), 1e12, start, lambda *a: None,
                         lambda j, why: skipped.append((j["stage"], why)), sleep=lambda _: None)
    assert stop is None and skipped == [] and len(spawned) == 4
    assert FakeAgent.peak == 2
    assert state.spent == 2.0


def test_run_cap_skips_when_nothing_in_flight(tmp_path):
    state = RunState(tmp_path, tmp_path / "a")
    skipped = []
    dispatch.pool(state, cfg(maxRunBudgetUsd=4.0), jobs(3), 1e12, lambda j: FakeAgent(j, cost=2.5),
                  lambda *a: None, lambda j, why: skipped.append((j["stage"], why)), sleep=lambda _: None)
    assert skipped == [("writer-b01", "run-cap"), ("writer-b02", "run-cap")]


def test_first_writer_warms_the_prefix_alone(tmp_path):
    state = RunState(tmp_path, tmp_path / "a")
    order = []

    def start(job):
        order.append((job["stage"], [a.poll_count for a in agents]))
        a = FakeAgent(job, polls=3, turned=lambda self: self.poll_count >= 2)
        agents.append(a)
        return a
    agents = []
    dispatch.pool(state, cfg(), jobs(3), 1e12, start, lambda *a: None, lambda *a: None, sleep=lambda _: None)
    assert order[0] == ("writer-b00", [])
    assert order[1][0] == "writer-b01" and order[1][1][0] >= 2


def test_deadline_kills_running_and_skips_the_rest(tmp_path):
    state = RunState(tmp_path, tmp_path / "a")
    now = [0.0]
    skipped, done = [], []

    def start(job):
        now[0] = 100.0
        return FakeAgent(job, polls=50)
    stop = dispatch.pool(state, cfg(writerConcurrency=1), jobs(3), 50.0, start,
                         lambda j, s, st, q: done.append((j["stage"], s["class"], st)),
                         lambda j, why: skipped.append((j["stage"], why)), clock=lambda: now[0], sleep=lambda _: None)
    assert stop == "timeout"
    assert done == [("writer-b00", "work", "timeout")]
    assert skipped == [("writer-b01", "timeout"), ("writer-b02", "timeout")]


@pytest.fixture
def proj(tmp_path):
    repo = make_project(tmp_path, pages={"arch/x.md": page_text("X", "x", "old x"),
                                         "arch/y.md": page_text("Y", "y", "old y"),
                                         "arch/z.md": page_text("Z", "z", "old z")})
    state = RunState(repo, repo / "llake/.state/agents/run")
    snapshots.take_pre(state)
    return repo, state


def brief_for(*pages):
    return {"themes": [], "pages": [{"page": p, "severity": "major", "claims": []} for p in pages]}


def status(*pairs, other=()):
    return {"pages": [{"page": "llake/" + p, "status": s, "claimsLeft": [], "rejected": [], "note": ""}
                      for p, s in pairs], "otherStale": list(other)}


def edit(repo, page, text):
    return lambda: write(repo, "llake/" + page, text)


def test_writer_success_outcomes(proj):
    repo, state = proj
    other = [{"page": "llake/wiki/arch/z.md", "quote": "old z", "head": "h", "severity": "minor"}]

    def spawn(job):
        return FakeAgent(job, effect=edit(repo, "wiki/arch/x.md", "new x\n"),
                         structured=status(("wiki/arch/x.md", "corrected"), ("wiki/arch/y.md", "no-change"),
                                           other=other))
    bundles = [{"id": "b01", "pages": ["wiki/arch/x.md", "wiki/arch/y.md"]}]
    dispatch.run_writers(state, cfg(), brief_for("wiki/arch/x.md", "wiki/arch/y.md"), bundles, "a", "b", 1e12,
                         spawn=spawn, sleep=lambda _: None)
    assert state.pages["wiki/arch/x.md"]["outcome"] == "corrected" and state.pages["wiki/arch/x.md"]["changed"]
    assert state.pages["wiki/arch/y.md"]["outcome"] == "no-change"
    assert state.journal["inFlight"] == {} and set(state.journal["owned"]) == {"wiki/arch/x.md", "wiki/arch/y.md"}
    assert state.ledger["otherStale"] == other


def test_work_failure_reverts_and_retries_as_singletons(proj):
    repo, state = proj
    seen_at_retry = []

    def spawn(job):
        if job["stage"] == "writer-b01":
            return FakeAgent(job, cls="work", effect=edit(repo, "wiki/arch/x.md", "half\n"))
        seen_at_retry.append((repo / "llake/wiki/arch/x.md").read_text())
        p = job["pages"][0]
        return FakeAgent(job, effect=edit(repo, p, "fixed\n"), structured=status((p, "corrected")))
    bundles = [{"id": "b01", "pages": ["wiki/arch/x.md", "wiki/arch/y.md"]}]
    dispatch.run_writers(state, cfg(), brief_for("wiki/arch/x.md", "wiki/arch/y.md"), bundles, "a", "b", 1e12,
                         spawn=spawn, sleep=lambda _: None)
    assert "old x" in seen_at_retry[0]
    assert [s["stage"] for s in state.ledger["stages"]] == ["writer-b01", "writer-b01-s1", "writer-b01-s2"]
    assert state.pages["wiki/arch/x.md"]["outcome"] == "corrected"


def test_singleton_failure_is_writer_failed_and_reverted(proj):
    repo, state = proj
    dispatch.run_writers(state, cfg(), brief_for("wiki/arch/x.md"), [{"id": "b01", "pages": ["wiki/arch/x.md"]}],
                         "a", "b", 1e12, spawn=lambda j: FakeAgent(j, cls="work",
                                                                    effect=edit(repo, "wiki/arch/x.md", "bad\n")),
                         sleep=lambda _: None)
    assert state.pages["wiki/arch/x.md"]["outcome"] == "writer-failed"
    assert "old x" in (repo / "llake/wiki/arch/x.md").read_text()


def test_invalid_structured_status_is_a_failure(proj):
    repo, state = proj
    dispatch.run_writers(state, cfg(), brief_for("wiki/arch/x.md"), [{"id": "b01", "pages": ["wiki/arch/x.md"]}],
                         "a", "b", 1e12, spawn=lambda j: FakeAgent(j, structured={"pages": "nope"}),
                         sleep=lambda _: None)
    assert state.pages["wiki/arch/x.md"]["outcome"] == "writer-failed"


def test_infra_failure_stops_dispatch(proj):
    repo, state = proj

    def spawn(job):
        return FakeAgent(job, cls="infra", effect=edit(repo, "wiki/arch/x.md", "half\n"))
    bundles = [{"id": "b01", "pages": ["wiki/arch/x.md"]}, {"id": "b02", "pages": ["wiki/arch/y.md"]}]
    stop = dispatch.run_writers(state, cfg(writerConcurrency=1), brief_for("wiki/arch/x.md", "wiki/arch/y.md"),
                                bundles, "a", "b", 1e12, spawn=spawn, sleep=lambda _: None)
    assert stop == "infra"
    assert state.pages["wiki/arch/x.md"]["outcome"] == "infra"
    assert state.pages["wiki/arch/y.md"]["outcome"] == "infra"
    assert "old x" in (repo / "llake/wiki/arch/x.md").read_text()


def test_out_of_surface_writes(proj):
    repo, state = proj
    write(repo, "src/rogue.py", "user code\n")

    def effect():
        write(repo, "llake/wiki/arch/x.md", "new x\n")
        write(repo, "llake/wiki/arch/z.md", "tampered\n")
    writes = [{"tool": "Edit", "file_path": str(repo / "llake/wiki/arch/x.md")},
              {"tool": "Edit", "file_path": str(repo / "llake/wiki/arch/z.md")},
              {"tool": "Write", "file_path": str(repo / "src/rogue.py")}]
    dispatch.run_writers(state, cfg(), brief_for("wiki/arch/x.md"), [{"id": "b01", "pages": ["wiki/arch/x.md"]}],
                         "a", "b", 1e12, spawn=lambda j: FakeAgent(j, effect=effect, writes=writes,
                                                                    structured=status(("wiki/arch/x.md", "corrected"))),
                         sleep=lambda _: None)
    assert (repo / "llake/wiki/arch/x.md").read_text() == "new x\n"
    assert "old z" in (repo / "llake/wiki/arch/z.md").read_text()
    assert (repo / "src/rogue.py").read_text() == "user code\n"
    assert {a["action"] for a in state.ledger["surface"]} == {"reverted", "reported-outside-llake"}


def test_verifier_runs_after_its_writer(proj):
    repo, state = proj
    findings = {"pages": [{"page": "wiki/arch/x.md", "accuracy": [{"quote": "new x", "head": "h", "severity": "major"}],
                           "residual": []}]}

    def spawn(job):
        if job["kind"] == "verifier":
            return FakeAgent(job, structured=findings)
        return FakeAgent(job, effect=edit(repo, "wiki/arch/x.md", "new x\n"),
                         structured=status(("wiki/arch/x.md", "corrected")))
    dispatch.run_writers(state, cfg(verifierMode="accuracy"), brief_for("wiki/arch/x.md"),
                         [{"id": "b01", "pages": ["wiki/arch/x.md"]}], "a", "b", 1e12, spawn=spawn,
                         sleep=lambda _: None)
    assert [s["stage"] for s in state.ledger["stages"]] == ["writer-b01", "verifier-b01"]
    assert state.pages["wiki/arch/x.md"]["verifier"]["accuracy"][0]["quote"] == "new x"


def test_verifier_failure_marks_unverified(proj):
    repo, state = proj

    def spawn(job):
        if job["kind"] == "verifier":
            return FakeAgent(job, cls="work")
        return FakeAgent(job, effect=edit(repo, "wiki/arch/x.md", "new x\n"),
                         structured=status(("wiki/arch/x.md", "corrected")))
    dispatch.run_writers(state, cfg(verifierMode="accuracy"), brief_for("wiki/arch/x.md"),
                         [{"id": "b01", "pages": ["wiki/arch/x.md"]}], "a", "b", 1e12, spawn=spawn,
                         sleep=lambda _: None)
    assert state.pages["wiki/arch/x.md"]["unverified"] is True
    assert state.pages["wiki/arch/x.md"]["outcome"] == "corrected"


def test_every_spawn_cost_lands_in_the_ledger(proj):
    repo, state = proj
    costs = {"writer-b01": 0.4, "writer-b01-s1": 0.3, "verifier-b01-s1": 0.2}

    def spawn(job):
        if job["stage"] == "writer-b01":
            return FakeAgent(job, cls="work", cost=costs[job["stage"]])
        if job["kind"] == "verifier":
            return FakeAgent(job, cost=costs[job["stage"]], structured={"pages": []})
        return FakeAgent(job, cost=costs[job["stage"]], effect=edit(repo, "wiki/arch/x.md", "new x\n"),
                         structured=status(("wiki/arch/x.md", "corrected")))
    dispatch.run_writers(state, cfg(verifierMode="accuracy"), brief_for("wiki/arch/x.md"),
                         [{"id": "b01", "pages": ["wiki/arch/x.md"]}], "a", "b", 1e12, spawn=spawn,
                         sleep=lambda _: None)
    assert [(s["stage"], s["kind"], s["cost_usd"]) for s in state.ledger["stages"]] == [
        ("writer-b01", "writer", 0.4), ("writer-b01-s1", "writer", 0.3), ("verifier-b01-s1", "verifier", 0.2)]
    assert state.spent == 0.9
    assert RunState(state.project, state.dir).spent == 0.9


def test_unrestorable_page_is_recorded_kept_and_not_retried(proj):
    repo, state = proj

    def spawn(job):
        if job["stage"] == "writer-b01":
            def effect():
                write(repo, "llake/wiki/arch/x.md", "half\n")
                os.remove(os.path.join(job["snap"], "wiki/arch/x.md"))
            return FakeAgent(job, cls="work", effect=effect)
        p = job["pages"][0]
        return FakeAgent(job, effect=edit(repo, p, "fixed\n"), structured=status((p, "corrected")))
    bundles = [{"id": "b01", "pages": ["wiki/arch/x.md", "wiki/arch/y.md"]}]
    dispatch.run_writers(state, cfg(), brief_for("wiki/arch/x.md", "wiki/arch/y.md"), bundles, "a", "b", 1e12,
                         spawn=spawn, sleep=lambda _: None)
    x = state.pages["wiki/arch/x.md"]
    assert x["outcome"] == "writer-failed" and x["unrestored"] is True
    assert any("could not be restored" in h for h in x["history"])
    assert (repo / "llake/wiki/arch/x.md").read_text() == "half\n"
    assert "wiki/arch/x.md" in state.journal["inFlight"]
    assert state.ledger["unrestored"] == [{"stage": "writer-b01", "page": "wiki/arch/x.md"}]
    assert [s["stage"] for s in state.ledger["stages"]] == ["writer-b01", "writer-b01-s1"]
    assert state.pages["wiki/arch/y.md"]["outcome"] == "corrected"


SPAWN_BRIEF = {"themes": [{"id": "T1", "title": "Rename", "summary": "x renamed"}],
               "pages": [{"page": "wiki/arch/x.md", "severity": "major",
                          "claims": [{"quote": "old x", "head": "x is new", "severity": "major"}]}]}


def _flag(argv, name):
    return argv[argv.index(name) + 1]


def test_writer_context_writes_the_prefix_once_with_the_inputs_path(proj):
    repo, state = proj
    write(state.dir, "inputs/names.json", json.dumps({"removed": ["n{}".format(i) for i in range(151)], "added": []}))
    ctx = dispatch.WriterContext(state, full_cfg(), SPAWN_BRIEF, "2026-10-03")
    text = open(ctx.prefix_file).read()
    assert ctx.prefix_file == os.path.join(state.dir, "stages", "writer.shared.md")
    assert "llake/.state/agents/run/inputs/names.json" in text and "2026-10-03" in text
    assert ctx.patches_rel == "llake/.state/agents/run/inputs/patches"
    write(state.dir, "stages/writer.shared.md", "kept\n")
    dispatch.WriterContext(state, full_cfg(), SPAWN_BRIEF, "2026-10-04")
    assert open(ctx.prefix_file).read() == "kept\n"


def test_job_spawn_args_for_a_writer_and_a_verifier(proj):
    repo, state = proj
    c = full_cfg(verifierMode="accuracy")
    ctx = dispatch.WriterContext(state, c, SPAWN_BRIEF, "2026-10-03")
    job = {"kind": "writer", "stage": "writer-b01", "bundle": "b01", "pages": ["wiki/arch/x.md"], "retry": False,
           "snap": os.path.join(state.dir, "bundles/b01/snapshot"), "base": "a" * 40, "head": "b" * 40}
    argv, prompt, timeout = dispatch.job_spawn_args(state, c, SPAWN_BRIEF, 1e12, ctx, job)
    assert _flag(argv, "--tools") == dispatch.WRITER_TOOLS
    assert _flag(argv, "--allowedTools") == "Read,Glob,Grep,Edit(/" + str(repo / "llake/wiki/arch/x.md") + ")"
    assert _flag(argv, "--append-system-prompt-file") == ctx.prefix_file
    assert json.loads(_flag(argv, "--json-schema"))["required"] == ["pages", "otherStale"]
    assert _flag(argv, "--model") == "sonnet" and _flag(argv, "--max-budget-usd") == "3.0"
    assert "llake/wiki/arch/x.md" in prompt and "old x" in prompt and timeout == 900.0

    snapshots.snapshot(state, job["pages"], job["snap"])
    write(repo, "llake/wiki/arch/x.md", page_text("X", "x", "x is new"))
    vjob = dict(job, kind="verifier", stage="verifier-b01")
    argv, prompt, timeout = dispatch.job_spawn_args(state, c, SPAWN_BRIEF, 1e12, ctx, vjob)
    assert _flag(argv, "--tools") == dispatch.READ_TOOLS and _flag(argv, "--allowedTools") == dispatch.READ_TOOLS
    assert "--append-system-prompt-file" not in argv
    assert _flag(argv, "--max-budget-usd") == "1.0" and timeout == 600.0
    assert "+x is new" in prompt


def test_default_spawn_starts_one_agent_per_job(proj, monkeypatch):
    repo, state = proj
    made = []

    class RecAgent:
        def __init__(self, *args):
            made.append(args)

        def start(self):
            return self
    monkeypatch.setattr(dispatch, "Agent", RecAgent)
    c = full_cfg()
    spawn = dispatch.default_spawn(state, c, SPAWN_BRIEF, 1e12,
                                   dispatch.WriterContext(state, c, SPAWN_BRIEF, "2026-10-03"))
    agent = spawn({"kind": "writer", "stage": "writer-b01", "bundle": "b01", "pages": ["wiki/arch/x.md"],
                   "retry": False, "snap": "", "base": "a" * 40, "head": "b" * 40})
    assert isinstance(agent, RecAgent)
    stage, argv, prompt, cwd, out_dir, timeout, ttl, agent_id = made[0]
    assert (stage, cwd, out_dir, ttl, agent_id) == ("writer-b01", state.project, os.path.join(state.dir, "stages"),
                                                     "5m", "run_writer-b01")
