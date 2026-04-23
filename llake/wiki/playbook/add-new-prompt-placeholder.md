---
title: "Add a New Prompt Placeholder"
description: "Step-by-step guide for wiring a new {{VAR}} through a prompt template and its calling hook"
tags: [playbook, prompts, render-prompt, hooks, development]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[render-prompt]]"
  - "[[render-prompt-strict-exit]]"
---

# Add a New Prompt Placeholder

LoreLake prompt templates use `{{VAR}}` placeholders that are substituted at runtime by `render-prompt.py`. The renderer is strict: any unresolved placeholder causes a non-zero exit and the hook aborts without spawning an agent. This page walks through every step required to add a new placeholder safely.

## How the Wiring Works

When a hook calls `render-prompt.py`, it passes:

1. A template file path (`hooks/prompts/*.md.tmpl`)
2. The project's `config.json`
3. Zero or more `KEY=value` CLI arguments

The renderer resolves each `{{VAR}}` in this order:

1. **Runtime vars** — `KEY=value` arguments passed on the CLI (highest priority).
2. **Config custom slots** — `config.prompts.<template-name>.<KEY>` in `config.json`.
3. **Fallback files** — `{{VAR|fallback:path/to/file}}` reads a file from disk (resolved against `--templates-dir` when the path is relative).

If none of these resolves a placeholder, the renderer exits 1 with an error. The hook then logs nothing to `agent.log` and writes a crash marker to `hooks.log`.

See [[render-prompt]] for the full resolution algorithm and [[render-prompt-strict-exit]] for why strict exit matters.

## The Four-Step Wiring Contract

All four steps must ship together in the same commit. The strict exit means that updating the template without updating the hook (or vice versa) will break the hook for every user immediately.

### Step 1 — Add `{{VAR}}` to the template

Edit the relevant template file under `hooks/prompts/`:

- `hooks/prompts/ingest.md.tmpl` — for the post-merge ingest agent
- `hooks/prompts/triage.md.tmpl` — for the session-capture triage pass
- `hooks/prompts/capture.md.tmpl` — for the session-capture capture pass

Place the placeholder where the value should appear in the rendered prompt:

```
The current commit range is {{COMMIT_RANGE}}.
```

If the value should have a sensible default, use the fallback form instead:

```
{{EXTRA_CONTEXT|fallback:extra-context.md}}
```

This tells the renderer: if `EXTRA_CONTEXT` is not supplied as a runtime var or config custom slot, read the file at `templates/extra-context.md` (relative paths are resolved against `--templates-dir`, which all hooks set to `<plugin-root>/templates`).

### Step 2 — Pass `KEY=value` in the calling hook script

Find where the hook calls `render-prompt.py` and add the new argument.

For `post-merge.sh`, the call site is:

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

For `session-end.sh`, the triage render call is:

```bash
TRIAGE_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$PROMPTS_DIR/triage.md.tmpl" \
  "$CONFIG_FILE" \
  "SESSION_DIR=$SESSION_DIR")
```

And the capture render call is:

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

Add your new `"MYVAR=$MY_SHELL_VAR"` argument to whichever call site corresponds to your template.

Make sure `$MY_SHELL_VAR` is set before the render call. Derive it from config using `read-config.py` if appropriate:

```bash
MY_SHELL_VAR=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "mySection.myKey")
```

### Step 3 — (If configurable) Add a fallback default

If the new variable is user-configurable and needs a shipped default value:

1. Add the default to `templates/config.default.json` under the appropriate section.
2. Use the fallback form `{{MYVAR|fallback:path/to/default-file.md}}` in the template if the value is long (e.g., a multi-line block of context injected into the prompt). For simple scalar values, pass them via the hook script using `read-config.py` (Step 2 above) — that already handles the config-to-default fallback.

For a file fallback, create the default file under `templates/`:

```bash
# Example: templates/extra-context.md
echo "No extra context configured." > templates/extra-context.md
```

The renderer will read this file if `EXTRA_CONTEXT` is not provided as a CLI var or config custom slot.

### Step 4 — Update the render-prompt test

If `tests/lib/test_render_prompt.py` has tests for the template you edited, add a test case covering the new placeholder:

