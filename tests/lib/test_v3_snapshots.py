"""Snapshots, attribution, out-of-surface reverts (llake/ only), revert-run."""
import json
import subprocess
import sys

import pytest

from v3_helpers import REPO_ROOT, make_project, page_text, write
from ingest_v3 import snapshots
from ingest_v3.state import RunState


@pytest.fixture
def run(tmp_path):
    repo = make_project(tmp_path, pages={"arch/x.md": page_text("X", "x", "original x"),
                                         "arch/z.md": page_text("Z", "z", "original z")})
    write(repo, "llake/.state/hooks.log", "log\n")
    state = RunState(repo, repo / "llake" / ".state" / "agents" / "run-1")
    snapshots.take_pre(state)
    return repo, state


def test_take_pre_excludes_state(run):
    repo, state = run
    manifest = json.loads((repo / "llake/.state/agents/run-1/pre-manifest.json").read_text())
    assert "wiki/arch/x.md" in manifest and "last-ingest-sha" in manifest
    assert not any(k.startswith(".state") for k in manifest)
    assert not (repo / "llake/.state/agents/run-1/pre/.state").exists()


def test_snapshot_and_revert(run):
    repo, state = run
    dest = state.dir + "/bundles/b01/snapshot"
    snapshots.snapshot(state, ["wiki/arch/x.md", "wiki/arch/new.md"], dest)
    assert set(state.journal["inFlight"]) == {"wiki/arch/x.md", "wiki/arch/new.md"}
    assert "wiki/arch/x.md" in state.journal["owned"]
    write(repo, "llake/wiki/arch/x.md", "changed\n")
    write(repo, "llake/wiki/arch/new.md", "created\n")
    assert snapshots.changed_since(state, "wiki/arch/x.md", dest)
    assert "+changed" in snapshots.page_diff(state, "wiki/arch/x.md", dest)
    snapshots.revert(state, ["wiki/arch/x.md", "wiki/arch/new.md"], dest)
    assert "original x" in (repo / "llake/wiki/arch/x.md").read_text()
    assert not (repo / "llake/wiki/arch/new.md").exists()
    assert state.journal["inFlight"] == {}


def test_settle_keeps_edits(run):
    repo, state = run
    dest = state.dir + "/s"
    snapshots.snapshot(state, ["wiki/arch/x.md"], dest)
    write(repo, "llake/wiki/arch/x.md", "edited\n")
    snapshots.settle(state, ["wiki/arch/x.md"])
    assert state.journal["inFlight"] == {} and (repo / "llake/wiki/arch/x.md").read_text() == "edited\n"


def test_out_of_surface_classification(run):
    repo, state = run
    brief_dir = state.dir + "/brief"
    summary = {"writes": [
        {"tool": "Edit", "file_path": str(repo / "llake/wiki/arch/x.md")},
        {"tool": "Write", "file_path": str(repo / "llake/wiki/arch/z.md")},
        {"tool": "Write", "file_path": brief_dir + "/themes.json"},
        {"tool": "Write", "file_path": "src/rogue.py"},
    ]}
    inside, outside = snapshots.out_of_surface(state, summary, ["wiki/arch/x.md"], allowed_dirs=[brief_dir])
    assert inside == ["wiki/arch/z.md"]
    assert outside == [str(repo / "src/rogue.py")]


def test_out_of_surface_write_under_llake_is_reverted(run):
    repo, state = run
    write(repo, "llake/wiki/arch/z.md", "tampered\n")
    write(repo, "llake/wiki/arch/stray.md", "stray\n")
    actions = snapshots.revert_out_of_surface(state, "writer-b01", ["wiki/arch/z.md", "wiki/arch/stray.md",
                                                                    "wiki/arch/x.md"], [])
    assert "original z" in (repo / "llake/wiki/arch/z.md").read_text()
    assert not (repo / "llake/wiki/arch/stray.md").exists()
    assert [a["action"] for a in actions] == ["reverted", "reverted"]
    assert state.ledger["surface"] == actions


def test_write_outside_llake_is_reported_never_touched(run):
    repo, state = run
    write(repo, "src/rogue.py", "user source\n")
    actions = snapshots.revert_out_of_surface(state, "writer-b01", [], [str(repo / "src/rogue.py")])
    assert (repo / "src/rogue.py").read_text() == "user source\n"
    assert actions == [{"stage": "writer-b01", "path": str(repo / "src/rogue.py"),
                        "action": "reported-outside-llake"}]


