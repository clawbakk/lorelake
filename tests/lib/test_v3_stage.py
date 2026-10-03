"""Staging and the analysis/recall prompts."""
import json
import os

from v3_helpers import commit, git, make_project, page_text
from ingest_v3 import names, stage
from ingest_v3.common import dump_json


def project(tmp_path):
    pages = {"arch/client.md": page_text("Client", "The HTTP client", "Calls fetchUserData.")}
    repo = make_project(tmp_path, src={"src/app.py": "def fetchUserData():\n    pass\n",
                                       "src/my file.py": "a = 1\n"}, pages=pages)
    base = git(repo, "rev-parse", "HEAD").strip()
    head = commit(repo, {"src/app.py": "def loadProfile():\n    pass\n", "src/my file.py": "a = 2\n"},
                  "rename the loader")
    return repo, base, head


def test_stage_patches_split_at_hunks(tmp_path):
    big = "".join("line {}\n".format(i) for i in range(4000))
    repo = make_project(tmp_path, src={"src/big.txt": big})
    base = git(repo, "rev-parse", "HEAD").strip()
    changed = "".join(("CHANGED {}\n" if i % 100 == 0 else "line {}\n").format(i) for i in range(4000))
    head = commit(repo, {"src/big.txt": changed}, "many hunks")
    out = stage.stage_patches(str(repo), base, head, ["src/"], str(tmp_path / "p"), max_bytes=2000)
    assert len(out) > 1 and all(n.startswith("src__big.txt.patch.part") for n in out)
    for n in out[1:]:
        assert (tmp_path / "p" / n).read_text().startswith("@@")


def test_stage_inputs_handles_spaces_and_writes_files(tmp_path):
    repo, base, head = project(tmp_path)
    agent = tmp_path / "agent"
    staged = stage.stage_inputs(str(repo), base, head, ["src/"], str(repo / "llake"), str(agent))
    assert "src__my file.py.patch" in staged["patches"]
    assert "rename the loader" in (agent / "inputs" / "commits.md").read_text()
    assert "wiki/arch/client.md" in (agent / "inputs" / "catalog.md").read_text()
    assert (agent / "brief" / "pages").is_dir()


def test_analysis_prompt_renders_inputs(tmp_path):
    repo, base, head = project(tmp_path)
    llake, agent = str(repo / "llake"), tmp_path / "agent"
    names.write_inputs(str(repo), base, head, ["src/"], llake, str(agent / "inputs"))
    staged = stage.stage_inputs(str(repo), base, head, ["src/"], llake, str(agent))
    dump_json(os.path.join(llake, "ingest-gaps.json"), {"version": 1, "gaps": [
        {"page": "wiki/arch/client.md", "severity": "major", "cause": "declared", "stuck": False, "claims": []}],
        "ranges": []})
    prompt = stage.analysis_prompt(str(repo), llake, str(agent), base, head, ["src/"], staged)
    assert str(agent / "brief") in prompt
    assert "`fetchUserData`" in prompt
    assert "| wiki/arch/client.md | state |" in prompt
    assert "llake/wiki/arch/client.md` (major, declared" in prompt
    assert (agent / "analysis.prompt.md").read_text() == prompt


def test_recall_prompt_lists_brief_and_considered(tmp_path):
    repo, base, head = project(tmp_path)
    llake, agent = str(repo / "llake"), tmp_path / "agent"
    names.write_inputs(str(repo), base, head, ["src/"], llake, str(agent / "inputs"))
    (agent / "brief" / "pages").mkdir(parents=True)
    (agent / "brief" / "themes.json").write_text(json.dumps([{"id": "T1", "title": "Loader renamed", "summary": "s"}]))
    (agent / "brief" / "pages" / "batch-1.json").write_text(json.dumps([
        {"path": "llake/wiki/arch/client.md", "severity": "major", "reason": "names the loader", "stale": []}]))
    (agent / "brief" / "considered.json").write_text(json.dumps([{"path": "llake/wiki/arch/other.md",
                                                                 "reason": "still true"}]))
    prompt = stage.recall_prompt(str(repo), llake, str(agent))
    assert "T1: Loader renamed" in prompt
    assert "`llake/wiki/arch/client.md`: names the loader" in prompt
    assert "`llake/wiki/arch/other.md`: still true" in prompt
    assert str(agent / "brief") in prompt


def test_stage_inputs_survives_non_utf8_and_binary_sources(tmp_path):
    repo = make_project(tmp_path, src={"src/a.py": "x = 1\n"})
    base = git(repo, "rev-parse", "HEAD").strip()
    head = commit(repo, {"src/a.py": b"x = '\xe9\xff'\n", "src/blob.bin": b"\x00\x01\xfe\xff\x00"}, "odd bytes")
    agent = tmp_path / "agent"
    staged = stage.stage_inputs(str(repo), base, head, ["src/"], str(repo / "llake"), str(agent))
    assert "src__a.py.patch" in staged["patches"]
    assert "src__blob.bin.patch" in staged["patches"]
    assert "x = " in (agent / "inputs" / "patches" / "src__a.py.patch").read_text(encoding="utf-8")
