"""Run planning: kinds, failure counter, split halving, skip; journal recovery; RunState."""
from v3_helpers import commit, git, make_project, write
from ingest_v3 import plan, state
from ingest_v3.common import dump_json, load_json


def chain_repo(tmp_path, n):
    repo = make_project(tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    shas = [commit(repo, {"src/app.py": "v{}\n".format(i)}, "c{}".format(i)) for i in range(n)]
    return str(repo), str(repo / "llake"), base, shas


def test_empty_when_no_watched_changes(tmp_path):
    repo, llake, base, _ = chain_repo(tmp_path, 0)
    head = commit(repo, {"README.md": "x\n"}, "docs")
    assert plan.plan_run(repo, llake, ["src/"], base, head) == ("empty", head)
    assert plan.plan_run(repo, llake, ["src/"], base, base) == ("empty", base)


def test_gap_only_when_major_owed(tmp_path):
    repo, llake, base, _ = chain_repo(tmp_path, 0)
    head = commit(repo, {"README.md": "x\n"}, "docs")
    dump_json(llake + "/ingest-gaps.json", {"version": 1, "gaps": [
        {"page": "wiki/a/b.md", "severity": "major", "stuck": False}], "ranges": []})
    assert plan.plan_run(repo, llake, ["src/"], base, head) == ("gap-only", head)


def test_range_by_default(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 3)
    assert plan.plan_run(repo, llake, ["src/"], base, shas[-1]) == ("range", shas[-1])


def test_work_failures_count_infra_does_not(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 1)
    assert plan.record_failure(llake, base, shas[0], "infra") == {}
    assert plan.record_failure(llake, base, shas[0], "work")["count"] == 1
    f = plan.record_failure(llake, base, shas[0], "work")
    assert f == {"base": base, "count": 2, "lastHead": shas[0]}
    assert plan.record_failure(llake, base, shas[0], "infra")["count"] == 2
    assert plan.record_failure(llake, "other", shas[0], "work")["count"] == 1
    plan.clear_failures(llake)
    assert plan.load_failures(llake) == {}


def test_split_after_two_work_failures_halves_until_skip(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 4)
    head = shas[-1]
    plan.record_failure(llake, base, head, "work")
    assert plan.plan_run(repo, llake, ["src/"], base, head) == ("range", head)
    plan.record_failure(llake, base, head, "work")
    p1 = plan.plan_run(repo, llake, ["src/"], base, head)
    assert p1 == ("split", shas[1])
    plan.record_failure(llake, base, p1.head, "work")
    p2 = plan.plan_run(repo, llake, ["src/"], base, head)
    assert p2 == ("split", shas[0])
    plan.record_failure(llake, base, p2.head, "work")
    assert plan.plan_run(repo, llake, ["src/"], base, head) == ("skip", shas[0])


def test_two_commit_split_midpoint_is_strictly_older(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 2)
    for _ in range(2):
        plan.record_failure(llake, base, shas[1], "work")
    assert plan.plan_run(repo, llake, ["src/"], base, shas[1]) == ("split", shas[0])


def test_single_commit_range_skipped_after_two_work_failures(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 1)
    plan.record_failure(llake, base, shas[0], "work")
    assert plan.plan_run(repo, llake, ["src/"], base, shas[0]).kind == "range"
    plan.record_failure(llake, base, shas[0], "work")
    assert plan.plan_run(repo, llake, ["src/"], base, shas[0]) == ("skip", shas[0])


def test_first_parent_chain_newest_first(tmp_path):
    repo, llake, base, shas = chain_repo(tmp_path, 3)
    assert plan.first_parent_chain(repo, base, shas[-1]) == list(reversed(shas))


def dead_run(llake, name, journal_extra):
    run_dir = llake / ".state" / "agents" / name
    snap = run_dir / "bundles" / "b01" / "snapshot"
    write(snap, "wiki/a/x.md", "original\n")
    dump_json(str(snap / "manifest.json"), {"wiki/a/x.md": True, "wiki/a/new.md": False})
    j = state.new_journal()
    j.update({"inFlight": {"wiki/a/x.md": str(snap), "wiki/a/new.md": str(snap)}})
    j.update(journal_extra)
    dump_json(str(run_dir / "run.json"), j)
    return run_dir


def test_recovery_after_mid_write_crash(tmp_path):
    llake = tmp_path / "llake"
    write(llake, "wiki/a/x.md", "half written\n")
    write(llake, "wiki/a/new.md", "created by the dead writer\n")
    dead = dead_run(llake, "dead-run", {})
    dead_run(llake, "done-run", {"finalized": True})
    dead_run(llake, "killed-run", {"aborted": True})
    assert plan.recover_dead_runs(str(llake)) == ["dead-run"]
    assert (llake / "wiki/a/x.md").read_text() == "original\n"
    assert not (llake / "wiki/a/new.md").exists()
    j = load_json(str(dead / "run.json"))
    assert j["inFlight"] == {} and j["recovered"] is True
    assert plan.recover_dead_runs(str(llake)) == []


def test_recovery_skips_the_current_run(tmp_path):
    llake = tmp_path / "llake"
    write(llake, "wiki/a/x.md", "in progress\n")
    current = dead_run(llake, "current", {})
    assert plan.recover_dead_runs(str(llake), exclude_dir=str(current)) == []
    assert (llake / "wiki/a/x.md").read_text() == "in progress\n"


def test_run_state_roundtrip_and_ledger(tmp_path):
    s = state.RunState(tmp_path, tmp_path / "agent")
    s.record_stage({"stage": "writer-b01", "class": "none", "cost_usd": 0.5,
                    "permission_denials": [{"tool_name": "Edit", "tool_input": {"file_path": "/p/llake/x.md"}}]},
                   "writer")
    s.journal["owned"] = ["wiki/a/x.md"]
    s.save()
    again = state.RunState(tmp_path, tmp_path / "agent")
    assert again.spent == 0.5
    assert again.ledger["stages"][0]["deniedPaths"] == ["/p/llake/x.md"]
    assert again.journal["owned"] == ["wiki/a/x.md"] and again.journal["finalized"] is False
    assert again.abs("wiki/a/x.md") == str(tmp_path / "llake" / "wiki/a/x.md")


def test_recovery_without_manifest_keeps_existing_page_and_run_unrecovered(tmp_path):
    llake = tmp_path / "llake"
    write(llake, "wiki/a/x.md", "precious\n")
    dead = dead_run(llake, "dead-run", {})
    (dead / "bundles" / "b01" / "snapshot" / "manifest.json").unlink()
    assert plan.recover_dead_runs(str(llake)) == []
    assert (llake / "wiki/a/x.md").read_text() == "precious\n"
    j = load_json(str(dead / "run.json"))
    assert not j.get("recovered") and "wiki/a/x.md" in j["inFlight"]


def test_recovery_with_missing_snapshot_copy_leaves_page_and_run_unrecovered(tmp_path):
    llake = tmp_path / "llake"
    write(llake, "wiki/a/x.md", "half written\n")
    dead = dead_run(llake, "dead-run", {})
    (dead / "bundles" / "b01" / "snapshot" / "wiki/a/x.md").unlink()
    assert plan.recover_dead_runs(str(llake)) == []
    assert (llake / "wiki/a/x.md").read_text() == "half written\n"
    j = load_json(str(dead / "run.json"))
    assert not j.get("recovered") and "wiki/a/x.md" in j["inFlight"]
    assert "wiki/a/x.md" in j["unrecovered"]
