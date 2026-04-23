---
title: "render-prompt.py Strict Exit on Unresolved Placeholders"
description: "Unresolved {{VAR}} in a template causes nonzero exit — must wire both template and hook caller together"
tags: [gotchas, render-prompt, templates, hooks]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[render-prompt]]"
  - "[[add-new-prompt-placeholder]]"
---

# render-prompt.py Strict Exit on Unresolved Placeholders

## What It Is

`hooks/lib/render-prompt.py` exits with code `1` if any `{{VAR}}` placeholder in a template file remains unresolved after all three substitution passes. This is an intentional, hard failure — not a warning. If the calling hook does not capture the exit code and act on it, the empty or partial prompt is silently never passed to the agent.

The three substitution passes are:

1. Runtime vars from the hook's CLI invocation (`KEY=value` positional args to `render-prompt.py`)
2. Custom slots from `config.json` under `prompts.<template-name>.<KEY>`
3. Fallback file content via `{{KEY|fallback:path}}` markers

If none of the three passes resolve a placeholder, the script prints to stderr and exits nonzero:
```
render-prompt: unresolved placeholders: NEW_VAR
```

The exact check from `hooks/lib/render-prompt.py` (lines 97–101):
```python
leftovers = PLACEHOLDER_RE.findall(rendered)
if leftovers:
    unresolved = sorted({m[0] for m in leftovers})
    print(f"render-prompt: unresolved placeholders: {', '.join(unresolved)}", file=sys.stderr)
    sys.exit(1)
```

## Why It Exists

Prompt templates contain both static text and dynamic values that must be filled in at runtime — project root paths, agent IDs, commit ranges, wiki roots, schema directories, and more. Silently passing a prompt with literal `{{PROJECT_ROOT}}` strings to the Claude CLI would produce an agent that either hallucinates wrong paths or errors out in a way that is hard to diagnose. Strict exit surfaces the wiring mistake immediately at the hook level, where it is easy to find and fix.

## Symptoms

- A hook (`session-end.sh`, `post-merge.sh`) exits with a nonzero status without spawning an agent.
- The agent is never spawned; nothing shows up in `llake/.state/hooks.log` beyond the hook's `started` line.
- If you run the hook manually and inspect stderr, you see:
  ```
  render-prompt: unresolved placeholders: NEW_VAR
  ```
- Session capture or ingest stops working entirely after a template edit, even though the edit looks correct in isolation.

## The Fix

**The wiring contract:** whenever you add a `{{NEW_VAR}}` to a `.md.tmpl` file, you must simultaneously add `NEW_VAR=<value>` to the `render-prompt.py` invocation in the calling hook. The template and the hook caller are a matched pair — you cannot change one without the other.

**Example: adding `{{SCHEMA_DIR}}` to `ingest.md.tmpl`**

Step 1 — Template (`hooks/prompts/ingest.md.tmpl`) after the edit:
```
Your schema reference is at {{SCHEMA_DIR}}.
```

Step 2 — Hook invocation (`hooks/post-merge.sh`) — the broken state (missing the new var):
```bash
INGEST_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$INGEST_PROMPT_TMPL" \
  "$CONFIG_FILE" \
  "AGENT_ID=$AGENT_ID" \
  "PROJECT_ROOT=$PROJECT_ROOT" \
  "LAST_SHA=$LAST_SHA" \
  "CURRENT_SHA=$CURRENT_SHA")
# ^^^ {{SCHEMA_DIR}} is in the template but not passed here — render-prompt.py exits 1
```

Step 3 — Correct state (var added to caller):
```bash
INGEST_PROMPT=$(python3 "$LIB_DIR/render-prompt.py" \
  --templates-dir "$TEMPLATES_DIR" \
  "$INGEST_PROMPT_TMPL" \
  "$CONFIG_FILE" \
  "AGENT_ID=$AGENT_ID" \
  "PROJECT_ROOT=$PROJECT_ROOT" \
  "LAST_SHA=$LAST_SHA" \
  "CURRENT_SHA=$CURRENT_SHA" \
  "SCHEMA_DIR=$SCHEMA_DIR")   # <-- added
```

The actual `post-merge.sh` invocation (lines 209–221) shows the full set of vars that must be passed for the ingest template. The `session-end.sh` capture invocation (lines 290–302) shows the equivalent for the capture template. Both are the reference for what a complete, wired invocation looks like.

**If you use a `{{KEY|fallback:path}}` form instead of a plain `{{VAR}}`**, the fallback file path is resolved relative to `--templates-dir` when the path is not absolute. The placeholder is still considered unresolved if the fallback file does not exist, so the same strict-exit applies.

**Testing the render step in isolation:**
```bash
python3 hooks/lib/render-prompt.py \
  --templates-dir templates \
  hooks/prompts/ingest.md.tmpl \
  llake/config.json \
  AGENT_ID=test-001 \
  PROJECT_ROOT=/tmp/test \
  LAST_SHA=abc1234 \
  CURRENT_SHA=def5678 \
  COMMIT_RANGE=abc1234..def5678 \
  PATHSPEC_INCLUDE="" \
  LLAKE_ROOT=/tmp/test/llake \
  WIKI_ROOT=/tmp/test/llake/wiki \
  SCHEMA_DIR=/path/to/plugin/schema
```

A clean exit (code 0) means every placeholder is resolved. Any exit code 1 output on stderr identifies exactly which vars are missing.

## See Also

- [[render-prompt]] — full reference for the three substitution passes and the `{{KEY|fallback:path}}` syntax
- [[add-new-prompt-placeholder]] — step-by-step playbook for safely adding a new placeholder end-to-end
