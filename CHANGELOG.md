# Changelog

All notable changes to LoreLake are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Ingest batching gate** — `hooks/lib/ingest_gate.py` decides `EMPTY` / `WAIT` / `RUN` on every `post-merge`, before either ingest pipeline spawns. Ingest no longer runs on every merge: it defers until net churn under `ingest.include` crosses `ingest.schedule.minChangedLines` (default 1500) or the pile ages past `ingest.schedule.maxAgeHours` (default 24). `ingest.schedule.enabled: false` disables batching but never the empty-pile skip; `LLAKE_IGNORE_SCHEDULE=1` forces a run. Covered by `tests/lib/test_ingest_gate.py` and `tests/hooks/test_post_merge_gate.sh`.
- `hooks/lib/ingest-cursor.sh` — `advance_ingest_cursor` writes `llake/last-ingest-sha` and `llake/.state/last-ingest-at` together; `ensure_ingest_clock` seeds a missing clock so a fresh clone does not force an ingest on its first merge.
- `.claude-plugin/marketplace.json` — makes the repo a single-plugin marketplace under the name `clawbakk`, so `/plugin marketplace add clawbakk/lorelake` + `/plugin install lorelake@clawbakk` resolve. Test coverage in `tests/lib/test_marketplace_manifest.py`.
- **Merge rules for parallel branches and worktrees** — `templates/gitattributes` ships the `merge=union` rules for `llake/log.md` and the four fixed-category indexes, the append-only files every capture and ingest adds to. The install plan copies it to `<project>/llake/.gitattributes` in Phase 1, and `/llake-doctor` adds any missing rule to existing installs, so two worktrees that both captured a session no longer conflict on every merge and rebase. Wiki pages are deliberately excluded: union there would silently keep both versions of prose two sessions rewrote. Covered by `tests/lib/test_gitattributes_template.py`.

### Changed
- Install docs (`README.md`, `docs/INSTALL.md`) rewritten around the marketplace-add flow. The previously documented `git+https://...` form never worked and has been removed.

### Fixed
- The ingest-v2 pipeline now honours the empty-pile skip. Previously it spawned a planner agent even when nothing under `ingest.include` had changed, and its success path wrote the cursor without the clock, which permanently defeated the age arm for v2 projects.
- Watchdog subshells no longer orphan their `sleep` child. `kill "$WATCHDOG_PID"` killed the subshell but not the `sleep`, which reparented to init and held inherited file descriptors for its full timeout (up to 20 minutes), stalling any command that piped hook output. All seven teardown sites in `hooks/post-merge.sh` and `hooks/lib/session-capture-worker.sh` now use the existing `kill_tree` helper.
- The gate's empty-pile cursor advance now takes the `post-merge` lock, so it can no longer advance past a commit range an in-flight agent will write back.
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
