---
title: "Configuration Schema"
description: "Complete reference for all config.json keys, defaults, types, and effects"
tags: [config, reference, schema]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[config-layering]]"
  - "[[read-config]]"
  - "[[session-end-hook]]"
  - "[[post-merge-hook]]"
  - "[[session-start-hook]]"
  - "[[three-writer-model]]"
---

# Configuration Schema

## Overview

LoreLake is configured via a single JSON file: `<project>/llake/config.json`. Users only need to include keys they want to override; every omitted key falls back to the shipped defaults in `templates/config.default.json` inside the plugin repo. This document is the complete reference for every key in that defaults file — what each key does, its type, its default value, and what breaks if the value is wrong or missing.

For the technical layering contract (how the two files are merged at read time), see [[config-layering]].

---

## Full default structure (annotated)

The file below is `templates/config.default.json` with inline commentary explaining every field.

```jsonc
{
  // Metadata fields — not live config values; never looked up by hooks.
  "_comment": "LoreLake configuration. Defaults shown here apply when a key is omitted from the project's config.json.",
  "_schemaVersion": 1,

  // ── llake ────────────────────────────────────────────────────────────────
  "llake": {
    "_comment": "LoreLake-wide constants.",
    "fixedCategories": ["discussions", "decisions", "gotchas", "playbook"]
  },

  // ── sessionCapture ───────────────────────────────────────────────────────
  "sessionCapture": {
    "_comment": "Two-pass session capture: cheap triage → full capture on CAPTURE/PARTIAL.",
    "enabled": true,
    "triageModel": "sonnet",
    "triageEffort": "high",
    "triageBudgetUsd": 0.50,
    "captureModel": "sonnet",
    "captureEffort": "high",
    "maxBudgetUsd": 5.00,
    "timeoutSeconds": 600,
    "minTurns": 2,
    "minWords": 150,
    "allowedTools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
    "writableCategories": ["discussions", "decisions", "gotchas", "playbook"],
    "lockStalenessSeconds": 900
  },

  // ── ingest ───────────────────────────────────────────────────────────────
  "ingest": {
    "_comment": "Post-merge agent that updates wiki from code changes on the monitored branch.",
    "enabled": true,
    "model": "opus",
    "effort": "high",
    "maxBudgetUsd": 10.00,
    "timeoutSeconds": 1200,
    "branch": "main",
    "include": ["src/"],
    "allowedTools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
  },

  // ── lint ─────────────────────────────────────────────────────────────────
  "lint": {
    "_comment": "On-demand `/llake-lint` skill. Quick mode runs in-session; these knobs apply only to Comprehensive-mode subagents and the no-arg recommendation heuristic.",
    "model": "sonnet",
    "effort": "high",
    "comprehensive-recommended-after-days": 14,
    "comprehensive-recommended-after-activity": 20,
    "stale-threshold-days": 30
  },

  // ── transcript ───────────────────────────────────────────────────────────
  "transcript": {
    "_comment": "Transcript extraction & sampling.",
    "maxMessageLength": 2000,
    "headSize": 10,
    "tailSize": 20,
    "middleMaxSize": 30,
    "middleScaleStart": 100
  },

  // ── logging ──────────────────────────────────────────────────────────────
  "logging": {
    "_comment": "Hook log rotation for llake/.state/hooks.log.",
    "maxLines": 1000,
    "rotateKeepLines": 500
  },

  // ── prompts ──────────────────────────────────────────────────────────────
  "prompts": {
    "_comment": "Per-prompt custom slot overrides. Empty/missing → template falls back to shipped defaults.",
    "ingest": {
      "EXAMPLES": ""
    }
  }
}
```

---

## Section reference

### Metadata fields

These keys appear at the top level of `config.default.json` but are **not live config values**. The `read-config.py` script performs dot-key lookups — you can technically look them up, but no hook ever does. They exist for documentation purposes only.