def test_restore_refuses_paths_outside_llake(run):
    repo, state = run
    with pytest.raises(ValueError):
        snapshots._restore_llake(state, "../src/app.py", None)
    assert (repo / "src/app.py").exists()


def test_revert_run_restores_run_writes_and_strips_its_log_entry(run):
    repo, state = run
    snapshots.snapshot(state, ["wiki/arch/x.md"], state.dir + "/s")
    snapshots.settle(state, ["wiki/arch/x.md"])
    write(repo, "llake/wiki/arch/x.md", "writer edit\n")
    write(repo, "llake/ingest-gaps.json", "{}\n")
    write(repo, "llake/last-ingest-sha", "newsha\n")
    entry = "\n## [2026-10-02] ingest | a..b: v3 — 1 updated\n"
    state.journal["finalizeWrites"] = ["wiki/arch/x.md", "ingest-gaps.json", "last-ingest-sha"]
    state.journal["logEntry"] = entry
    state.save()
    log = repo / "llake/log.md"
    log.write_text(log.read_text() + entry + "\n## [2026-10-02] session-capture | concurrent\n")
    res = snapshots.revert_run(str(repo), state.dir)
    assert res["skipped"] is False
    assert "original x" in (repo / "llake/wiki/arch/x.md").read_text()
    assert not (repo / "llake/ingest-gaps.json").exists()
    assert (repo / "llake/last-ingest-sha").read_text() != "newsha\n"
    text = log.read_text()
    assert "v3 — 1 updated" not in text and "session-capture | concurrent" in text
    assert RunState(repo, state.dir).journal["aborted"] is True
    assert snapshots.revert_run(str(repo), state.dir)["skipped"] is True


def test_revert_run_is_noop_once_finalized(run):
    repo, state = run
    snapshots.snapshot(state, ["wiki/arch/x.md"], state.dir + "/s")
    write(repo, "llake/wiki/arch/x.md", "final\n")
    state.journal["finalized"] = True
    state.save()
    assert snapshots.revert_run(str(repo), state.dir)["skipped"] is True
    assert (repo / "llake/wiki/arch/x.md").read_text() == "final\n"


def test_cli_revert_run(run):
    repo, state = run
    out = subprocess.run([sys.executable, str(REPO_ROOT / "hooks/lib/ingest-v3.py"), "revert-run",
                          "--project-root", str(repo), "--agent-dir", state.dir], capture_output=True, text=True)
    assert out.returncode == 0 and json.loads(out.stdout)["skipped"] is False


def test_manifest_records_false_for_missing_page_and_precedes_journal(run):
    repo, state = run
    dest = state.dir + "/bundles/b02/snapshot"
    seen = {}
    real_save = state.save

    def spy_save():
        seen["manifest_on_disk"] = json.loads((repo / dest / "manifest.json").read_text()) \
            if (repo / dest / "manifest.json").exists() else None
        seen["copy_on_disk"] = (repo / dest / "wiki/arch/x.md").exists()
        real_save()

    state.save = spy_save
    snapshots.snapshot(state, ["wiki/arch/x.md", "wiki/arch/new.md"], dest)
    assert seen["manifest_on_disk"] == {"wiki/arch/x.md": True, "wiki/arch/new.md": False}
    assert seen["copy_on_disk"] is True


def test_failure_between_manifest_and_journal_leaves_no_inflight_entry(run):
    repo, state = run
    dest = state.dir + "/bundles/b03/snapshot"

    def boom():
        raise OSError("disk full")

    state.save = boom
    with pytest.raises(OSError):
        snapshots.snapshot(state, ["wiki/arch/x.md", "wiki/arch/new.md"], dest)
    assert json.loads((repo / dest / "manifest.json").read_text())["wiki/arch/new.md"] is False
    on_disk = RunState(repo, state.dir)
    assert on_disk.journal["inFlight"] == {}


# ---- fix round 1: fail-closed restores, denied attempts, changed-only revert ----

def test_restore_page_without_bundle_manifest_keeps_existing_page(run):
    repo, state = run
    dest = state.dir + "/nomanifest"
    (repo / dest).mkdir(parents=True)
    assert snapshots.restore_page(state, "wiki/arch/x.md", dest) == "unrestored"
    assert "original x" in (repo / "llake/wiki/arch/x.md").read_text()


