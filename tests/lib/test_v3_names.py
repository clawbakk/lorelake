"""Removed/added names, hit index, anchor and literal leads."""
import json

from v3_helpers import commit, git, make_project, page_text, write
from ingest_v3 import names

BASE_SRC = (
    "def fetchUserData():\n"
    "    MAX_RETRY = 3\n"
    "    cfg = load('config.retry.limit')\n"
    "    keep_me = 1\n"
    "    log.error(\"Failed to connect to the upstream server\")\n"
    "    abc = Something\n"
    + "".join("    filler_{} = {}\n".format(i, i) for i in range(10))
)
HEAD_SRC = (
    "def loadProfile():\n"
    "    MAX_ATTEMPTS = 3\n"
    "    cfg = load('config.attempts')\n"
    "    keep_me = 1\n"
    "    log.warn(\"Upstream unavailable, retrying soon\")\n"
    "    abc = Something\n"
    + "".join("    filler_{} = {}\n".format(i, i) for i in range(10))
)


def setup(tmp_path, pages=None, extra_src=None):
    src = {"src/app.py": BASE_SRC, "src/old_helper.py": "x = 1\n"}
    src.update(extra_src or {})
    repo = make_project(tmp_path, src=src, pages=pages)
    base = git(repo, "rev-parse", "HEAD").strip()
    head = commit(repo, {"src/app.py": HEAD_SRC}, "rewrite", remove=["src/old_helper.py"])
    return repo, base, head


def test_removed_and_added_names(tmp_path):
    repo, base, head = setup(tmp_path)
    n = names.derive(str(repo), base, head, ["src/"])
    for name in ("fetchUserData", "MAX_RETRY", "config.retry.limit", "old_helper.py"):
        assert name in n["removed"], name
    assert "old_helper.py" in n["removed_files"]
    for name in ("Something", "abc", "keep_me"):
        assert name not in n["removed"], name
    assert "loadProfile" in n["added"] and "MAX_ATTEMPTS" in n["added"]


def test_name_that_only_shrank_is_dropped(tmp_path):
    repo = make_project(tmp_path, src={"src/a.py": "use(shared_name)\nuse(shared_name)\n"})
    base = git(repo, "rev-parse", "HEAD").strip()
    head = commit(repo, {"src/a.py": "use(shared_name)\n"}, "shrink")
    assert "shared_name" not in names.derive(str(repo), base, head, ["src/"])["removed"]


def test_hit_index_caps_lines_and_skips_discussions(tmp_path):
    body = "\n".join("Line {} calls fetchUserData here.".format(i) for i in range(25))
    pages = {"arch/client.md": page_text("Client", "client", body),
             "discussions/2026-01-01-x.md": page_text("D", "d", "fetchUserData was discussed")}
    repo, base, head = setup(tmp_path, pages=pages)
    llake = str(repo / "llake")
    hits = names.wiki_hits(llake, names.derive(str(repo), base, head, ["src/"])["removed"])
    assert list(hits) == ["wiki/arch/client.md"]
    assert len(hits["wiki/arch/client.md"]["fetchUserData"]) == 25
    md = names.hit_index_md(hits, cap=20)
    assert "`fetchUserData` ×25" in md and "+5" in md


def test_anchor_index_rewritten_line_not_shifted(tmp_path):
    pages = {"arch/client.md": page_text("Client", "client",
                                         "Retries live at `src/app.py:2`.\n\nFiller is at `src/app.py:12`.")}
    repo, base, head = setup(tmp_path, pages=pages)
    ranges = names.changed_ranges(str(repo), base, head, ["src/"])
    hits = names.anchor_hits(str(repo / "llake"), ranges)
    cited = [a for _, a in hits["wiki/arch/client.md"]]
    assert any(a.startswith("src/app.py:2") for a in cited)
    assert not any(a.startswith("src/app.py:12") for a in cited)


def test_literal_index(tmp_path):
    pages = {"playbook/outage.md": page_text("Outage", "o", 'Look for "Failed to connect to the upstream server" in logs.')}
    repo, base, head = setup(tmp_path, pages=pages)
    n = names.derive(str(repo), base, head, ["src/"])
    lit = "Failed to connect to the upstream server"
    assert n["literals"][lit] == "removed"
    lh = names.literal_hits(str(repo / "llake"), n["literals"])
    assert lit in lh["wiki/playbook/outage.md"]
    assert "(removed)" in names.literal_index_md(lh, n["literals"], n["literal_lines"])


def test_non_utf8_source_does_not_crash(tmp_path):
    repo = make_project(tmp_path, src={"src/legacy.txt": b"caf\xe9 oldValueName\n",
                                       "src/blob.dat": b"\xff\xfe\x00 binary \x80\n"})
    base = git(repo, "rev-parse", "HEAD").strip()
    head = commit(repo, {"src/legacy.txt": b"caf\xe9 newValueName\n",
                         "src/blob.dat": b"\xff\xfe\x00 binary \x81\n"}, "latin-1 and binary")
    n = names.derive(str(repo), base, head, ["src/"])
    assert "oldValueName" in n["removed"] and "newValueName" in n["added"]


def test_hit_leads():
    hits = {"wiki/a/x.md": {"oldName": [3, 9]}}
    assert names.hit_leads(hits) == ["wiki/a/x.md: `oldName` L3,9"]
    assert names.hit_leads({}) == ["(no wiki page names a removed name in this range)"]


def test_write_inputs_and_empty_inputs(tmp_path):
    repo, base, head = setup(tmp_path, pages={"arch/client.md": page_text("C", "c", "fetchUserData")})
    out = tmp_path / "inputs"
    summary = names.write_inputs(str(repo), base, head, ["src/"], str(repo / "llake"), str(out))
    for f in ("names.json", "hits.json", "hit-index.md", "anchor-hits.json", "anchor-index.md",
              "literal-hits.json", "literal-index.md"):
        assert (out / f).exists(), f
    assert summary["hit_pages"] == 1
    empty = tmp_path / "empty"
    names.write_empty_inputs(str(empty))
    assert json.loads((empty / "names.json").read_text())["removed"] == []
    assert (empty / "patches").is_dir()