| Key | Type | Value | Purpose |
|-----|------|-------|---------|
| `_comment` | string | (informational) | Human note; ignored by all runtime code. |
| `_schemaVersion` | integer | `1` | Declares the schema generation. Reserved for future migration logic; not read by any hook today. |

---

### `llake` section

LoreLake-wide constants that describe the shape of the wiki itself.

#### `llake.fixedCategories`

| Attribute | Value |
|-----------|-------|
| Type | `string[]` |
| Default | `["discussions", "decisions", "gotchas", "playbook"]` |
| Read by | bootstrap skill, session capture prompt, ingest prompt |

The four canonical wiki categories that every LoreLake install ships. These directory names are treated as stable across all projects. Bootstrap creates them; session capture and ingest agents may write into them.

**What breaks if wrong:** If you remove a category name from this list, the bootstrap skill may not create the corresponding directory. Capture/ingest agents will still try to write to the path they know from their prompts, so pages will be written regardless — but the category will not appear in the index's generated table. Adding an entry here has no automatic effect; it is advisory metadata.

---

### `sessionCapture` section

Controls the two-pass background agent that fires at `SessionEnd`. See [[session-end-hook]] for the hook implementation.

#### `sessionCapture.enabled`

| Attribute | Value |
|-----------|-------|
| Type | `boolean` |
| Default | `true` |

Master switch. Set to `false` to disable all session capture entirely — neither the triage nor the capture agent will be spawned.

**What breaks if wrong:** Setting to `true` when you don't want capture wastes API budget on every session end. Setting to `false` stops all wiki content from being written from conversations.

---

#### `sessionCapture.triageModel`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"sonnet"` |

The Claude model alias for the cheap triage pass. Accepts the same short aliases as the Claude CLI (`"sonnet"`, `"opus"`, `"haiku"`).

**What breaks if wrong:** An invalid model alias causes the `claude -p` triage invocation to fail. If triage fails, the capture pass is never attempted, and session content is silently lost.

---

#### `sessionCapture.triageEffort`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"high"` |

Effort level hint passed to the triage agent. Accepts `"low"`, `"medium"`, or `"high"`.

**What breaks if wrong:** Lowering effort can cause the triage agent to misclassify a session as `SKIP` when it contains capturable content, resulting in permanently lost knowledge. There is no retry.

---

#### `sessionCapture.triageBudgetUsd`

| Attribute | Value |
|-----------|-------|
| Type | `number` |
| Default | `0.50` |

Maximum spend cap in USD for the triage agent. The agent is killed if it exceeds this.

**What breaks if wrong:** Setting too low can cause triage to hit the cap mid-run and produce no classification, which is treated the same as `SKIP`. Setting too high risks unexpected cost on pathological sessions.

---

#### `sessionCapture.captureModel`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"sonnet"` |

The Claude model alias for the full capture pass (only runs when triage classifies as `CAPTURE` or `PARTIAL`).

---

#### `sessionCapture.captureEffort`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"high"` |

Effort level hint for the capture agent.

**What breaks if wrong:** Lower effort produces shallower, less accurate wiki pages. The capture agent has the widest write surface of any background agent.

---

#### `sessionCapture.maxBudgetUsd`

| Attribute | Value |
|-----------|-------|
| Type | `number` |
| Default | `5.00` |

USD spend cap for the capture agent (not the triage). The combined worst-case cost per session is `triageBudgetUsd + maxBudgetUsd`.

---

#### `sessionCapture.timeoutSeconds`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `600` (10 minutes) |

Wall-clock timeout for the capture agent. The hook's watchdog subshell sends `USR1` to the agent process after this many seconds, triggering a clean kill via `_agent_cleanup`.

**What breaks if wrong:** Setting too low kills the agent before it finishes writing pages, leaving partially-written files. Setting too high allows a runaway agent to block system resources indefinitely.

---

#### `sessionCapture.minTurns`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `2` |

