"""Regression tests for the release-readiness content rewrites."""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
CONFIG_DEFAULT = REPO_ROOT / "templates" / "config.default.json"


def test_readme_has_no_stale_scaffolding_line():
    """The pre-release README said skills were not yet generated; all four now exist."""
    content = README.read_text()
    assert "Skills are scaffolded but not yet generated" not in content, (
        "README still references the stale 'skills not yet generated' line"
    )


def test_readme_links_to_contributing_and_install():
    content = README.read_text()
    assert "(./CONTRIBUTING.md)" in content, "README must link to CONTRIBUTING.md"
    assert "(./docs/INSTALL.md)" in content, "README must link to docs/INSTALL.md"


def test_readme_karpathy_only_in_inspiration_section():
    """Karpathy is attribution-only; no comparison framing in Features/Why LoreLake."""
    content = README.read_text()
    assert "## Inspiration & credits" in content, (
        "README must have an Inspiration & credits section"
    )
    pre_section = content.split("## Inspiration & credits")[0]
    assert "karpathy" not in pre_section.lower(), (
        "Karpathy mentions must be confined to the Inspiration & credits section"
    )


def test_changelog_has_0_1_0_section():
    content = CHANGELOG.read_text()
    assert "## [0.1.0]" in content, "CHANGELOG must have a [0.1.0] release section"


def test_changelog_has_no_stale_skill_creator_bullet():
    """Skills exist now; the pre-release 'via /skill-creator' bullet is stale."""
    content = CHANGELOG.read_text()
    assert "/skill-creator" not in content, (
        "CHANGELOG still references the stale /skill-creator pending-generation bullet"
    )


def test_changelog_does_not_mention_code_of_conduct():
    """CODE_OF_CONDUCT.md is explicitly not shipped in this release."""
    content = CHANGELOG.read_text()
    assert "CODE_OF_CONDUCT" not in content, (
        "CHANGELOG must not reference CODE_OF_CONDUCT.md (not shipped in 0.1.0)"
    )


def test_config_default_ingest_include_is_minimal():
    """Spec §13 (c): ship a minimal, unbiased default for ingest.include."""
    data = json.loads(CONFIG_DEFAULT.read_text())
    include = data["ingest"]["include"]
    assert include == ["src/"], (
        f"ingest.include default must be ['src/']; got {include!r}"
    )


def test_no_git_plus_https_install_syntax_in_user_docs():
    """`/plugin install git+https://...` never worked — it is treated as a
    marketplace name lookup and returns 'Marketplace not found'. Any recurrence
    in user-facing docs is a regression.
    """
    offenders = []
    for rel in ("README.md", "docs/INSTALL.md"):
        path = REPO_ROOT / rel
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if "git+https" in line:
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Found deprecated git+https install syntax:\n" + "\n".join(offenders)
    )


def test_plugin_install_lorelake_uses_clawbakk_marketplace():
    """Every `/plugin install lorelake` or `/plugin update lorelake` must be
    qualified with `@clawbakk` once the marketplace manifest ships — the bare
    form doesn't resolve.
    """
    pattern = re.compile(r"/plugin (?:install|update) lorelake(?!@clawbakk)")
    offenders = []
    for rel in ("README.md", "docs/INSTALL.md"):
        path = REPO_ROOT / rel
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Found `/plugin install|update lorelake` without `@clawbakk` suffix:\n"
        + "\n".join(offenders)
    )
