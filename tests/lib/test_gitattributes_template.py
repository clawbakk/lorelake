"""Tests for `templates/gitattributes` — the merge rules installed into a
project as `<project>/llake/.gitattributes`.

Capture and ingest both append: a row at the end of a category index, an entry
at the end of `log.md`. Two branches (or two worktrees) that append at the same
place conflict on every merge and rebase, which is what these rules remove by
asking git's built-in `union` driver to keep both sides' lines.

The rules must stay narrow. `union` on an ordinary wiki page would silently keep
both versions of edited prose instead of reporting a conflict.
"""
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GITATTRIBUTES = REPO_ROOT / "templates" / "gitattributes"
CONFIG_DEFAULT = REPO_ROOT / "templates" / "config.default.json"
PLAN_TMPL = REPO_ROOT / "templates" / "plan.md.tmpl"
DOCTOR_SKILL = REPO_ROOT / "skills" / "llake-doctor" / "SKILL.md"

INDEX_HEADER = """---
title: "Gotchas"
description: "Category index for gotchas."
tags: [gotchas]
created: 2026-01-01
updated: 2026-01-01
---

# Gotchas

| Page | Description |
|---|---|
"""


def _rules():
    """Return [(pattern, [attr, ...])] for every non-comment line of the template."""
    rules = []
    for line in GITATTRIBUTES.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        rules.append((fields[0], fields[1:]))
    return rules


def _git(cwd, *args):
    """Run git with an identity of its own, so the test never depends on
    (or is broken by) the developer's global git config."""
    return subprocess.run(
        ["git", "-c", "user.name=llake-test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


def _seed_project(root):
    """Create a git repo holding the append-only files plus the shipped rules."""
    llake = root / "llake"
    (llake / "wiki" / "gotchas").mkdir(parents=True)
    (llake / ".gitattributes").write_text(GITATTRIBUTES.read_text())
    (llake / "log.md").write_text("# LoreLake Activity Log\n")
    (llake / "wiki" / "gotchas" / "gotchas.md").write_text(INDEX_HEADER)
    (llake / "wiki" / "gotchas" / "some-page.md").write_text("---\ntitle: \"A page\"\n---\n\nBody.\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")


def _capture(root, slug, date):
    """Append what a capture agent appends: an index row and a log entry."""
    llake = root / "llake"
    index = llake / "wiki" / "gotchas" / "gotchas.md"
    index.write_text(index.read_text() + f"| [[{slug}]] | {slug} summary |\n")
    log = llake / "log.md"
    log.write_text(log.read_text() + f"\n## [{date}] session-capture | {slug}\n\nPages affected: [[{slug}]]\n")


def test_template_exists():
    assert GITATTRIBUTES.is_file(), (
        f"templates/gitattributes must exist at {GITATTRIBUTES} — it is the source "
        "the install plan copies and /llake-doctor repairs against"
    )


def test_every_rule_asks_for_union_merge():
    for pattern, attrs in _rules():
        assert attrs == ["merge=union"], (
            f"rule for {pattern!r} sets {attrs!r}; every rule in this template must "
            "set exactly merge=union"
        )


def test_rules_cover_log_and_every_fixed_category_index():
    """The append-only set is log.md plus one index per fixed category. Adding a
    fixed category without a rule for it silently reintroduces the conflicts."""
    fixed = json.loads(CONFIG_DEFAULT.read_text())["llake"]["fixedCategories"]
    expected = {"/log.md"} | {f"/wiki/{cat}/{cat}.md" for cat in fixed}
    assert {pattern for pattern, _ in _rules()} == expected


def test_install_plan_and_doctor_both_reference_the_template():
    """The template is the single source of truth: the plan copies it at install,
    doctor repairs against it afterwards."""
    for path in (PLAN_TMPL, DOCTOR_SKILL):
        content = path.read_text()
        assert "templates/gitattributes" in content, f"{path.name} must reference templates/gitattributes"
        assert "llake/.gitattributes" in content, f"{path.name} must name the installed path llake/.gitattributes"


def test_parallel_appends_merge_without_conflict(tmp_path):
    """Two branches each capture a session, then merge — the case that conflicts today."""
    _seed_project(tmp_path)

    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _capture(tmp_path, "gotcha-from-feature", "2026-01-02")
    _git(tmp_path, "commit", "-qam", "capture on feature")

    _git(tmp_path, "checkout", "-q", "main")
    _capture(tmp_path, "gotcha-from-main", "2026-01-03")
    _git(tmp_path, "commit", "-qam", "capture on main")

    merge = _git(tmp_path, "merge", "--no-edit", "feature")
    assert merge.returncode == 0, f"merge conflicted:\n{merge.stdout}\n{merge.stderr}"

    index = (tmp_path / "llake" / "wiki" / "gotchas" / "gotchas.md").read_text()
    log = (tmp_path / "llake" / "log.md").read_text()
    for content in (index, log):
        assert "<<<<<<<" not in content, f"conflict markers survived in:\n{content}"
    for slug in ("gotcha-from-feature", "gotcha-from-main"):
        assert slug in index, f"{slug} row lost from the index:\n{index}"
        assert slug in log, f"{slug} entry lost from log.md:\n{log}"


def test_rules_do_not_apply_to_ordinary_wiki_pages(tmp_path):
    """Union on a real page would merge two rewrites of the same prose silently."""
    _seed_project(tmp_path)

    def attr(rel):
        out = _git(tmp_path, "check-attr", "merge", "--", rel).stdout.strip()
        return out.rsplit(": ", 1)[-1]

    assert attr("llake/wiki/gotchas/gotchas.md") == "union"
    assert attr("llake/log.md") == "union"
    assert attr("llake/wiki/gotchas/some-page.md") == "unspecified"
