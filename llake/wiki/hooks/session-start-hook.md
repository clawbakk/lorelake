---
title: "Session Start Hook"
description: "Context injection hook — loads session-preamble and llake/index.md at session start"
tags: [hooks, session-start, context-injection]
created: 2026-04-23
updated: 2026-09-20
status: current
related:
  - "[[detect-project-root]]"
  - "[[is-llake-agent-guard]]"
  - "[[three-writer-model]]"
  - "[[hook-log]]"
---

## Overview

`hooks/session-start.sh` fires at the beginning of every Claude Code session. Its sole job is **context injection**: it concatenates `templates/session-preamble.md` with the project's `llake/index.md` and returns the combined text as `additionalContext` in Claude Code's `SessionStart` hook response. No agent is spawned; no files are written; no state is modified. It is intentionally side-effect-free.

If the hook were removed or broken, Claude Code sessions would start with no awareness of LoreLake. The assistant would not know to consult `llake/wiki/`, would not understand the wiki's query convention (index → category index → page), and would not see the operating-context note about reading code when LoreLake disagrees. Every session would be effectively cold-started.

## Registration

The hook is registered in `hooks/hooks.json` under the `SessionStart` event:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" }
        ]
      }
    ]
  }
}
```

Claude Code executes this command at session start and expects a JSON response on stdout with a `hookSpecificOutput.additionalContext` field.

## How it works

### 1. Project-root detection

The hook reads `cwd` from the JSON payload Claude Code sends on stdin, then calls `detect_project_root` (env override `LLAKE_PROJECT_ROOT` → marker walk for `llake/config.json`). If no LoreLake install is found, it exits 0 silently — the session continues normally, just without wiki context. See [[detect-project-root]] for the full algorithm.

```bash
INPUT=$(cat)
CWD=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('cwd', ''))
except:
    print('')
" 2>/dev/null)

PROJECT_ROOT=$(detect_project_root "${CWD:-$PWD}" 2>/dev/null) || exit 0
```

### 2. File assembly

Two files are read:

- `templates/session-preamble.md` — shipped with the plugin (see "Preamble content" below); always static across all projects.
- `<project>/llake/index.md` — the project's wiki category catalog; unique per project.

If both are empty or missing, the hook exits 0 without emitting any context. If at least one exists, they are joined with a horizontal rule separator.

```bash
COMBINED="$PREAMBLE"
[ -n "$INDEX" ] && COMBINED="$COMBINED

---

$INDEX"
```

### 3. Output format

The combined text is written to a temp file (cleaned up on `EXIT`) and then emitted as JSON via an inline Python snippet:

```python
output = {
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': content
    }
}
print(json.dumps(output))
```

This is the exact schema Claude Code expects for `SessionStart` hook responses. The `hookEventName` field must match the triggering event name or Claude Code ignores the output.

## Preamble content

The preamble (`templates/session-preamble.md`) explains the LoreLake system to the assistant at the start of every session. Its key elements:

- **When to consult LoreLake** — architectural reasoning, design decisions, known gotchas, troubleshooting playbooks, conversation history that the code itself does not reveal.
- **Code wins over wiki** — "When LoreLake disagrees with the code, the code is truth — flag the LoreLake page as stale." This prevents the assistant from acting on stale wiki information.
- **Query convention** — read `llake/index.md` first, then the category index, then the target page.
- **What not to touch** — `llake/.state/` (runtime) and `llake/last-ingest-sha` (ingest cursor).

## IS_LLAKE_AGENT guard

`session-start.sh` does **not** check `IS_LLAKE_AGENT`. It contains no recursion risk because it spawns nothing — there is no agent subprocess that could trigger a second `SessionStart`. The guard pattern (bail early when `IS_LLAKE_AGENT=true`) is important in `session-end.sh` and `post-merge.sh` because those hooks spawn background `claude -p` processes; those processes fire `SessionEnd`, which could loop. `SessionStart` only fires for interactive sessions opened by the user, not for `claude -p` non-interactive invocations, so the guard is unnecessary here.

## What breaks if this hook fails silently

Silent failures (e.g., `detect_project_root` finding no install, preamble file missing) cause the hook to exit 0 with no output. Claude Code treats this as "no additional context" and continues the session normally. The assistant loses wiki awareness for that session but nothing is corrupted. This is the correct failure mode for a context-injection hook: degrade gracefully.

If the hook exits nonzero, Claude Code may surface an error to the user. The `set -e` at the top of the script means any unexpected command failure propagates — but the early `|| exit 0` guards on `detect_project_root` and the guarded file reads (`[ -f "$FILE" ] && ...`) prevent most failure modes from reaching `set -e`.

## hooks.log integration

SessionStart used to be the only silent hook. It now sources `hooks/lib/hook-log.sh` and participates in the same audit log as the other two, so an operator reading `.state/hooks.log` sees context injection alongside ingest and capture activity. See [[hook-log]].

The hook opens its line with `hook_start "session-start" ...` (`hooks/session-start.sh:44`) and closes it with one of three outcomes:

| Outcome | Meaning |
|---|---|
| `context injected` | Preamble and/or index were assembled and emitted as `additionalContext` (`hooks/session-start.sh:89`) |
| `skipped: recursion guard [<agent-id>]` | Fired inside a LoreLake agent's `claude -p` sub-session (`hooks/session-start.sh:49-52`) |
| `skipped: no preamble or index` | Both files were absent or empty, so there was nothing to inject (`hooks/session-start.sh:60-63`) |

There is a fourth case that stays silent: when `detect_project_root` fails there is no `llake/.state/` to log into, so the hook simply `exit 0`s (`hooks/session-start.sh:33`). This is the one outcome you will never see in the log, and it is the first thing to suspect when SessionStart appears to do nothing at all.

The recursion guard matters for correctness, not just cost. The preamble tells the model not to edit `llake/` directly; injecting it into an ingest or capture agent's session would contradict that agent's own prompt, whose entire job is to write there.

## Key Points

- Pure context injection — no agents, no file writes, no state changes.
- Concatenates `templates/session-preamble.md` (static, plugin-shipped) with `llake/index.md` (dynamic, project-owned).
- Uses `detect_project_root` to find the LoreLake install from the session's `cwd`.
- Exits 0 silently if no LoreLake install is found or both files are missing.
- Output format must be `{ hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: "..." } }`.
- Does not check `IS_LLAKE_AGENT` — no recursion risk since `claude -p` does not fire `SessionStart`.
- Removing this hook means every session starts cold with no wiki awareness.
- Logs to `.state/hooks.log` via [[hook-log]] with three outcomes: `context injected`, `skipped: recursion guard`, `skipped: no preamble or index`.
- A missing project root is the one silent exit — there is nowhere to write the log line.
- The recursion guard keeps the "don't edit `llake/` directly" preamble out of agent sessions whose job is to write there.

## Code References

- `hooks/session-start.sh:1-68` — full hook implementation
- `hooks/hooks.json:1-18` — hook registration (`SessionStart` entry)
- `templates/session-preamble.md:1-23` — the static operating-context preamble injected at every session start

## See Also

- [[detect-project-root]] — project-root detection algorithm used in step 1
- [[three-writer-model]] — where `session-start.sh` fits among the three writers (it is not a writer — no background agent)
- [[is-llake-agent-guard]] — the recursion guard used by the other two hooks (not needed here, but context for why)
- [[session-end-hook]] — the companion hook that fires at session end and does spawn an agent
