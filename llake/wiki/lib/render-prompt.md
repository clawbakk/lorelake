---
title: "render-prompt.py"
description: "Strict {{VAR}} placeholder substitution for prompt template files"
tags: [lib, prompts, shell-interface]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[render-prompt-strict-exit]]"
  - "[[add-new-prompt-placeholder]]"
  - "[[ingest-template]]"
  - "[[capture-template]]"
  - "[[triage-template]]"
---

## Overview

`hooks/lib/render-prompt.py` turns `.md.tmpl` template files into finished agent prompts by substituting `{{VAR}}` placeholders. It is invoked by every hook that spawns a background agent immediately before calling `claude -p`. The rendered output goes to stdout; the hook pipes it directly into the agent invocation.

The renderer is deliberately strict: any placeholder that survives all three substitution passes causes an immediate nonzero exit. This prevents silent prompt corruption — if a required variable is missing, the hook fails loudly rather than passing a broken prompt to the agent.

## CLI Interface

```
python3 hooks/lib/render-prompt.py \
    [--templates-dir DIR] \
    <template.md.tmpl> \
    <config.json> \
    [KEY=value ...]
```

| Argument | Description |
|---|---|
| `--templates-dir DIR` | Optional. Directory used to resolve relative fallback paths in `{{VAR\|fallback:path}}` markers. |
| `<template.md.tmpl>` | Path to the template file. The filename (minus `.md.tmpl`) determines which `config.prompts` section to read. |
| `<config.json>` | Path to the project's `llake/config.json` (may be missing — treated as `{}`). |
| `KEY=value ...` | Runtime substitutions. Any number of `KEY=value` pairs. |

**Output**: rendered prompt text on stdout.

**Exit codes**:

| Code | Meaning |
|---|---|
| `0` | All placeholders resolved; rendered prompt on stdout. |
| `1` | One or more `{{VAR}}` placeholders remain unresolved after all passes. Names listed on stderr. |
| `2` | Template file could not be read. |

## Placeholder Syntax

Templates use two forms:

```
{{VAR}}                   Simple placeholder — must be supplied via CLI arg or config slot.
{{VAR|fallback:path}}     With fallback — reads the file at path if VAR is not provided or the slot is empty.
```

Variable names must match `[A-Z_][A-Z0-9_]*` (uppercase only).

## Three-Pass Substitution

Substitution runs in a single `re.sub` call, but the resolution logic inside follows a strict priority order for each placeholder:

**Pass 1 — Runtime CLI vars**: `KEY=value` pairs from the command line. Highest priority; always wins over config and fallbacks.

**Pass 2 — Per-template config slots**: The template name drives the lookup section. `ingest.md.tmpl` reads from `config.prompts.ingest.*`; `capture.md.tmpl` reads from `config.prompts.capture.*`. A slot with an empty string value is treated as absent (falls through to pass 3).

**Pass 3 — Fallback file**: For `{{VAR|fallback:path}}` markers only. The path is resolved against `--templates-dir` if relative. The file content replaces the placeholder. If the file is unreadable, the placeholder is left as-is and will trigger the strict-exit check.

If none of the three passes resolves a placeholder, it stays in the output verbatim. After all substitutions complete, the renderer scans for any remaining `{{...}}` matches and exits nonzero if found.

## Example Templates and Invocations

**Template snippet** (`hooks/prompts/ingest.md.tmpl`):

```markdown
You are the LoreLake ingest agent. Analyse the diff for range {{COMMIT_RANGE}}.
Project root: {{PROJECT_ROOT}}

{{EXAMPLES|fallback:generic-examples.md}}
```

**Invocation from the hook**:

```bash
python3 "$PLUGIN_ROOT/hooks/lib/render-prompt.py" \
    --templates-dir "$PLUGIN_ROOT/templates" \
    "$PLUGIN_ROOT/hooks/prompts/ingest.md.tmpl" \
    "$CONFIG_PATH" \
    "PROJECT_ROOT=$PROJECT_ROOT" \
    "COMMIT_RANGE=$COMMIT_RANGE"
```