```python
def test_new_var_substituted(tmp_path):
    tmpl = tmp_path / "ingest.md.tmpl"
    tmpl.write_text("Range: {{COMMIT_RANGE}}\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    result = subprocess.run(
        ["python3", str(RENDER_PROMPT), str(tmpl), str(config), "COMMIT_RANGE=abc..def"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Range: abc..def" in result.stdout
```

Run the tests:

```bash
python3 -m pytest tests/lib/test_render_prompt.py -v
```

## Step 5 — Verify shell syntax

After editing a hook script, always check for syntax errors:

```bash
bash -n hooks/session-end.sh
bash -n hooks/post-merge.sh
```

If `shellcheck` is installed:

```bash
shellcheck hooks/post-merge.sh hooks/session-end.sh
```

## Worked Example: Adding a `{{WIKI_CATEGORIES}}` Variable to ingest.md.tmpl

Suppose you want to inject the list of wiki categories into the ingest agent prompt.

**Before (template excerpt):**

```
Update the wiki under the project's llake/wiki/ directory.
```

**Step 1 — Add the placeholder to `hooks/prompts/ingest.md.tmpl`:**

```
Update the wiki under the project's llake/wiki/ directory.
Writable categories: {{WIKI_CATEGORIES}}.
```

**Step 2 — Compute the value and pass it in `hooks/post-merge.sh`.**

Add before the render call:

```bash
WRITABLE_CATS_JSON=$(python3 "$LIB_DIR/read-config.py" "$CONFIG_FILE" "llake.fixedCategories")
WIKI_CATEGORIES=$(python3 -c "
import json, sys
cats = json.loads(sys.argv[1])
print(', '.join(cats))
" "$WRITABLE_CATS_JSON" 2>/dev/null || echo "discussions, decisions, gotchas, playbook")
```

Then add to the render call:

```bash
INGEST_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$INGEST_PROMPT_TMPL" \
  "$CONFIG_FILE" \
  "AGENT_ID=$AGENT_ID" \
  ...existing args... \
  "WIKI_CATEGORIES=$WIKI_CATEGORIES")
```

**Step 3 — No fallback needed** (value is always computed from config, which has defaults).

**Step 4 — Add a test in `tests/lib/test_render_prompt.py`:**

```python
def test_wiki_categories_substituted(tmp_path):
    tmpl = tmp_path / "ingest.md.tmpl"
    tmpl.write_text("Categories: {{WIKI_CATEGORIES}}\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    result = subprocess.run(
        ["python3", str(RENDER_PROMPT), str(tmpl), str(config),
         "WIKI_CATEGORIES=discussions, decisions, gotchas, playbook"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "discussions" in result.stdout
```

**Step 5 — Verify syntax:**

```bash
bash -n hooks/post-merge.sh
shellcheck hooks/post-merge.sh
```

All five steps complete. Both files change in the same commit.

## What Breaks If You Forget Step 2

If you add `{{WIKI_CATEGORIES}}` to the template but forget to pass `WIKI_CATEGORIES=...` in the hook:

```
render-prompt: unresolved placeholders: WIKI_CATEGORIES
```

The hook logs a crash marker to `hooks.log` (line ends with `→ CRASHED`) and exits. The agent is never spawned. Every user on every project sees this failure until the fix is deployed.

## What Breaks If You Forget Step 1

If you add `WIKI_CATEGORIES=$WIKI_CATEGORIES` to the hook but forget to add `{{WIKI_CATEGORIES}}` to the template, nothing breaks — unused runtime vars are silently ignored. However, the value never reaches the agent, so the change is also pointless.

## Prevention

- Always grep the template for `{{` before finalizing a PR to confirm all placeholders are accounted for:
  ```bash
  grep -o '{{[A-Z_]*' hooks/prompts/ingest.md.tmpl | sort -u
  ```
- Run `python3 -m pytest tests/lib/ -q` before merging.
- Review `bash -n` and shellcheck output before merging hook script changes.

## See Also

- [[render-prompt]] — full renderer reference, resolution order, fallback semantics
- [[render-prompt-strict-exit]] — why strict exit is deliberate and what it prevents