def test_restore_page_manifest_true_but_copy_missing_keeps_page(run):
    repo, state = run
    dest = state.dir + "/s"
    snapshots.snapshot(state, ["wiki/arch/x.md"], dest)
    (repo / dest / "wiki/arch/x.md").unlink()
    write(repo, "llake/wiki/arch/x.md", "later\n")
    snapshots.revert(state, ["wiki/arch/x.md"], dest)
    assert (repo / "llake/wiki/arch/x.md").read_text() == "later\n"
    assert "wiki/arch/x.md" in state.journal["inFlight"]


def test_revert_run_pre_copy_missing_keeps_page(run):
    """pre/ is the source only for a path with no snapshot of its own (here a finalize write journaled
    without a finalize snapshot); a missing pre/ copy is no evidence of absence."""
    repo, state = run
    state.journal["finalizeWrites"] = ["wiki/arch/x.md"]
    state.save()
    (repo / state.dir / "pre/wiki/arch/x.md").unlink()
    write(repo, "llake/wiki/arch/x.md", "edit\n")
    res = snapshots.revert_run(str(repo), state.dir)
    assert (repo / "llake/wiki/arch/x.md").read_text() == "edit\n"
    assert "wiki/arch/x.md" in res["unrestored"]
    assert RunState(repo, state.dir).journal["aborted"] is False


def test_out_of_surface_revert_without_pre_manifest_only_reports(run):
    repo, state = run
    (repo / state.dir / "pre-manifest.json").unlink()
    write(repo, "llake/wiki/arch/z.md", "tampered\n")
    actions = snapshots.revert_out_of_surface(state, "w", ["wiki/arch/z.md"], [])
    assert (repo / "llake/wiki/arch/z.md").read_text() == "tampered\n"
    assert [a["action"] for a in actions] == ["reported"]


def test_revert_run_without_pre_manifest_restores_from_bundle_snapshot_or_stays_open(run):
    repo, state = run
    snapshots.snapshot(state, ["wiki/arch/x.md"], state.dir + "/s")
    snapshots.snapshot(state, ["wiki/arch/z.md"], state.dir + "/t")
    (repo / state.dir / "t/wiki/arch/z.md").unlink()
    (repo / state.dir / "pre-manifest.json").unlink()
    write(repo, "llake/wiki/arch/x.md", "edit\n")
    write(repo, "llake/wiki/arch/z.md", "edit z\n")
    res = snapshots.revert_run(str(repo), state.dir)
    assert "original x" in (repo / "llake/wiki/arch/x.md").read_text()
    assert (repo / "llake/wiki/arch/z.md").read_text() == "edit z\n"
    j = RunState(repo, state.dir).journal
    assert j["aborted"] is False and list(j["inFlight"]) == ["wiki/arch/z.md"]
    assert res["unrestored"] == ["wiki/arch/z.md"]


def test_take_pre_manifest_reflects_the_copy_not_the_live_tree(tmp_path, monkeypatch):
    repo = make_project(tmp_path, pages={"arch/x.md": page_text("X", "x", "x")})
    state = RunState(repo, repo / "llake" / ".state" / "agents" / "run-1")
    real = snapshots.shutil.copytree

    def racing(*a, **k):
        out = real(*a, **k)
        write(repo, "llake/wiki/arch/late.md", "late\n")
        return out

    monkeypatch.setattr(snapshots.shutil, "copytree", racing)
    snapshots.take_pre(state)
    manifest = json.loads((repo / "llake/.state/agents/run-1/pre-manifest.json").read_text())
    assert "wiki/arch/late.md" not in manifest


def test_denied_attempt_and_owned_pages_are_not_attributed(run):
    repo, state = run
    state.journal["owned"] = ["wiki/arch/z.md"]
    summary = {"writes": [
        {"tool": "Edit", "file_path": str(repo / "llake/wiki/arch/z.md")},
        {"tool": "Edit", "file_path": str(repo / "llake/wiki/arch/other.md")},
        {"tool": "Write", "file_path": str(repo / "src/denied.py")},
    ], "permission_denials": [
        {"tool_input": {"file_path": str(repo / "llake/wiki/arch/other.md")}},
        {"tool_input": {"file_path": str(repo / "src/denied.py")}},
    ]}
    assert snapshots.out_of_surface(state, summary, []) == ([], [])


