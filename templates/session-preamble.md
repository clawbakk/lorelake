# LoreLake — Operating Context

This project uses LoreLake at `llake/` — a persistent, structured wiki that serves as the project's long-term knowledge base. It complements `CLAUDE.md` ("how to work in this project") with deep, queryable knowledge ("how this project works").

## When to consult LoreLake

Read LoreLake when you need context the code alone doesn't reveal: architectural reasoning, cross-module data flow, design decisions (and the alternatives that were rejected), known gotchas, troubleshooting playbooks, conversation history.

Read the **code** for current implementation details. When LoreLake disagrees with the code, **the code is truth** — flag the LoreLake page as stale.

## How to query

1. Read `llake/index.md` — the category catalog. Find the relevant area.
2. Read the category index (e.g., `llake/wiki/<category>/<category>.md`) — it lists all pages in that category with one-line summaries.
3. Read the page itself for full detail.
4. Follow `[[wikilinks]]` for deeper context.

## Conventions

Full conventions and operations are in the plugin's `schema/` directory — start at `schema/index.md` for a map of which file owns which rule. Always defer to those files as the canonical spec.

LoreLake is maintained by automated hooks. Do not edit `llake/.state/` (runtime working dir). Do not edit `llake/last-ingest-sha` (ingest cursor).
