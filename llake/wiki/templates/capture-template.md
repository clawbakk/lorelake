---
title: "Capture Prompt Template"
description: "The full session-capture agent prompt that writes discussion, decision, gotcha, and playbook pages"
tags: [templates, session-capture, capture]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[session-end-hook]]"
  - "[[triage-template]]"
  - "[[template-system]]"
  - "[[adr-002-two-pass-triage]]"
---

## Overview

The capture template (`hooks/prompts/capture.md.tmpl`) drives the second pass of session capture. It instantiates a full-capability agent that reads the session transcript — already classified by the triage agent — and extracts persistent knowledge into the wiki. The capture agent is the only writer to `wiki/discussions/`. It may also create or update pages in `wiki/decisions/`, `wiki/gotchas/`, and `wiki/playbook/`.

The capture agent runs only when the triage agent returns `CAPTURE` or `PARTIAL`. A `PARTIAL` result instructs the capture agent to filter the transcript and extract only the project-relevant portions before writing.

## Placeholders

The capture template uses eight `{{VAR}}` placeholders, all supplied as CLI KEY=value args by `session-end.sh`:

| Placeholder | Filled by | Value |
|---|---|---|
| `{{AGENT_ID}}` | `session-end.sh` | Readable agent ID, e.g. `swift-lion-143022_capture` |
| `{{PROJECT_ROOT}}` | `session-end.sh` | Absolute path to the project root |
| `{{TRIAGE_CLASSIFICATION}}` | `session-end.sh` | `CAPTURE` or `PARTIAL` (parsed from triage result) |
| `{{TRIAGE_REASON}}` | `session-end.sh` | The brief rationale from the triage agent's output line |
| `{{WRITABLE_CATEGORIES}}` | `session-end.sh` | Formatted list of writable category paths, built from `sessionCapture.writableCategories` config |
| `{{LLAKE_ROOT}}` | `session-end.sh` | Absolute path to `<project>/llake/` |
| `{{WIKI_ROOT}}` | `session-end.sh` | Absolute path to `<project>/llake/wiki/` |
| `{{SCHEMA_DIR}}` | `session-end.sh` | Absolute path to the plugin's `schema/` directory |
| `{{SESSION_DIR}}` | `session-end.sh` | Absolute path to `<project>/llake/.state/sessions/<session-id>/` |

There are no `{{VAR|fallback:path}}` file-read placeholders in the capture template. All values are injected at runtime.

## Write Surface

The template defines a category boundary enforced in the prompt text via `{{WRITABLE_CATEGORIES}}`. The default writable categories (from `sessionCapture.writableCategories`) are:

```
discussions/, decisions/, gotchas/, playbook/
```

The capture agent is explicitly forbidden from writing to any other directory. The ingest agent owns all other categories (architecture, config, lib, etc.), and the capture agent must not touch them. This boundary is a prompt-level rule; `allowedTools` at the shell level provides a second layer by restricting which tools the agent can call (see `sessionCapture.allowedTools` in config, typically `Read,Write,Edit,Glob,Grep,Bash`).

## Content Standards the Agent Enforces

### Standard 2 — Conversation-content fidelity

The most important rule in the capture template. The agent is required to read `schema/conversation-content-standard.md` in Phase 1 before writing anything. The load-bearing constraint from that standard:

**Discussion entries have an immutable Key Facts block.** Once written, the Key Facts block in a discussion entry is permanent — it is never edited, even if derived pages evolve. The block captures what was concretely known and decided at the moment of the session. This immutability is what gives discussions their value as historical record.

The template instructs the agent to format the Key Facts block exactly per the canonical format in the schema file, and to treat it as `status: immutable` in the frontmatter.

### Standard 3 — Security (no sensitive data)

Standard 3 overrides everything, including Key Facts immutability. If a Key Facts block would contain credentials, secrets, PII, or other harmful data, the agent must redact or abstract it. The rule: abstract (`"the API key"`) rather than capture the value.