def test_revert_run_restores_changed_not_merely_owned(run):
    repo, state = run
    snapshots.snapshot(state, ["wiki/arch/x.md", "wiki/arch/z.md"], state.dir + "/s")
    write(repo, "llake/wiki/arch/x.md", "writer edit\n")
    snapshots.settle(state, ["wiki/arch/x.md", "wiki/arch/z.md"])
    assert state.journal["changed"] == ["wiki/arch/x.md"]
    write(repo, "llake/wiki/arch/z.md", "capture edit\n")
    res = snapshots.revert_run(str(repo), state.dir)
    assert "original x" in (repo / "llake/wiki/arch/x.md").read_text()
    assert (repo / "llake/wiki/arch/z.md").read_text() == "capture edit\n"
    assert res["unrestored"] == []


def test_revert_run_restores_each_page_from_its_own_snapshot_keeping_concurrent_capture_writes(run):
    """A capture write that landed after the pre-run copy but before the writer's snapshot is not the
    run's write: the kill revert restores the bundle snapshot, never pre/ (design §12 deviation 1)."""
    repo, state = run
    write(repo, "llake/wiki/arch/x.md", "capture edit of x\n")
    write(repo, "llake/wiki/arch/new.md", "capture created new\n")
    dest = state.dir + "/bundles/b01/snapshot"
    snapshots.snapshot(state, ["wiki/arch/x.md", "wiki/arch/new.md"], dest)
    write(repo, "llake/wiki/arch/x.md", "writer edit of x\n")
    write(repo, "llake/wiki/arch/new.md", "writer edit of new\n")
    snapshots.settle(state, ["wiki/arch/x.md", "wiki/arch/new.md"])
    res = snapshots.revert_run(str(repo), state.dir)
    assert (repo / "llake/wiki/arch/x.md").read_text() == "capture edit of x\n"
    assert (repo / "llake/wiki/arch/new.md").read_text() == "capture created new\n"
    assert res["unrestored"] == [] and RunState(repo, state.dir).journal["aborted"] is True


def test_settle_keeps_the_first_snapshot_dir_of_a_changed_page(run):
    """A fixer's later settle must not move a page's revert point to its post-write state."""
    repo, state = run
    snapshots.snapshot(state, ["wiki/arch/x.md"], state.dir + "/s")
    write(repo, "llake/wiki/arch/x.md", "writer edit\n")
    snapshots.settle(state, ["wiki/arch/x.md"])
    snapshots.snapshot(state, ["wiki/arch/x.md"], state.dir + "/fix")
    write(repo, "llake/wiki/arch/x.md", "fixer edit\n")
    snapshots.settle(state, ["wiki/arch/x.md"])
    assert state.journal["changedFrom"] == {"wiki/arch/x.md": state.dir + "/s"}
    snapshots.revert_run(str(repo), state.dir)
    assert "original x" in (repo / "llake/wiki/arch/x.md").read_text()


def test_revert_run_of_a_fixer_in_flight_undoes_the_writer_edit_too(run):
    repo, state = run
    snapshots.snapshot(state, ["wiki/arch/x.md"], state.dir + "/s")
    write(repo, "llake/wiki/arch/x.md", "writer edit\n")
    snapshots.settle(state, ["wiki/arch/x.md"])
    snapshots.snapshot(state, ["wiki/arch/x.md"], state.dir + "/fix")
    write(repo, "llake/wiki/arch/x.md", "fixer half-edit\n")
    res = snapshots.revert_run(str(repo), state.dir)
    assert "original x" in (repo / "llake/wiki/arch/x.md").read_text()
    assert res["unrestored"] == [] and RunState(repo, state.dir).journal["inFlight"] == {}


def test_revert_run_finalize_write_restores_its_finalize_snapshot_keeping_a_capture_index_row(run):
    from ingest_v3 import finalize
    repo, state = run
    idx = "wiki/arch/arch.md"
    with_row = (repo / "llake" / idx).read_text() + "| [[decision]] | added by capture |\n"
    write(repo, "llake/" + idx, with_row)
    finalize._will_write(state)(idx)
    write(repo, "llake/" + idx, with_row.replace("| x |", "| x rewritten |"))
    finalize._will_write(state)("ingest-gaps.json")
    write(repo, "llake/ingest-gaps.json", "{}\n")
    res = snapshots.revert_run(str(repo), state.dir)
    assert (repo / "llake" / idx).read_text() == with_row
    assert not (repo / "llake/ingest-gaps.json").exists()
    assert res["unrestored"] == []
