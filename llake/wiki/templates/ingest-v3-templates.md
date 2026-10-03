---
title: "Ingest v3 Prompt Templates"
description: "The five ingest v3 prompt templates, how they are rendered in-process through render_text, and the JSON schemas that constrain agent output"
tags: [templates, ingest, v3, prompts, render-prompt]
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[render-prompt]]"
  - "[[template-system]]"
  - "[[render-prompt-strict-exit]]"
  - "[[ingest-v3-brief]]"
  - "[[ingest-v3-writers]]"
---
# Ingest v3 Prompt Templates

## Overview

Ingest v3 uses five templates in `hooks/prompts/`. Its Python code fills them through the same strict renderer the shell hooks use. The renderer runs in-process instead of through a CLI call, and a stdlib validator checks the agents' JSON output against schemas in `hooks/lib/ingest_v3/schemas/`.

## Templates

| Template | Agent | Placeholders | Filled by |
|---|---|---|---|
| `ingest.v3.analysis.md.tmpl` | analysis | `BRIEF_DIR`, `INPUTS`, `BASE`, `HEAD`, `INCLUDE`, `COMMITS`, `NUMSTAT`, `REMOVED_NAMES`, `ADDED_NAMES`, `HIT_CAP`, `HIT_INDEX`, `ANCHOR_INDEX`, `LITERAL_INDEX`, `OWED` | `stage.analysis_prompt` |
| `ingest.v3.recall.md.tmpl` | recall | `BRIEF_DIR`, `INPUTS`, `THEMES`, `LISTED`, `CONSIDERED` | `stage.recall_prompt` |
| `ingest.v3.writer-shared.md.tmpl` | writers and fixers (system prompt prefix) | `TODAY`, `MODE_RULE`, `THEMES`, `REMOVED_NAMES`, `ADDED_NAMES`, `CATALOG` | `prompts.shared_prefix` |
| `ingest.v3.writer-bundle.md.tmpl` | writers and fixers (task message) | `TASK`, `BASE`, `HEAD`, `PATCHES`, `PAGE_LIST`, `PAGES` | `prompts.bundle_prompt` |
| `ingest.v3.verifier.md.tmpl` | verifier | `BASE`, `HEAD`, `CHECKS`, `PATCHES`, `PAGES` | `prompts.verifier_prompt` |

The main content of each template:

- **Analysis.** Its job, its tools (no shell, `Write` only inside the brief dir) and the staged inputs. It defines change theme, directly and thematically affected page, and stale claim. It sets the scope rules for discussions, decisions, gotchas, category indexes and `index.md`. It gives the working method (parallel reads, write the brief incrementally) and the exact JSON shape of every brief file. It ends with the reply `BRIEF WRITTEN: <n> pages`.
- **Writer shared prefix.** The ten-point writer contract (see [[ingest-v3-writers]]), an efficiency note with the `writeMode` rule, the themes, the names and the catalog of valid link targets.
- **Writer bundle.** The task (`bring your pages up to date with the code at head`, or for a fixer `fix what was flagged on your pages after writing`), the range, the patches dir and one block per owned page.
- **Verifier.** The checks text for the chosen mode, `accuracy` and optionally `residual`, followed by each page's diff and brief claims.

## Rendering — `hooks/lib/ingest_v3/render.py`

- `hooks/lib/render-prompt.py` has a hyphen in its name, so it cannot be imported normally. `render.py` loads it once with `importlib` from its path and calls `render_text` (see [[render-prompt]]).
- Values are converted with `str()` and passed in-process. Large values, such as the catalog or the page blocks, never touch argv.
- Unresolved placeholders raise `RenderError`, keeping the renderer's strictness (see [[render-prompt-strict-exit]]). An unknown template name also raises.
- Callers pass no config, so **`config.prompts.*` custom slots do not apply to v3 templates**. Relative `|fallback:` paths resolve against the plugin's `templates/`.
- Every rendered prompt is saved next to the agent's stream as `stages/<stage>.prompt.md`. The analysis and recall prompts are also saved as `analysis.prompt.md` and `recall.prompt.md`.

To add a placeholder to a v3 template, add it in the template and in the Python function that renders it. `tests/lib/test_v3_prompts_render.py` and `tests/lib/test_v3_writer_prompts.py` render every template and catch a missing value.

## The writer prefix and prompt caching

The shared prefix is identical for every writer and fixer in a run. It is written once to `stages/writer.shared.md` and passed with `--append-system-prompt-file`, while the per-bundle part goes on stdin. The pool lets the first writer reach its first turn alone, so later writers read the prefix from cache.

## Structured-output schemas

| Schema | Used for |
|---|---|
| `brief-page.json` | each analysis/recall page entry (`path`, `severity`, `reason`, `stale` required) |
| `brief-themes.json` | `brief/themes.json` |
| `writer-status.json` | writer/fixer final status, passed as `--json-schema` (`pages[]` with `status` `corrected`/`declared-gap`/`no-change`, `claimsLeft`, `rejected`, `note`; plus `otherStale[]`) |
| `verifier-findings.json` | verifier output, passed as `--json-schema` |
| `gap-record.json` | `llake/ingest-gaps.json` |

`hooks/lib/ingest_v3/schema.py` validates them with no third-party dependency. It supports the subset the files use: `type` (including lists), `enum`, `minLength`, `required`, `properties`, `additionalProperties` (boolean or schema), `minItems`, `items` and local `$ref` to `#/$defs/...`. Anything else raises `ValueError`.

## Key Points

- The five v3 templates are rendered in-process through `render_text`, with the same strictness as the CLI renderer.
- `config.prompts.*` slots are not applied to v3 templates.
- The writer prompt is a cached shared prefix (`--append-system-prompt-file`) plus a per-bundle task message.
- Agent JSON output is constrained by `--json-schema` and validated again in code with a stdlib subset validator.

## Code References

- `hooks/lib/ingest_v3/render.py:35` — `render`
- `hooks/lib/ingest_v3/schema.py` — `load_schema`, `validate`
- `hooks/lib/ingest_v3/stage.py:95` — analysis prompt values
- `hooks/lib/ingest_v3/prompts.py:22` — shared prefix values and `MODE_RULES`
- `hooks/prompts/ingest.v3.analysis.md.tmpl`, `hooks/prompts/ingest.v3.recall.md.tmpl`, `hooks/prompts/ingest.v3.writer-shared.md.tmpl`, `hooks/prompts/ingest.v3.writer-bundle.md.tmpl`, `hooks/prompts/ingest.v3.verifier.md.tmpl`
- `tests/lib/test_v3_render.py`, `tests/lib/test_v3_prompts_render.py`, `tests/lib/test_v3_writer_prompts.py`, `tests/lib/test_v3_schema.py`

## See Also

- [[render-prompt]] — `render_text` and the CLI
- [[template-system]] — the placeholder resolution order
- [[ingest-v3-brief]] — what the analysis and recall agents produce
- [[ingest-v3-writers]] — how writer and verifier prompts are used
