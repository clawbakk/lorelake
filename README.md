<p align="center">
  <img src="./docs/assets/logo.svg" alt="LoreLake logo" width="120" />
</p>

<h1 align="center">LoreLake</h1>

<p align="center">
  <em>A compounding, self-maintaining project wiki for Claude Code.</em>
</p>

<p align="center">
  <a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  <a href="https://github.com/clawbakk/lorelake/releases"><img alt="Version" src="https://img.shields.io/github/v/release/clawbakk/lorelake" /></a>
  <img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin-7A5AF8.svg" />
</p>

![LoreLake install wizard completing in a test project, side-by-side with the resulting llake/ tree](./docs/assets/hero.png)
<sub><em>Placeholder: side-by-side terminal screenshot of `/llake-lady` finishing in a test project, with the resulting `<project>/llake/` tree in a second pane.</em></sub>

> **Claude Code forgets.** LoreLake remembers — in markdown your team can read, edit, diff, and grep.

## What it is

LoreLake is a Claude Code plugin that turns your project into a **self-maintaining knowledge base**. Every time you end a coding session or merge a branch, LoreLake quietly updates a structured markdown wiki inside your repo (`<project>/llake/wiki/`). The next Claude Code session loads that wiki as context — so Claude walks into your project already knowing its decisions, gotchas, and playbooks.

Unlike retrieval-augmented chat, LoreLake builds a **compounding artifact**: each ingest extends an interlinked corpus of pages you can read, diff, and trust. The wiki is plain markdown. It lives in your repo. You own it.

## Features

- 🌊 **Automatic.** Updates on session end (what you discussed) and on merges (what changed in the code).
- 📚 **Structured.** Fixed categories — `decisions`, `gotchas`, `discussions`, `playbook` — plus project-specific ones that emerge as the wiki grows.
- 🔗 **Wikilinks.** `[[page-name]]` syntax and bidirectional `related:` frontmatter. Any markdown editor renders the wiki natively.
- 🩺 **Self-healing.** `/llake-doctor` diagnoses and repairs drift; safe to run anytime.
- 🔒 **Security-first.** A built-in content standard forbids writing credentials, connection strings, internal hostnames/IPs, or PII into the wiki.
- 📝 **You own the data.** Everything lives in `<project>/llake/`. Plain markdown, git-friendly, zero vendor lock-in.
- 💸 **Cost-aware.** A cheap triage pass decides whether session end is worth capturing at all; budgets are configurable per writer.
- 🧠 **Schema-driven.** Every page satisfies a "new-employee test": a stranger can start work on any subsystem reading only the wiki.

![Animated demo: install, code, merge, ingest, wiki grows, open in Obsidian](./docs/assets/demo.gif)
<sub><em>Placeholder: 30–60s animated demo of the full loop, from `/llake-lady` to a new `ingest` entry appearing in `log.md` to the graph view in Obsidian.</em></sub>

## Install

**From GitHub (recommended today):**

```
/plugin marketplace add clawbakk/lorelake
/plugin install lorelake@clawbakk
```

**Claude Code marketplace (once listed):**

```
/plugin install lorelake@clawbakk
```

Full instructions — including pinning, local-development install, and troubleshooting — in [**docs/INSTALL.md**](./docs/INSTALL.md).

## Quick start

1. Install the plugin (above).
2. In your project root, run `/llake-lady`. The install wizard creates `<project>/llake/` and wires the git `post-merge` hook.
3. Run `/llake-bootstrap` once. Claude reads your codebase and populates the initial wiki.
4. Work normally. On every session end and every merge, the wiki updates itself.
5. Run `/llake-doctor` anytime you want to check or repair the install.

## How it works

![Three writers — bootstrap, ingest, capture — all writing into one wiki](./docs/assets/architecture.png)
<sub><em>Placeholder: diagram showing `/llake-bootstrap` (user-invoked, in-session), `ingest` (git post-merge, background agent), and `capture` (Claude Code SessionEnd, background agent) all writing to `<project>/llake/wiki/`.</em></sub>

Three writers, three triggers, one wiki:

