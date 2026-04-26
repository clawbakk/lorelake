# LoreLake Schema Rules — Reference for the Ingest Planner

These are the rules a planner agent needs to follow when emitting an ingest plan.
The full spec lives in `schema/core.md` and `schema/code-content-standard.md`.

## Page format

Every wiki page requires YAML frontmatter:

```yaml
---
title: "Page Title"
description: "One-line summary for category index"
tags: [category, topic, subtopic]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: current  # current | draft | stale | deprecated | immutable
related:
  - "[[page-name]]"
---
```

Required body sections: a brief overview, main content, "Key Points" summary,
"Code References" with `file:line` links, and "See Also" with wikilinks.

## Category taxonomy

Two kinds of categories exist:

- **Fixed categories** — universal across every project: `discussions`,
  `decisions`, `gotchas`, `playbook`. Declared in `config.json` under
  `llake.fixedCategories`.
- **Project-specific categories** — vary per project, reflecting the project's
  conceptual model. Discovered at runtime by listing `llake/wiki/*/`. Created
  on demand when no existing category is a reasonable home for new content.

### Category creation protocol

Only the planner may propose new categories (via a `create` whose category
doesn't yet exist as a directory). Rules:

1. **Prefer existing.** Only create a new category when no existing one is a
   reasonable home.
2. **Align naming.** Read all existing category one-liners before naming a new
   one. Do not propose `monitoring/` if `observability/` already exists.
3. **Name broadly.** Lowercase, singular noun. Prefer names broad enough that
   future related pages can live there too.
4. **Update the root index.** When a new category is introduced, also emit an
   `update` for `index.md` to add the category line.

## Naming conventions

- **Slugs/file names:** lowercase, hyphen-separated. e.g., `tick-loop`.
- **Decision records:** `adr-NNN-short-title` (NNN = zero-padded sequence).
- **Tags:** lowercase, hyphen-separated. Category tag first, then specifics.

## Slug uniqueness across nested categories

`[[wikilink]]` slugs remain **globally unique across the entire wiki**, even
under nested categories. When two natural homes would produce the same slug,
prefix with the parent (`[[apposum-runtime]]`, `[[beejector-runtime]]`).

## Monorepos

If the project uses a nested layout (multiple project markers like
`package.json`, `pyproject.toml`, etc. at non-root paths), the wiki mirrors
the workspace under grouping dirs (`packages/`, `apps/`, `services/`). The
ingest planner does NOT restructure the layout — that's bootstrap's job. The
planner simply respects whatever layout already exists. **Out of scope: do
not propose layout changes in an ingest plan.**

## Standard 1 — Code-content completeness (non-negotiable)

Pages documenting code must hold up to the **new-employee test**: a new hire
reading the page should not need to ask a senior engineer or open the source
to understand the topic.

A complete page captures:

- **What is it?** Subject identity.
- **Why does it exist?** Purpose, problem it solves, alternatives.
- **What does it consist of?** Structure — parts, members, contents.
- **What is its semantic value?** Role in the larger project — what depends
  on it, what it enables, what invariants it upholds, what would break if it
  changed.
- **How is it used?** Concrete examples — real call sites, message flows,
  configuration values in context.
- **What does it interact with?** Inputs, outputs, dependencies, side effects.
- **What constrains it?** Conventions, invariants, lifecycle, error modes,
  gotchas.
- **Where does it connect?** `[[wikilinks]]` to related pages.

Brevity is not a virtue when it forces the reader back to the source.

## Standard 3 — Security (overrides every other guideline)

**No sensitive data in any wiki page, ever.** This rule overrides every other
content guideline.

Specifically forbidden in any page, frontmatter, code reference, or example:

- Credentials: API keys, access tokens, JWTs, OAuth secrets, SSH keys,
  passwords.
- Database connection strings (with or without credentials).
- Private hostnames, internal URLs, internal IP addresses.
- Personal data: real names, emails, phone numbers, addresses, employee IDs.
- Any value that appears in `.env`, secrets managers, or vaulted config.

When uncertain, apply both tests:

1. **Exploitation test:** *"If a hacker knew this value, could they exploit
   it?"* If yes → do not capture.
2. **Public-disclosure test:** *"If this leaks publicly, is there even a 1%
   chance of harm?"* If yes → do not capture.

If a fact requires the value to make sense, **abstract it**: refer to "the
API key" not the key itself, "the production database" not the connection
string, "an internal endpoint" not the URL. A "complete" page that contains
a password is not complete — it is broken.

## Forbidden write surface (planner cannot propose)

The applier rejects ops targeting any of these regardless of what the plan
says, but proposing them wastes tokens:

- `wiki/discussions/**` — owned by session-capture
- `schema/**` — immutable to agents
- `<llake_root>/config.json` — user configuration
- `<llake_root>/last-ingest-sha` — managed by the shell
- `<llake_root>/.state/**` — runtime working dir
- Anything outside `<llake_root>/`