Minimum number of conversation turns required before the session is eligible for capture. Sessions below this threshold are skipped before the triage agent is even spawned.

**What breaks if wrong:** Setting to `1` will attempt to capture trivial one-turn exchanges. Setting too high will skip genuine knowledge-bearing sessions.

---

#### `sessionCapture.minWords`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `150` |

Minimum word count in the extracted transcript before capture is attempted. Works alongside `minTurns` — both must pass.

---

#### `sessionCapture.allowedTools`

| Attribute | Value |
|-----------|-------|
| Type | `string[]` |
| Default | `["Read", "Write", "Edit", "Glob", "Grep", "Bash"]` |

The list of Claude tool names the capture agent is permitted to use. Passed verbatim to `claude -p --allowedTools` in the session-end hook. This is a **hard enforcement boundary** at the shell level — the agent cannot call tools not in this list regardless of what the prompt requests.

**What breaks if wrong:** Removing `Write` or `Edit` prevents the agent from writing wiki pages — it will run but produce nothing. Removing `Read` prevents the agent from reading the transcript or existing pages. Adding `WebFetch` or other network tools would allow the agent to make external requests, which is a security concern. The hook shell script reads this value via `read-config.py` and constructs the `--allowedTools` flag from it.

---

#### `sessionCapture.writableCategories`

| Attribute | Value |
|-----------|-------|
| Type | `string[]` |
| Default | `["discussions", "decisions", "gotchas", "playbook"]` |

The wiki category directories that the capture agent is allowed to write pages into. This value is injected into the capture prompt template as a constraint — the agent is explicitly told to write only within these paths. It is also checked by path rules in the prompt itself.

**What breaks if wrong:** If you remove a category, the agent will refuse (or fail) to write pages that belong there. If you add a project-specific category, the agent will be permitted to write there — but you also need to ensure the directory exists (bootstrap creates it). This list should always be a subset of the full wiki category set.

---

#### `sessionCapture.lockStalenessSeconds`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `900` (15 minutes) |

How old (in seconds) a session capture lock file must be before it is considered stale and may be overridden by a new capture attempt. Prevents two simultaneous capture agents from writing to the same session directory, while ensuring a crashed agent's lock doesn't block the directory forever.

---

### `ingest` section

Controls the post-merge background agent triggered by `git post-merge` on the configured branch. See [[post-merge-hook]] for the hook implementation.

#### `ingest.enabled`

| Attribute | Value |
|-----------|-------|
| Type | `boolean` |
| Default | `true` |

Master switch. Set to `false` to disable all post-merge ingest.

---

#### `ingest.model`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"opus"` |

Claude model alias for the ingest agent. Defaults to `opus` because ingest does deep code analysis that benefits from the most capable model.

**What breaks if wrong:** Downgrading to `haiku` or `sonnet` may produce shallower wiki pages for complex code. The ingest agent has a larger budget than capture for this reason.

---

#### `ingest.effort`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"high"` |

Effort level hint for the ingest agent.

---

#### `ingest.maxBudgetUsd`

| Attribute | Value |
|-----------|-------|
| Type | `number` |
| Default | `10.00` |

USD spend cap for a single ingest run. Large merges touching many files can push the agent toward this limit.

**What breaks if wrong:** Setting too low causes the agent to be killed before it finishes updating all affected pages. Setting very high on a busy project can incur significant API costs per merge.

---

#### `ingest.timeoutSeconds`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `1200` (20 minutes) |

Wall-clock timeout for the ingest agent. Higher than session capture because ingest must read changed files and update multiple wiki pages.

---

#### `ingest.branch`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"main"` |

The git branch name that triggers ingest on merge. The `post-merge` hook compares the current branch against this value; if they do not match, the hook exits immediately without spawning an agent.

**What breaks if wrong:** If your project uses `master` or a different integration branch, you must override this to that branch name. Leaving it as `"main"` on a `master`-only project means ingest never fires.

---