- **Bootstrap** (`/llake-bootstrap`) — user-invoked, runs once. Reads `ingest.include`, decomposes the codebase, and dispatches subagents to write the initial pages.
- **Ingest** (git `post-merge`) — background agent. On every merge into the configured branch, reads the diff and updates affected pages.
- **Capture** (Claude Code `SessionEnd`) — background agent. Classifies the session via a cheap triage pass, then captures decisions, gotchas, and playbooks on sessions worth it.

Safety rails applied to every writer:

- Immutable schema (`schema/`) defines page format, write surface, and content standards.
- Per-writer write-surface rules (capture owns the fixed categories; ingest excludes `discussions/`; bootstrap has full write access).
- Recursion guard (`IS_LLAKE_AGENT=true`) so background sessions don't re-trigger themselves.
- Bounded budgets and timeouts; two-pass triage keeps costs sane on high-volume session days.

## The wiki

Every page is a short markdown file with YAML frontmatter, located under `<project>/llake/wiki/<category>/`. Example:

```markdown
---
title: "Why ingest runs on post-merge, not post-commit"
category: decisions
created: 2026-04-18
updated: 2026-04-22
related: [[post-merge-hook]], [[ingest-agent]]
tags: [architecture, hooks]
---

## Decision
Ingest runs on `post-merge`, not `post-commit`.

## Why
Post-commit fires on every local commit — too noisy, too cheap to be trustworthy.
Post-merge fires on `git pull` and on merge commits — exactly "new code arrived
from somewhere else," the moment the wiki should reconsider its claims.

## Tradeoffs
...
```

The wiki is plain markdown — any editor renders it: VS Code, Zed, Vim, Typora, GitHub's web UI, Obsidian if you want a graph view, whatever you prefer. Nothing is hidden in a database.

## Why LoreLake

**Versus retrieval-augmented chat over a codebase:**

| | Traditional RAG | **LoreLake** |
|---|---|---|
| Artifact | Opaque vector index | **Structured, versioned markdown** |
| Maintenance | Re-index on demand | **Automatic on merge + session end** |
| Content standard | None | **Schema-enforced; new-employee test** |
| Security posture | Unspecified | **Explicit no-credentials rule** |
| Trigger model | Query-time retrieval | **Event-triggered background agents** |
| Drift repair | N/A | **`/llake-doctor`** |

**Core design decisions:**

- **Event-triggered, not query-time.** The wiki updates when code changes or sessions end — not when someone remembers to refresh it.
- **Schema-enforced content.** Every page satisfies a "new-employee test": enough context that a stranger can start work in that area without reading the source.
- **Security-first.** An explicit content standard: no credentials, connection strings, internal hostnames, or PII in the wiki. Not a guideline — a rule, enforced during capture.
- **Self-healing.** `/llake-doctor` reconciles drift in place. Fresh clones, upgrades, and manual edits all route through the same repair path.
- **Plain markdown, your repo.** The artifact is human-readable and editable by any tool. No database, no index, nothing to back up separately.

## Inspiration & credits

LoreLake was inspired by **[Andrej Karpathy's LLM-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)**, which sketched the pattern of an LLM maintaining a project's internal wiki. We took the idea and wired it into Claude Code as a first-class plugin: triggers, schema, background agents, safety rails, and a doctor.

Thanks to the Claude Code and Anthropic teams for the plugin system that makes this possible.

## Roadmap

- ✅ **Claude Code** — current.
- 🟡 **Codex** — planned.
- 🟡 **GitHub Copilot CLI** — planned.
- 🟡 **Gemini CLI** — planned.
- 🟡 **Cursor** — under evaluation.

Follow [issues labelled `roadmap`](https://github.com/clawbakk/lorelake/issues?q=label%3Aroadmap) for specifics.

## Contributing

PRs welcome. Read [CONTRIBUTING.md](./CONTRIBUTING.md) first — LoreLake follows a spec-first workflow: design in `docs/specs/`, implement against the spec, tests required for lib code.

## License

MIT — see [LICENSE](./LICENSE).