If the user has set `config.prompts.ingest.EXAMPLES` in their `config.json`, that text replaces `{{EXAMPLES}}`. Otherwise the file `$PLUGIN_ROOT/templates/generic-examples.md` is read and inlined.

**Overriding a slot from user config** (`llake/config.json`):

```json
{
  "prompts": {
    "ingest": {
      "EXAMPLES": "Always prefer small atomic commits."
    }
  }
}
```

## Test Coverage (from `tests/lib/test_render_prompt.py`)

```python
# Runtime var substitution
render(tmpl, cfg, "PROJECT_ROOT=/tmp/proj", "COMMIT_RANGE=abc..def")
# → "Project: /tmp/proj\nRange: abc..def"

# Custom slot from config (prompts.ingest.EXAMPLES)
render(tmpl, cfg)   # config has {"prompts": {"ingest": {"EXAMPLES": "An example body."}}}
# → "Examples:\nAn example body."

# Fallback used when slot is empty string
render(tmpl, cfg)   # config has EXAMPLES="" → reads fallback file
# → "Examples:\nGENERIC FALLBACK CONTENT"

# Fallback NOT used when slot is filled
render(tmpl, cfg)   # config has EXAMPLES="REAL" → "REAL" wins
# → "Examples:\nREAL"

# Unresolved placeholder → nonzero exit, name on stderr
render(tmpl, cfg)   # template has {{UNFILLED}}, not in config or args
# rc=1, stderr contains "UNFILLED"

# Template filename drives section: capture.md.tmpl → prompts.capture.*
render(capture_tmpl, cfg)   # config has {"prompts": {"capture": {"NOTE": "from capture section"}}}
# → "from capture section"

# Relative fallback path resolved against --templates-dir
cmd = [..., "--templates-dir", str(templates_dir), str(tmpl), str(cfg)]
# reads templates_dir/generic-examples.md
```

## Wiring Contract

Every `{{VAR}}` added to a template must be wired in two places:

1. **The template file** — declare the placeholder.
2. **The calling hook script** — pass `KEY=value` on the `render-prompt.py` command line, OR ensure the key exists in the relevant `config.prompts.<name>` section, OR provide a `|fallback:` path.

Failing to wire both sides causes the hook to exit 1 at runtime when it tries to render the prompt. See [[add-new-prompt-placeholder]] for the step-by-step procedure, and [[render-prompt-strict-exit]] for the gotcha and how to diagnose it.

## Key Points

- Strict exit on unresolved placeholders is intentional — a broken prompt reaching an agent is worse than a hook that fails fast.
- Template section name is derived purely from the filename: `ingest.md.tmpl` → `ingest`, `capture.md.tmpl` → `capture`. The path does not matter, only the basename.
- An empty string in a config slot is treated the same as absent — it falls through to the fallback file. Use a non-empty string to override.
- `--templates-dir` only affects relative fallback paths; absolute paths in `|fallback:` are always used as-is.
- Rendered output goes to stdout only; use shell redirection or a pipe to pass it to `claude -p`.

## Code References

| Symbol | Location |
|---|---|
| `PLACEHOLDER_RE` regex | `hooks/lib/render-prompt.py:23` |
| `template_section_name()` | `hooks/lib/render-prompt.py:34-42` |
| `parse_runtime_vars()` | `hooks/lib/render-prompt.py:44-50` |
| `resolve()` inner function | `hooks/lib/render-prompt.py:74-93` |
| Strict-exit check | `hooks/lib/render-prompt.py:97-101` |
| Test suite | `tests/lib/test_render_prompt.py` |

## See Also

- [[render-prompt-strict-exit]] — gotcha page: what the nonzero exit means and how to debug it
- [[add-new-prompt-placeholder]] — playbook: how to add a new `{{VAR}}` safely end-to-end
- [[ingest-template]] — the ingest prompt template that this tool renders
- [[capture-template]] — the capture prompt template
- [[triage-template]] — the triage prompt template
