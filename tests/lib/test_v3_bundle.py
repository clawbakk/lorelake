"""Bundling: caps, severity tiers, carried order, record affinity."""
from v3_helpers import page_text, write
from ingest_v3.bundle import make_bundles, weight


def e(page, severity="minor", themes=("T1",), kb=1.0, claims=1, **kw):
    d = {"page": page, "severity": severity, "themes": list(themes), "evidence": [], "weight_kb": kb,
         "claims": [{"quote": "q", "head": "h", "severity": severity}] * claims}
    d.update(kw)
    return d


def test_weight():
    assert weight(e("wiki/a/x.md", kb=10.5, claims=3)) == 13.5


def test_page_cap(tmp_path):
    pages = [e("wiki/a/p{}.md".format(i)) for i in range(6)]
    assert [len(b["pages"]) for b in make_bundles(pages, str(tmp_path), 4, 80)] == [4, 2]


def test_weight_cap_and_heavy_page_alone(tmp_path):
    pages = [e("wiki/a/heavy.md", kb=100), e("wiki/a/m1.md", kb=30), e("wiki/a/m2.md", kb=30),
             e("wiki/a/m3.md", kb=30)]
    bundles = make_bundles(pages, str(tmp_path), 4, 80)
    sizes = sorted(len(b["pages"]) for b in bundles)
    assert sizes == [1, 1, 2]
    assert ["wiki/a/heavy.md"] in [b["pages"] for b in bundles]
    assert all(b["weight"] <= 80 or len(b["pages"]) == 1 for b in bundles)


def test_major_first_and_ids(tmp_path):
    pages = [e("wiki/a/minor.md", themes=("T2",)), e("wiki/a/major.md", severity="major")]
    bundles = make_bundles(pages, str(tmp_path), 4, 80)
    assert bundles[0]["pages"] == ["wiki/a/major.md"] and bundles[0]["severity"] == "major"
    assert [b["id"] for b in bundles] == ["b01", "b02"]


def test_carried_oldest_since_first(tmp_path):
    pages = [e("wiki/a/new.md", severity="major", carried=True, since="s2"),
             e("wiki/a/old.md", severity="major", carried=True, since="s1"),
             e("wiki/a/fresh.md", severity="major")]
    order = [b["pages"][0] for b in make_bundles(pages, str(tmp_path), 1, 80, since_rank={"s1": 10, "s2": 20})]
    assert order == ["wiki/a/old.md", "wiki/a/new.md", "wiki/a/fresh.md"]


def test_shared_theme_groups_before_unrelated(tmp_path):
    pages = [e("wiki/a/a1.md", themes=("T1",)), e("wiki/a/b1.md", themes=("T2",)),
             e("wiki/a/a2.md", themes=("T1",)), e("wiki/a/b2.md", themes=("T2",))]
    groups = [sorted(b["pages"]) for b in make_bundles(pages, str(tmp_path), 2, 80)]
    assert sorted(groups) == [["wiki/a/a1.md", "wiki/a/a2.md"], ["wiki/a/b1.md", "wiki/a/b2.md"]]


def test_record_joins_the_state_page_it_links(tmp_path):
    write(tmp_path, "wiki/decisions/adr-cache.md", page_text("ADR", "adr", "See [[cache-layer]] for details."))
    write(tmp_path, "wiki/arch/cache-layer.md", page_text("Cache", "cache", "x"))
    write(tmp_path, "wiki/arch/other.md", page_text("Other", "other", "x"))
    pages = [e("wiki/arch/cache-layer.md", severity="major", themes=("T1",)),
             e("wiki/arch/other.md", severity="major", themes=("T1",)),
             e("wiki/decisions/adr-cache.md", severity="minor", themes=())]
    bundles = make_bundles(pages, str(tmp_path), 2, 80)
    with_record = [b for b in bundles if "wiki/decisions/adr-cache.md" in b["pages"]][0]
    assert "wiki/arch/cache-layer.md" in with_record["pages"]


def test_record_join_respects_caps(tmp_path):
    write(tmp_path, "wiki/decisions/adr-cache.md", page_text("ADR", "adr", "See [[cache-layer]]."))
    write(tmp_path, "wiki/arch/cache-layer.md", page_text("Cache", "cache", "x"))
    pages = [e("wiki/arch/cache-layer.md", severity="major"), e("wiki/decisions/adr-cache.md", themes=())]
    assert all(len(b["pages"]) == 1 for b in make_bundles(pages, str(tmp_path), 1, 80))
