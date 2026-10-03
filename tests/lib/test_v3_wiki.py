"""Page model (flat and nested), catalog, atomic I/O."""
import os

import pytest

from v3_helpers import page_text, write  # noqa: F401  (sets sys.path)
from ingest_v3 import common, wiki


@pytest.mark.parametrize("raw,expected", [
    ("llake/wiki/arch/tick-loop.md", "wiki/arch/tick-loop.md"),
    ("wiki/arch/tick-loop.md", "wiki/arch/tick-loop.md"),
    ("arch/tick-loop", "wiki/arch/tick-loop.md"),
    ("`llake/wiki/packages/core/runtime.md`", "wiki/packages/core/runtime.md"),
    ("/abs/proj/llake/wiki/arch/x.md", "wiki/arch/x.md"),
    ("llake/index.md", None),
    ("wiki/x.md", None),
    ("wiki/arch/../secrets.md", None),
    ("wiki/.hidden/x.md", None),
])
def test_norm_page(raw, expected):
    assert wiki.norm_page(raw) == expected


@pytest.mark.parametrize("page,cls", [
    ("wiki/arch/tick-loop.md", "state"),
    ("wiki/arch/arch.md", "index"),
    ("wiki/decisions/adr-x.md", "record"),
    ("wiki/decisions/decisions.md", "index"),
    ("wiki/gotchas/g.md", "gotcha"),
    ("wiki/playbook/p.md", "state"),
    ("wiki/discussions/2026-01-01-x.md", "excluded"),
    ("wiki/discussions/discussions.md", "excluded"),
    ("wiki/packages/packages.md", "index"),
    ("wiki/packages/core/core.md", "index"),
    ("wiki/packages/core/runtime.md", "state"),
])
def test_page_class(page, cls):
    assert wiki.page_class(page) == cls


def test_index_of_and_category_dir():
    assert wiki.index_of("wiki/packages/core/runtime.md") == "wiki/packages/core/core.md"
    assert wiki.category_dir("wiki/arch/x.md") == "wiki/arch"
    assert wiki.slug("wiki/packages/core/runtime.md") == "runtime"


def test_wiki_pages_recursive_and_sorted(tmp_path):
    for rel in ("wiki/b/b.md", "wiki/b/x.md", "wiki/a/a.md", "wiki/p/core/core.md", "wiki/p/core/y.md",
                "wiki/a/.x.md.tmp", "wiki/a/notes.txt"):
        write(tmp_path, rel, "x")
    assert wiki.wiki_pages(str(tmp_path)) == [
        "wiki/a/a.md", "wiki/b/b.md", "wiki/b/x.md", "wiki/p/core/core.md", "wiki/p/core/y.md"]
    assert wiki.slug_map(str(tmp_path))["y"] == "wiki/p/core/y.md"


def test_frontmatter_scalars():
    text = page_text("T", "One line: with colon", "body")
    fm = wiki.frontmatter_scalars(text)
    assert fm["description"] == "One line: with colon"
    assert wiki.frontmatter_scalars("no frontmatter") == {}
    assert wiki.frontmatter_scalars("---\ntitle: x\n") == {}


def test_page_links():
    assert wiki.page_links("See [[a]], [[b|B]] and [[c#sec]].") == ["a", "b", "c"]


def test_catalog_groups_by_category_and_skips_discussions(tmp_path):
    write(tmp_path, "wiki/a/a.md", page_text("A", "index a", ""))
    write(tmp_path, "wiki/a/z.md", page_text("Z", "page z", ""))
    write(tmp_path, "wiki/a/b/y.md", page_text("Y", "page y", ""))
    write(tmp_path, "wiki/discussions/d.md", page_text("D", "hidden", ""))
    text = wiki.catalog_md(str(tmp_path), planned=[{"page": "wiki/a/new.md", "description": "soon"}])
    assert text.count("## a\n") == 1
    assert "## a/b" in text
    assert "- `wiki/a/z.md` — page z" in text
    assert "hidden" not in text
    assert "(planned new page) soon" in text


def test_has_category(tmp_path):
    write(tmp_path, "wiki/a/a.md", "x")
    assert wiki.has_category(str(tmp_path), "wiki/a/new.md")
    assert not wiki.has_category(str(tmp_path), "wiki/zzz/new.md")


def test_atomic_io_roundtrip_preserves_crlf(tmp_path):
    p = str(tmp_path / "d" / "f.md")
    common.write_text(p, "a\r\nb\r\n")
    assert common.read_text(p) == "a\r\nb\r\n"
    common.dump_json(str(tmp_path / "j.json"), {"k": "é"})
    assert common.load_json(str(tmp_path / "j.json")) == {"k": "é"}
    assert common.load_json(str(tmp_path / "missing.json"), 7) == 7
    assert not os.path.exists(p + ".tmp")


def test_norm_ws():
    assert common.norm_ws("  a \n b\t c ") == "a b c"