#### `ingest.include`

| Attribute | Value |
|-----------|-------|
| Type | `string[]` |
| Default | `["src/"]` |

Path prefixes (relative to the project root) that the ingest agent is scoped to. The agent reads the list of files changed in the merge and filters to only those matching these prefixes before deciding what to document.

**What breaks if wrong:** If your project has no `src/` directory, the default `["src/"]` means ingest will always find zero changed files and produce no output. Override this to your project's actual source directories (e.g., `["lib/", "app/", "packages/"]`). Adding `""` (empty string) would match all files, which may be too broad for large monorepos.

---

#### `ingest.allowedTools`

| Attribute | Value |
|-----------|-------|
| Type | `string[]` |
| Default | `["Read", "Write", "Edit", "Glob", "Grep", "Bash"]` |

Same enforcement mechanism as `sessionCapture.allowedTools` — passed to `claude -p --allowedTools` in the post-merge hook. The ingest agent needs `Bash` to run `git diff` and `git show` to inspect changed files.

**What breaks if wrong:** Removing `Bash` prevents the agent from inspecting git history. Removing `Write`/`Edit` prevents it from updating wiki pages. As with capture, adding unexpected tools is a security risk.

---

### `lint` section

Controls the `/llake-lint` on-demand skill. Quick mode (in-session, no subagent) ignores these settings entirely and uses the parent session's model and limits. These keys apply only to Comprehensive-mode subagents and to the heuristic that recommends running Comprehensive mode.

#### `lint.model`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"sonnet"` |

Model for Comprehensive-mode lint subagents.

---

#### `lint.effort`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `"high"` |

Effort for Comprehensive-mode lint subagents.

---

#### `lint.comprehensive-recommended-after-days`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `14` |

If the wiki has not had a Comprehensive lint run in this many days, the skill will recommend running one when invoked with no arguments.

---

#### `lint.comprehensive-recommended-after-activity`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `20` |

If more than this many wiki write events have occurred since the last Comprehensive lint, the skill will recommend running one.

---

#### `lint.stale-threshold-days`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `30` |

Pages whose `updated` frontmatter date is older than this many days are flagged as stale candidates during lint.

---

### `transcript` section

Controls how `hooks/lib/extract_transcript.py` samples the Claude Code JSONL session file before handing it to the capture agent. These settings let you tune the fidelity-vs-cost trade-off for the capture pass.

#### `transcript.maxMessageLength`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `2000` |

Individual messages longer than this character count are truncated in the extracted transcript. Keeps very large tool outputs (e.g., long file reads) from inflating the transcript to the point where the triage/capture agents exceed their context or budget.

**What breaks if wrong:** Setting very low may truncate code snippets needed for accurate documentation. Setting very high increases agent cost proportionally to long messages.

---

#### `transcript.headSize`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `10` |

Number of messages to always include from the start of the conversation (the "head" of the sample). Preserves the user's initial question and context-setting.

---

#### `transcript.tailSize`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `20` |

Number of messages to always include from the end of the conversation (the "tail"). Preserves conclusions, final outputs, and decisions reached.

---

#### `transcript.middleMaxSize`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `30` |

Maximum number of messages sampled from the middle of a long conversation. The extractor selects evenly-spaced messages between the head and tail windows.

**What breaks if wrong:** Setting to `0` drops all middle content, which may cause the capture agent to miss decisions and context established mid-conversation. Setting very high on very long sessions increases cost.

---

#### `transcript.middleScaleStart`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `100` |

The conversation length (in total messages) at which the middle sampling begins to scale down. Below this threshold, all middle messages are included. Above it, `middleMaxSize` is the cap.

---

### `logging` section

Controls rotation of the `llake/.state/hooks.log` audit trail.

#### `logging.maxLines`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `1000` |

The maximum number of lines `hooks.log` is allowed to grow to before rotation is triggered.