## Six-Phase Workflow

The capture template structures the agent's work into six explicit phases:

**Phase 1 — Load Context.** Read `schema/core.md` (conventions), `schema/conversation-content-standard.md` (Standards 2 and 3), `llake/index.md` (current wiki state), and `{{SESSION_DIR}}/transcript.md` (the session to process).

**Phase 2 — Analyze the Transcript.** Extract knowledge across four categories:
- Decisions (ADRs): approach chosen over alternatives, tradeoff discussions
- Gotchas: non-obvious behaviors, surprising interactions, foot-guns
- Playbook: troubleshooting procedures, diagnostic steps
- Plans/Proposals: future work goes only in the discussion Key Facts block — not in separate pages

**Phase 3 — Read Existing Pages Before Writing.** For every page planned for update, read the current content first. Check `wiki/discussions/discussions.md` to detect if this session continues a prior topic (continuation detection).

**Phase 4 — Write Pages.** Create or update decision, gotcha, and playbook pages. Follow page format from `schema/core.md`. Update `updated:` frontmatter. Add `[[wikilinks]]`.

**Phase 5 — Create or Append Discussion Entry.** The discussion entry is the canonical record of the session.
- **New entry**: `YYYY-MM-DD-topic.md` in `wiki/discussions/`, with `status: immutable`, Key Facts block, summary, and links.
- **Continuation**: append to the existing file after a `---` separator with `## Continuation: ...` header, new Key Facts block, summary, and pages list. Do NOT touch any content above the separator.

**Phase 6 — Update Indexes and Log.** Update category index files, `discussions/discussions.md`, and append to `llake/log.md`.

## Template Excerpt

```markdown
## Standards (loaded from schema)

- **Standard 2 — Conversation-content fidelity** — defined in `{{SCHEMA_DIR}}/conversation-content-standard.md`.
  The discussion entry's **Key Facts (immutable)** block is the load-bearing rule.
- **Standard 3 — Security (no sensitive data)** — overrides every other content guideline,
  including Key-Facts immutability (redaction of secrets is allowed and required).

## Category Boundary

You may ONLY write to these categories:
{{WRITABLE_CATEGORIES}}

Do not write to any other directory.
```

## Key Points

- The Key Facts block in every discussion entry is immutable after creation — this is the core archival guarantee of session capture.
- `PARTIAL` classification means the agent must filter the transcript before writing — only project-relevant knowledge is extracted.
- The write-surface boundary (`{{WRITABLE_CATEGORIES}}`) is injected at runtime and comes from `sessionCapture.writableCategories` in config — it can be adjusted per project.
- Plans and future work belong exclusively in the discussion Key Facts block. The capture agent does not create speculative wiki pages; the ingest agent updates pages when code actually lands.
- Continuation detection prevents duplicate discussion files for the same topic arc — the agent tests whether the session slug would be named `<existing>-impl`, `<existing>-part2`, etc.
- Standard 3 (security) is the absolute override: it permits redaction of Key Facts content even though the block is otherwise immutable.

## Code References

- `hooks/prompts/capture.md.tmpl` — the full template
- `hooks/session-end.sh:290-302` — capture prompt rendering (all 9 KEY=value args)
- `hooks/session-end.sh:311-317` — capture agent spawn with `--allowedTools` and `--max-budget-usd`
- `hooks/session-end.sh:87-88` — `ALLOWED_TOOLS_JSON` and `WRITABLE_CATS_JSON` read from config

## See Also

- [[triage-template]] — the first-pass agent that decides whether capture runs
- [[session-end-hook]] — the shell hook that orchestrates both triage and capture
- [[template-system]] — how `{{VAR}}` placeholders are resolved
- [[adr-002-two-pass-triage]] — rationale for the two-pass architecture
- [[extract-transcript]] — produces `transcript.md` that both agents read
- [[config-schema]] — `sessionCapture.*` config keys controlling model, budget, tools, and categories
