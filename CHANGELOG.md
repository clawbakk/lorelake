# Changelog

All notable changes to LoreLake are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `.claude-plugin/marketplace.json` — makes the repo a single-plugin marketplace under the name `clawbakk`, so `/plugin marketplace add clawbakk/lorelake` + `/plugin install lorelake@clawbakk` resolve. Test coverage in `tests/lib/test_marketplace_manifest.py`.

### Changed
- Install docs (`README.md`, `docs/INSTALL.md`) rewritten around the marketplace-add flow. The previously documented `git+https://...` form never worked and has been removed.

### Fixed
- Every `/plugin install lorelake` / `/plugin update lorelake` reference in user-facing docs is now qualified with `@clawbakk`. Regression-tested in `tests/lib/test_release_content.py`.

## [0.1.0] — 2026-04-22

First public release.

### Added
- **Plugin scaffolding** — `.claude-plugin/plugin.json` manifest, `hooks/hooks.json` native hook registration, `/plugin install` compatibility.
- **Four user-invoked skills** (`disable-model-invocation: true`):
  - `/llake-lady` — install wizard.
  - `/llake-doctor` — idempotent diagnose-and-repair.
  - `/llake-bootstrap` — in-session initial wiki population.
  - `/llake-lint` — wiki lint, Quick + Comprehensive modes.
- **Hooks:**
  - Claude Code `SessionStart` — injects the LoreLake preamble and project `index.md` into the session.
  - Claude Code `SessionEnd` — two-pass triage → capture for decisions, gotchas, and discussions.
  - git `post-merge` — ingest agent updates the wiki on merges into the configured branch.
- **Schema** — split into `core.md`, `code-content-standard.md`, `conversation-content-standard.md`, `operations.md`; each writer loads only what it needs.
- **Content standards** — Standard 1 (new-employee test), Standard 2 (immutable discussion Key Facts), Standard 3 (no credentials or PII).
- **Plain-markdown wiki format** — pages are markdown with YAML frontmatter and `[[wikilinks]]`; any markdown editor (VS Code, Obsidian, Typora, GitHub's web UI, etc.) renders the wiki natively.
- **Tests** — pytest suite for Python lib helpers, bash test scripts for shell libs.
- **Docs** — README, `docs/INSTALL.md`, `CONTRIBUTING.md`, `SECURITY.md`.

### Known limitations
- **Claude Code only.** Codex, Copilot CLI, and Gemini CLI support is on the roadmap but not implemented.
- **Per-clone git hooks.** Each collaborator runs `/llake-doctor` once after cloning a LoreLake-tracked project to wire the local `post-merge` hook. Git does not ship hooks in the tree.

### Credits
Inspired by [Andrej Karpathy's LLM-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

[Unreleased]: https://github.com/clawbakk/lorelake/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/clawbakk/lorelake/releases/tag/v0.1.0
