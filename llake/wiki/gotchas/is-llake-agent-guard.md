---
title: "IS_LLAKE_AGENT Recursion Guard"
description: "All hooks bail early when IS_LLAKE_AGENT=true — prevents infinite capture recursion from background agents"
tags: [gotchas, recursion, hooks, background-agents]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[three-writer-model]]"
  - "[[session-end-hook]]"
  - "[[post-merge-hook]]"
---

# IS_LLAKE_AGENT Recursion Guard

## What It Is

Every background agent spawned by LoreLake runs with the environment variable `IS_LLAKE_AGENT=true`. All three entry-point hooks check this variable at startup and exit immediately (with code 0) if it is set.

The guard in `hooks/session-end.sh` (lines 63–67):
```bash
AGENT_ID="${LLAKE_AGENT_ID:-}"
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${AGENT_ID:-unknown}]"
  exit 0
fi
```

The identical guard in `hooks/post-merge.sh` (lines 78–82):
```bash
AGENT_ID="${LLAKE_AGENT_ID:-}"
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  hook_end "skipped: recursion guard [${AGENT_ID:-unknown}]"
  exit 0
fi
```

`hooks/session-start.sh` has no explicit guard because it is pure context injection and spawns no agent — but the same environment inheritance principle applies.

## Why It Exists

Claude Code fires the `SessionEnd` hook at the end of every Claude Code session, including sessions started by LoreLake itself to run the capture or ingest agents. Without the guard:

1. User session ends → `session-end.sh` fires → spawns capture agent A.
2. Capture agent A's session ends → `session-end.sh` fires again → spawns capture agent B.
3. Capture agent B's session ends → spawns capture agent C.
4. ... and so on indefinitely.

Each generation of agents would attempt to capture the previous agent's session, multiplying cost and running forever. The `IS_LLAKE_AGENT` flag breaks this cycle at step 2.

The variable is set in two places:

**`hooks/session-end.sh`** — set just before spawning the background subshell (lines 206–207), then explicitly re-exported on each `claude` invocation (lines 257, 311):
```bash
export IS_LLAKE_AGENT=true
export LLAKE_AGENT_ID="$AGENT_ID"
```

```bash
IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$TRIAGE_AGENT_ID" \
  claude --model "$TRIAGE_MODEL" ...
```

```bash
IS_LLAKE_AGENT=true LLAKE_AGENT_ID="$CAPTURE_AGENT_ID" \
  claude --model "$CAPTURE_MODEL" ...
```

**`hooks/post-merge.sh`** — set before the background subshell, then the variable is in the environment for the `claude` invocation (lines 172–173):
```bash
export IS_LLAKE_AGENT=true
export LLAKE_AGENT_ID="$AGENT_ID"
```

Because the variable is exported, it is inherited by the `claude` child process and by any shell tools Claude invokes. Claude Code's `SessionEnd` hook sees it in the environment of the spawned session and skips.

## Symptoms

If the guard is absent or bypassed:

- `hooks.log` shows repeated back-to-back `started` entries for `session-end` immediately following every agent completion.
- Agent counts in `llake/.state/agents/` grow geometrically — one session produces two agent directories, which produce four, and so on.
- API costs spike without any corresponding user activity.
- Eventually the background agent tree exhausts system resources or hits the configured `maxBudgetUsd` cap across many small agents rather than one bounded run.

## The Fix

**Never remove the guard.** If you are adding a new hook that can trigger a background agent, copy the same check verbatim at the top of the hook, before any expensive work:

```bash
if [ "$IS_LLAKE_AGENT" = "true" ]; then
  exit 0
fi
```

**Never unset `IS_LLAKE_AGENT` inside an agent prompt or agent-spawning script.** The variable must remain set for the entire lifetime of the agent's Claude Code session.

**Never add logic that conditionally skips the check.** The check must be unconditional — there is no scenario where a LoreLake background agent should re-trigger another LoreLake agent.

**When writing tests** that exercise hooks, either unset `IS_LLAKE_AGENT` explicitly before invoking the hook under test (to simulate a normal user session), or set it to `true` to verify the early-exit path:

```bash
# Test normal path
unset IS_LLAKE_AGENT
bash hooks/session-end.sh < test-input.json

# Test guard path — should exit 0 immediately
IS_LLAKE_AGENT=true bash hooks/session-end.sh < test-input.json
```

## See Also

- [[three-writer-model]] — overview of the three writers (bootstrap, ingest, capture) and how agents are spawned
- [[session-end-hook]] — full reference for the session-end hook, including where `IS_LLAKE_AGENT` is exported
- [[post-merge-hook]] — full reference for the post-merge hook and its equivalent guard
