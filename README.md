# LoreLake — Claude Code Plugin

A Claude Code plugin that turns your project into a self-maintaining knowledge base. LoreLake captures session learnings (ended Claude Code sessions) and post-merge commits into a structured wiki you can query later.

The wiki is a **compounding artifact** — not RAG-on-demand. Each ingest extends an interlinked corpus of pages (subsystems, decisions, gotchas, troubleshooting playbooks, session summaries) so future LLM sessions load deep project context cheaply.

## Status

Pre-release. Skills are scaffolded but not yet generated; see `skills/<name>/spec.md` files for the specifications.

## Architecture

- **Plugin** (`~/.claude/plugins/lorelake/` or local) ships hook scripts, lib helpers, prompt templates, the canonical `schema/` directory (split-by-consumer schema files), and skill specs. Updates to the plugin reach all installed projects immediately.
- **Project** (`<your-project>/llake/`) holds only data: `config.json`, `wiki/`, `index.md`, `log.md`, `.state/`. Git-friendly and small.
- **Three skills** (user-invoked only):
  - `/llake-lady` — install wizard
  - `/llake-doctor` — diagnose + repair
  - `/llake-bootstrap` — initial wiki population

## Quick start

(Coming soon — once skills are generated. See `docs/INSTALL.md`.)

## Documentation

- Operating spec: `schema/` (start at `schema/index.md`)
- Per-skill specs: `skills/<name>/spec.md`

## License

MIT — see `LICENSE`.
