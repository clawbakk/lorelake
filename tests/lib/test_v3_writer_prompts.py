"""Anchor rule and the writer/verifier prompts."""
import pytest

from v3_helpers import make_project, page_text
from ingest_v3 import anchors, prompts

SRC = "def loadProfile():\n    return 1\n\n\nclass Cache:\n    pass\n"


@pytest.fixture
def proj(tmp_path):
    repo = make_project(tmp_path, src={"src/app.py": SRC},
                        pages={"arch/client.md": page_text("Client", "The client",
                                                           "`fetchUserData` is at `src/app.py:1`.\n\n"
                                                           "`loadProfile` is at `src/app.py:1`.")})
    return repo


def test_check_anchors(proj):
    src = anchors.Source(str(proj))
    assert anchors.check_anchors(src, ["`loadProfile` lives at `src/app.py:1`."]) == []
    assert anchors.check_anchors(src, ["`Cache` is declared at `src/app.py:5-6`."]) == []
    assert "names no file" in anchors.check_anchors(src, ["`x1` at `src/missing.py:3`"])[0][1]
    assert "outside the file" in anchors.check_anchors(src, ["`loadProfile` at `src/app.py:99`"])[0][1]
    assert "shows none of" in anchors.check_anchors(src, ["`fetchUserData` at `src/app.py:1`"])[0][1]


def test_broken_anchors_puts_quoted_lines_first_and_caps(proj):
    src = anchors.Source(str(proj))
    text = "\n".join(["`zzzOne` at `src/app.py:1`", "`zzzTwo` at `src/app.py:1`"])
    out = anchors.broken_anchors(src, text, quotes=["`zzzTwo` at"], cap=1)
    assert len(out) == 1 and out[0].startswith("L2:")


def test_resolve_guards(tmp_path):
    repo = make_project(tmp_path, src={"src/new/client.py": SRC, "a/main.py": SRC, "b/main.py": SRC,
                                       "src/solo.py": SRC})
    src = anchors.Source(str(repo))
    assert src.resolve("src/old/client.py") is None          # moved directory is not guessed
    assert src.resolve("./a/main.py") == "a/main.py"          # leading ./ stripped
    assert src.resolve("main.py") is None                     # ambiguous basename
    assert src.resolve("solo.py") == "src/solo.py"            # bare name, unique
    assert anchors.check_anchors(src, ["`loadProfile` at `src/old/client.py:1`"])[0][1].find("names no file") >= 0
    assert anchors.check_anchors(src, ["`loadProfile` at `./a/main.py:1`"]) == []


def brief(create=False):
    page = {"page": "wiki/arch/client.md", "severity": "major", "kind": "direct", "themes": ["T1"],
            "reason": "renamed loader", "claims": [{"quote": "`fetchUserData` is at", "head": "src/app.py:1",
                                                    "severity": "major", "quoteFound": True}],
            "newFacts": ["loadProfile returns 1 (src/app.py:2)"], "evidence": ["src/app.py"],
            "hits": {"fetchUserData": [11]}, "carried": True, "since": "abcdef123", "attempts": 1,
            "create": False}
    pages = [page]
    if create:
        pages.append({"page": "wiki/arch/limits.md", "severity": "minor", "claims": [], "create": True,
                      "title": "Limits", "description": "Retry limits", "newFacts": []})
    return {"themes": [{"id": "T1", "title": "Loader renamed", "summary": "fetchUserData became loadProfile"}],
            "pages": pages}


def test_shared_prefix(proj):
    text = prompts.shared_prefix(str(proj / "llake"), brief(create=True),
                                 {"removed": ["fetchUserData"], "added": ["loadProfile"]}, "edit", "2026-10-02", "r")
    assert "**T1 Loader renamed**" in text
    assert "`fetchUserData`" in text and "`loadProfile`" in text
    assert "> **Superseded 2026-10-02:**" in text and "deprecated" in text
    assert "(planned new page) Retry limits" in text
    assert prompts.MODE_RULES["edit"] in text
    assert prompts.MODE_RULES["write"] in prompts.shared_prefix(str(proj / "llake"), brief(), {}, "write", "d", "r")
    with pytest.raises(ValueError):
        prompts.shared_prefix(str(proj / "llake"), brief(), {}, "rewrite", "d", "r")


def test_page_block(proj):
    src = anchors.Source(str(proj))
    block = prompts.page_block(brief()["pages"][0], str(proj / "llake"), src,
                               {"wiki/arch/client.md": [[11, "src/app.py:1"]]},
                               {"wiki/arch/client.md": {"Failed to connect upstream": [12]}},
                               findings=[{"quote": "x", "head": "h", "severity": "minor", "source": "check:anchor"}])
    assert '1. [major] page says "`fetchUserData` is at" — head: src/app.py:1' in block
    assert "Carried from an earlier run (since `abcdef1`, 1 failed attempts)" in block
    assert "- loadProfile returns 1 (src/app.py:2)" in block
    assert "`fetchUserData` L11" in block
    assert "L11 → `src/app.py:1`" in block
    assert '"Failed to connect upstream" L12' in block
    assert "already broken at head" in block and "shows none of" in block
    assert "[minor, check:anchor]" in block


def test_bundle_prompt(proj):
    job = {"pages": ["wiki/arch/client.md"], "base": "a" * 40, "head": "b" * 40}
    text = prompts.bundle_prompt(job, brief(), str(proj / "llake"), None, {}, {}, "llake/.state/x/inputs/patches")
    assert "# Your task: bring your pages up to date" in text
    assert "- `llake/wiki/arch/client.md`" in text and "`aaaaaaa..bbbbbbb`" in text
    fix = prompts.bundle_prompt(job, brief(), str(proj / "llake"), None, {}, {}, "p", fixer=True)
    assert "fix what was flagged" in fix


def test_verifier_prompt_modes(proj):
    job = {"pages": ["wiki/arch/client.md"], "base": "a" * 40, "head": "b" * 40}
    diffs = {"wiki/arch/client.md": "-old\n+new"}
    acc = prompts.verifier_prompt(job, brief(), diffs, "p", "accuracy")
    assert "Return `residual` as an empty list." in acc and "```diff\n-old\n+new\n```" in acc
    both = prompts.verifier_prompt(job, brief(), diffs, "p", "accuracy+residual")
    assert "**residual**" in both
    with pytest.raises(ValueError):
        prompts.verifier_prompt(job, brief(), diffs, "p", "off")


def test_shared_prefix_overflow_points_at_inputs_relative_path(proj):
    names = {"removed": ["n{}".format(i) for i in range(151)], "added": []}
    text = prompts.shared_prefix(str(proj / "llake"), brief(), names, "edit", "d", "llake/.state/x/inputs")
    assert "full list in `llake/.state/x/inputs/names.json`" in text
    with pytest.raises(TypeError):
        prompts.shared_prefix(str(proj / "llake"), brief(), names, "edit", "d")
    assert "concisely" not in text
