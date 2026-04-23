---
title: "Template System"
description: "How render-prompt.py resolves {{VAR}} placeholders through CLI args, config overrides, and file fallbacks"
tags: [templates, render-prompt, architecture]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[render-prompt]]"
  - "[[render-prompt-strict-exit]]"
  - "[[triage-template]]"
  - "[[capture-template]]"
  - "[[ingest-template]]"
  - "[[add-new-prompt-placeholder]]"
---

## Overview

LoreLake's three background agents (triage, capture, ingest) are driven by Markdown template files (`*.md.tmpl`). Before spawning any agent, the calling shell hook renders the template into a complete prompt by substituting `{{VAR}}` placeholders with real values. This rendering is handled by a single Python script: `hooks/lib/render-prompt.py`.

The renderer enforces a strict contract: **every placeholder must resolve**. If any `{{VAR}}` remains unresolved after all substitution passes, the script exits nonzero and the hook aborts — the agent is never spawned. This makes misconfiguration immediately visible rather than silently injecting broken prompts.

## Template Files

| Template | Path | Used by |
|---|---|---|
| Triage | `hooks/prompts/triage.md.tmpl` | `session-end.sh` (Pass 1) |
| Capture | `hooks/prompts/capture.md.tmpl` | `session-end.sh` (Pass 2) |
| Ingest | `hooks/prompts/ingest.md.tmpl` | `post-merge.sh` |

Templates live in the plugin repository under `hooks/prompts/`. They are never copied to the target project — the hooks reference them by absolute path via `$PROMPTS_DIR`. Updates to templates reach all installs instantly because there are no per-project copies.

## Placeholder Syntax

Two forms:

```
{{VAR_NAME}}                   — must be supplied by CLI arg or config override
{{VAR_NAME|fallback:path}}     — falls back to reading a file if not otherwise supplied
```

Placeholder names must match `[A-Z_][A-Z0-9_]*`. The regex is: `\{\{([A-Z_][A-Z0-9_]*)(?:\|fallback:([^}]+))?\}\}`.

## Resolution Order

The renderer applies substitutions in this priority order:

### 1. CLI KEY=value args

Runtime values passed directly on the command line take highest priority. Each `KEY=value` argument is split on the first `=`; the key is matched against placeholder names.

Example (from `post-merge.sh`):
```bash
INGEST_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$INGEST_PROMPT_TMPL" \
  "$CONFIG_FILE" \
  "AGENT_ID=$AGENT_ID" \
  "PROJECT_ROOT=$PROJECT_ROOT" \
  "LAST_SHA=$LAST_SHA" \
  "CURRENT_SHA=$CURRENT_SHA" \
  "COMMIT_RANGE=$COMMIT_RANGE" \
  "PATHSPEC_INCLUDE=$PATHSPEC_INCLUDE" \
  "LLAKE_ROOT=$LLAKE_ROOT" \
  "WIKI_ROOT=$WIKI_ROOT" \
  "SCHEMA_DIR=$SCHEMA_DIR")
```

All placeholders that carry runtime-computed values (agent IDs, paths, SHAs, classifications) are injected this way.

### 2. `config.prompts.<template-name>.<KEY>` overrides

After CLI args, the renderer checks the project's `config.json` under the key path `prompts.<template-name>.<KEY>`. The template name is derived from the filename: `ingest.md.tmpl` → `ingest`, `capture.md.tmpl` → `capture`, `triage.md.tmpl` → `triage`.

This mechanism lets project owners customize prompt content without modifying plugin files. Currently the primary use case is the `EXAMPLES` slot in the ingest template:

```json
{
  "prompts": {
    "ingest": {
      "EXAMPLES": "### Example: auth token rotation\nCommit: feat: rotate JWT secrets..."
    }
  }
}
```

A non-empty config value overrides any fallback file. An empty string or absent key does not override.

### 3. `{{KEY|fallback:path}}` file reads

If a placeholder has a `|fallback:path` component and was not resolved by CLI args or config, the renderer reads the file at that path. Path resolution:

- **Absolute path**: used as-is.
- **Relative path**: resolved against the `--templates-dir` argument if provided.

