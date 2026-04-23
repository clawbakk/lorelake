---
title: "Triage Prompt Template"
description: "The cheap first-pass session-capture prompt that classifies sessions as CAPTURE/PARTIAL/SKIP"
tags: [templates, session-capture, triage]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[session-end-hook]]"
  - "[[capture-template]]"
  - "[[adr-002-two-pass-triage]]"
  - "[[template-system]]"
---

## Overview

The triage template (`hooks/prompts/triage.md.tmpl`) drives the first pass of session capture. It instantiates a cheap, short-lived agent whose sole job is to read a session transcript and output a single classification word — `CAPTURE`, `PARTIAL`, or `SKIP` — with a brief rationale. The triage agent never writes files. Its output controls whether the more expensive capture agent runs at all.

This two-pass design exists to bound background costs: every Claude Code session end fires the hook, but most sessions are noise (greetings, status checks, pure code generation). Running the full capture agent on every session would be wasteful. The triage agent is intentionally constrained to `--max-budget-usd 0.50` and `--allowedTools Read` to keep it fast and cheap. See [[adr-002-two-pass-triage]] for the design rationale.

## Placeholders

The triage template contains one `{{VAR}}` placeholder:

| Placeholder | Filled by | Value |
|---|---|---|
| `{{SESSION_DIR}}` | CLI arg from `session-end.sh` | Absolute path to the session working directory, e.g. `<project>/llake/.state/sessions/<session-id>/` |

The template instructs the agent to read `{{SESSION_DIR}}/transcript.md` — the sampled transcript produced by `extract_transcript.py` before the hook spawns either agent.

There are no `config.prompts.triage.*` overrides defined in the default config, and no `{{VAR|fallback:path}}` file-read placeholders in this template. The single placeholder is always supplied at runtime by the hook.

## What the Agent Is Instructed to Do

The template gives the agent four classification rules applied in strict order:

**1. Scope Override (highest priority).** LoreLake meta-infrastructure is always `SKIP`, regardless of what `CLAUDE.md` or other context says. This covers the LoreLake wiki, its state, schema, indexes, logs, the hooks themselves, and the prompt files (including `triage.md.tmpl`). This prevents the capture system from documenting itself.

**2. Relevance Check.** If the session is primarily about the project → `CAPTURE`. If it mixes project-relevant content with unrelated content → `PARTIAL`. If nothing is relevant → `SKIP`.

**3. Noise Filter.** Several categories are always `SKIP` regardless of topic: simple Q&A about already-documented information, greetings and procedural exchanges, pure code generation with no design discussion, and general tooling questions with no project-specific insight.

**4. Fallback.** When unsure between `CAPTURE` and `PARTIAL`, default to `CAPTURE` (err toward preservation). This fallback does not override the Scope Override.

## Output Contract

The agent's response must start with exactly one of `CAPTURE`, `SKIP`, or `PARTIAL`, followed by a colon and a brief reason. No other output is permitted:

```
CAPTURE: debugging session uncovered race condition in subsystem X
SKIP: general git question with no project context
PARTIAL: tooling discussion, but discovered behavior Y in dependency Z
```

The `session-end.sh` hook parses the first line of the triage result file (written by `format-agent-log.py --extract-result`) to extract the classification word and reason. If the result file is missing, the hook defaults to `CAPTURE`.

## Model and Budget

The triage agent runs with:
- **Model**: `sessionCapture.triageModel` from config (default: a lighter/faster model than the capture agent)
- **Effort**: `sessionCapture.triageEffort` from config
- **Budget cap**: hard-coded `--max-budget-usd 0.50` in `session-end.sh` (not configurable)
- **Allowed tools**: `Read` only — the triage agent cannot write anything

Both agents share a single watchdog timer (`sessionCapture.timeoutSeconds`). The watchdog covers the combined triage + capture wall-clock time.

## Template Excerpt

```markdown
## Classification Rules

### Scope Override

Treat LoreLake as meta-infrastructure for the capture system — not as project content...
If the session is primarily about any of the above → **SKIP**.

### Relevance Check

Is this session about this project?
- If **primarily** about the project → **CAPTURE**
- If it **mixes** project-relevant content with unrelated content → **PARTIAL**
- If **nothing** is relevant to the project → **SKIP**

## Output Contract

Your response must start with exactly one of these words: `CAPTURE`, `SKIP`, or `PARTIAL`,
followed by a colon and a brief reason.
Do not output anything else. No narration, no explanation, no extra tool calls.
```

## Key Points

- The triage agent is intentionally minimal: one placeholder, one read tool, one output line, hard-capped budget.
- `SKIP` from triage short-circuits the entire capture pipeline — the session directory is cleaned up and no capture agent spawns.
- `CAPTURE` and `PARTIAL` both proceed to the capture agent. The capture template receives the classification and reason, and uses `PARTIAL` to filter out irrelevant transcript sections.
- The Scope Override rule prevents the capture system from recursively documenting its own sessions — a subtle but critical rule given that LoreLake is often being developed in the same repo.
- Missing triage result defaults to `CAPTURE`, so a triage agent crash does not silently discard sessions.

## Code References

- `hooks/prompts/triage.md.tmpl` — the full template
- `hooks/session-end.sh:244-263` — triage prompt rendering and agent spawn
- `hooks/session-end.sh:268-287` — result parsing and SKIP short-circuit

## See Also

- [[capture-template]] — the full-capture agent that runs when triage returns CAPTURE/PARTIAL
- [[session-end-hook]] — the shell hook that orchestrates both passes
- [[template-system]] — how `{{VAR}}` placeholders are resolved by `render-prompt.py`
- [[adr-002-two-pass-triage]] — rationale for the two-pass design
- [[extract-transcript]] — produces the `transcript.md` the triage agent reads