**What breaks if wrong:** Setting very low causes frequent rotations and discards hook history quickly. Setting to `0` or very high may allow unbounded log growth in active projects.

---

#### `logging.rotateKeepLines`

| Attribute | Value |
|-----------|-------|
| Type | `integer` |
| Default | `500` |

When rotation fires (because `maxLines` was reached), the log is trimmed to keep only this many of the most recent lines.

**What breaks if wrong:** Must be less than `maxLines` or rotation becomes a no-op. If equal to or greater than `maxLines`, the log will grow past the trigger threshold after every rotation attempt and rotation will fire again on the next hook run with no effective trimming.

---

### `prompts` section

Allows per-template custom slot overrides. The prompt renderer (`hooks/lib/render-prompt.py`) applies these values when substituting `{{KEY}}` placeholders before falling back to any `{{KEY|fallback:path}}` file-based default. See [[template-system]] for how the renderer works.

#### `prompts.ingest.EXAMPLES`

| Attribute | Value |
|-----------|-------|
| Type | `string` |
| Default | `""` (empty — template uses its shipped fallback) |

A string injected into the ingest prompt template's `{{EXAMPLES}}` slot. When non-empty, this replaces the shipped example pages embedded in the `ingest.md.tmpl` template.

**Why this matters:** The ingest agent uses examples to calibrate the style, depth, and structure of the wiki pages it writes. The shipped fallback examples are generic. If your project has established wiki pages that exemplify your preferred style, paste one or two of them (condensed) into this config key. The quality of ingest output is directly proportional to the quality of examples provided.

**What breaks if wrong:** Leaving it empty is safe — the template falls back to its built-in examples. Providing malformed or misleading examples may cause the ingest agent to adopt incorrect page structure. Note that this is a plain string, not a file path — the full example text goes here inline.

---

## Key Points

- Every key in `config.default.json` documents the system's authoritative default. **Never duplicate these defaults** into user `config.json` files or hook scripts — that creates a second source of truth that diverges silently.
- `_comment` and `_schemaVersion` are metadata annotations. They will never be returned as meaningful values by `read-config.py` lookups from hook code, but they are technically reachable if you look them up by dot-key.
- `allowedTools` in both `sessionCapture` and `ingest` is a **hard shell-level enforcement boundary**, not just a hint to the agent prompt. The hook passes the value directly to `claude -p --allowedTools`.
- `sessionCapture.writableCategories` is both a prompt-level constraint (injected into the agent's instructions) and a design-level boundary. It should mirror `llake.fixedCategories` unless you have intentionally added project-specific categories.
- The `prompts.ingest.EXAMPLES` key is the primary lever for tuning ingest output quality without modifying plugin code.

---

## Code References

- `templates/config.default.json` — the complete defaults file; every key documented above
- `hooks/lib/read-config.py:21` — `DEFAULTS_PATH` is resolved relative to the script's location in the plugin repo
- `hooks/lib/read-config.py:32-38` — `get_nested()` implements dot-key traversal; returns `(None, False)` on any missing intermediate key
- `hooks/lib/read-config.py:41-48` — `format_value()` serializes booleans as `"true"`/`"false"`, arrays/objects as JSON, `null` as `""`
- `hooks/session-end.sh` — reads `sessionCapture.*` keys and constructs `--allowedTools` flag
- `hooks/post-merge.sh` — reads `ingest.*` keys and constructs `--allowedTools` flag

---

## See Also

- [[config-layering]] — how user `config.json` and `config.default.json` are merged at read time
- [[read-config]] — the `read-config.py` script that performs dot-key lookups
- [[session-end-hook]] — the hook that consumes `sessionCapture.*` config
- [[post-merge-hook]] — the hook that consumes `ingest.*` config
- [[session-start-hook]] — the hook that reads `llake.*` config for context injection
- [[three-writer-model]] — how bootstrap, ingest, and capture relate to each other
- [[template-system]] — how `prompts.*` overrides are applied during prompt rendering