In practice, all fallback paths in the current templates are relative (`generic-examples.md`), and `--templates-dir` is always set to `$TEMPLATES_DIR` (the plugin's `templates/` directory).

If the file cannot be read, the placeholder is left unresolved — which triggers the strict-exit check.

### If nothing resolves

If a placeholder reaches the end of all three passes without a value, it is left as-is in the rendered text. The post-substitution scan finds any remaining `{{VAR}}` patterns and prints them to stderr:

```
render-prompt: unresolved placeholders: AGENT_ID, SESSION_DIR
```

The script then exits with code 1. The calling hook treats this as a fatal error and does not spawn the agent. See [[render-prompt-strict-exit]] for the failure mode and recovery.

## Command-Line Interface

```
render-prompt.py [--templates-dir DIR] <template> <config> [VAR=value ...]
```

| Argument | Role |
|---|---|
| `--templates-dir DIR` | Directory for resolving relative fallback paths |
| `<template>` | Path to the `.md.tmpl` file |
| `<config>` | Path to the project's `config.json` (used for `prompts.*` overrides) |
| `VAR=value ...` | Runtime substitutions (may be zero or more) |

Output is the rendered prompt on stdout. Errors go to stderr.

## The `--templates-dir` Flag

The flag decouples the template location from the fallback file location. In the current setup both live under the plugin root, but they are separate directories:

- Templates: `hooks/prompts/`
- Fallback files: `templates/`

The hooks set `--templates-dir "$TEMPLATES_DIR"` (the `templates/` directory), so a fallback like `generic-examples.md` resolves to `<plugin>/templates/generic-examples.md`. The template file itself is passed as an absolute path and does not use `--templates-dir`.

## Complete Rendering Call Pattern

**session-end.sh — triage pass** (one placeholder):
```bash
TRIAGE_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$PROMPTS_DIR/triage.md.tmpl" \
  "$CONFIG_FILE" \
  "SESSION_DIR=$SESSION_DIR")
```

**session-end.sh — capture pass** (nine placeholders):
```bash
CAPTURE_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$PROMPTS_DIR/capture.md.tmpl" \
  "$CONFIG_FILE" \
  "AGENT_ID=$CAPTURE_AGENT_ID" \
  "PROJECT_ROOT=$PROJECT_ROOT" \
  "TRIAGE_CLASSIFICATION=$CLASSIFICATION" \
  "TRIAGE_REASON=$TRIAGE_REASON" \
  "WRITABLE_CATEGORIES=$WRITABLE_CATS_LIST" \
  "LLAKE_ROOT=$LLAKE_ROOT" \
  "WIKI_ROOT=$WIKI_ROOT" \
  "SCHEMA_DIR=$SCHEMA_DIR" \
  "SESSION_DIR=$SESSION_DIR")
```

**post-merge.sh — ingest pass** (nine placeholders + one fallback slot):
```bash
INGEST_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$INGEST_PROMPT_TMPL" \
  "$CONFIG_FILE" \
  "AGENT_ID=$AGENT_ID" \
  "PROJECT_ROOT=$PROJECT_ROOT" \
  "LAST_SHA=$LAST_SHA" \
  "CURRENT_SHA=$CURRENT_SHA" \
  "COMMIT_RANGE=$COMMIT_RANGE" \
  "PATHSPEC_INCLUDE=$PATHSPEC_INCLUDE" \
  "LLAKE_ROOT=$LLAKE_ROOT" \
  "WIKI_ROOT=$WIKI_ROOT" \
  "SCHEMA_DIR=$SCHEMA_DIR")
```

In the ingest call, `{{EXAMPLES|fallback:generic-examples.md}}` is not passed as a CLI arg — it resolves through config or file fallback automatically.

## Strict-Exit Contract and Its Implications

The strict-exit behavior is the most important operational property of the renderer. It means:

**Adding a new `{{VAR}}` to a template without also wiring it in the calling hook causes an immediate hook failure.** The hook will abort before spawning any agent. This is intentional — a broken prompt that silently injects `{{UNRESOLVED_VAR}}` into an agent would produce garbage output with no error signal. The strict exit surfaces the problem at the hook level where it can be seen in `hooks.log`.

The development workflow when adding a new placeholder:
1. Add `{{NEW_VAR}}` (or `{{NEW_VAR|fallback:path}}`) to the template.
2. Add the corresponding `"NEW_VAR=$VALUE"` arg to the hook's `render-prompt.py` call.
3. Run `bash -n hooks/session-end.sh` (or `post-merge.sh`) for syntax check.
4. Run the Python unit tests to verify render-prompt's fallback logic still works.

See [[add-new-prompt-placeholder]] for the step-by-step playbook.

## Key Points

- Three substitution passes in priority order: (1) CLI KEY=value, (2) `config.prompts.<name>.<KEY>`, (3) `{{KEY|fallback:path}}` file read.
- Strict exit on any unresolved placeholder — no silent failures.
- `--templates-dir` resolves relative fallback paths; it is always set to the plugin's `templates/` directory by the hooks.
- Template name is inferred from the filename (`ingest.md.tmpl` → `ingest`) for the config override lookup.
- Templates live only in the plugin repo — no per-project copies — so plugin updates propagate instantly.
- The config override mechanism (`config.prompts.<name>.<KEY>`) is the correct way for project owners to customize prompt content.

## Code References

- `hooks/lib/render-prompt.py` — the renderer; full source
- `hooks/lib/render-prompt.py:23` — `PLACEHOLDER_RE` regex
- `hooks/lib/render-prompt.py:34-41` — template section name derivation
- `hooks/lib/render-prompt.py:74-93` — `resolve()` function implementing the three-pass order
- `hooks/lib/render-prompt.py:96-101` — leftover scan and strict-exit
- `hooks/session-end.sh:244-248` — triage render call
- `hooks/session-end.sh:290-302` — capture render call
- `hooks/post-merge.sh:209-221` — ingest render call

## See Also

- [[render-prompt]] — detailed reference for `render-prompt.py` including all flags and error codes
- [[render-prompt-strict-exit]] — gotcha: what happens when a placeholder is unresolved and how to recover
- [[triage-template]] — triage template placeholders
- [[capture-template]] — capture template placeholders
- [[ingest-template]] — ingest template placeholders including the EXAMPLES fallback slot
- [[add-new-prompt-placeholder]] — step-by-step playbook for adding a new `{{VAR}}` to any template
- [[config-schema]] — `config.prompts.*` override keys
