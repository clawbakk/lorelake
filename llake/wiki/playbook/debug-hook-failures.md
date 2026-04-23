---
title: "Debug Hook Failures"
description: "How to investigate silent or failing LoreLake hooks via hooks.log and agent working dirs"
tags: [playbook, debugging, hooks, agents]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[agent-run]]"
  - "[[format-agent-log]]"
  - "[[is-llake-agent-guard]]"
---

# Debug Hook Failures

LoreLake hooks (`session-end` and `post-merge`) run silently in the background. When they produce no wiki output or produce unexpected output, follow this guide to find the root cause.

## Symptoms

- A git pull or Claude Code session ends with no visible activity and no wiki changes.
- A wiki page is not updated after a code merge.
- A session discussion entry was expected but never appeared.
- The hook appears to start but leaves no trace.

## Diagnosis

Work through these steps in order, from cheapest to most detailed.

### Step 1 — Read hooks.log

Every hook invocation appends a line to `llake/.state/hooks.log`. This is always the first place to look.

```bash
cat <project>/llake/.state/hooks.log
```

Each line has the format:

```
2026-04-23 14:03:17 | session-end   | started → done: spawned agent brave-river-140317 (session: abc123, turns: 8, timeout: 600s)
```

If the line ends with `→ CRASHED`, the hook script itself exited without calling `hook_end` — a shell-level error occurred before the outcome was written.

Common `hook_end` outcomes and what they mean:

| Outcome | Meaning |
|---|---|
| `skipped: recursion guard` | `IS_LLAKE_AGENT=true` was set — the hook bailed to prevent an agent triggering itself. See [[is-llake-agent-guard]]. |
| `skipped: sessionCapture disabled` | `sessionCapture.enabled` is `false` in `config.json`. |
| `skipped: ingest disabled` | `ingest.enabled` is `false` in `config.json`. |
| `skipped: no transcript found` | The session JSONL could not be located — session ID unknown or `~/.claude/conversations/` missing the file. |
| `skipped: empty session (N turns < M)` | Session had fewer turns than `sessionCapture.minTurns`. |
| `skipped: thin session (N words < M)` | Session had fewer words than `sessionCapture.minWords`. |
| `skipped: no new commits` | `last-ingest-sha` matches `HEAD` — nothing to ingest. |
| `skipped: not on <branch>` | The current branch does not match `ingest.branch`. |
| `done: spawned agent <id>` | Hook succeeded and backgrounded an agent. Check the agent log next. |
| `completed: agent <id> finished (exit 0)` | Agent ran to completion without error. |
| `failed: agent <id> (exit N)` | Agent exited with a non-zero code. |
| `timeout: agent <id> killed after Ns` | The watchdog fired — the agent exceeded `timeoutSeconds`. |
| `terminated: agent <id> killed by user` | The agent received SIGTERM or SIGINT externally. |

### Step 2 — Find the agent directory

When a hook spawns an agent it creates a working directory:

```
<project>/llake/.state/agents/<agent-id>/
```

The `<agent-id>` is a human-readable string like `brave-river-140317` (adjective-noun-HHMMSS). It appears in the `hooks.log` line for that invocation.

```bash
ls <project>/llake/.state/agents/
# brave-river-140317/
# swift-lake-090512/
```

Inside the agent directory:

```
agent.log       — full execution trace (always present)
ingest.pid      — PID file while the ingest phase is running (removed on completion)
triage.pid      — PID file while triage is running (removed on completion)
capture.pid     — PID file while capture is running (removed on completion)
```

If a `.pid` file is still present, the agent may still be running or it crashed mid-phase without cleanup.

### Step 3 — Read agent.log directly

The raw `agent.log` starts with a header block, then interleaved stream-JSON output from the Claude CLI. The header is human-readable; the body is JSON lines.

```bash
head -20 <project>/llake/.state/agents/brave-river-140317/agent.log
```

The header looks like:

```
=== LoreLake Session Capture: brave-river-140317 ===
Session: abc123
Started: 2026-04-23 14:03:17
Timeout: 600s
Budget:  $5.00
Turns:   8
Triage:  sonnet (high) → Capture: sonnet (high)
---
```

The trailer lines at the end tell you the outcome:

| Trailer | Meaning |
|---|---|
| `=== COMPLETED: exit 0 at ... ===` | Agent finished successfully. |
| `=== FAILED: exit N at ... ===` | Agent exited with an error code. |
| `=== KILLED: exit 137/143 at ... ===` | Agent was killed by a signal (SIGKILL or SIGTERM). |
| `=== TIMEOUT during <phase> (session exceeded Ns) at ... ===` | Watchdog fired — the named phase was still running when the timer expired. |
| `=== KILLED by user during <phase> at ... ===` | SIGTERM or SIGINT received during the named phase. |
| `=== SKIPPED: triage SKIP at ... ===` | Triage classified the session as SKIP; capture phase never ran. |

### Step 4 — Parse agent.log with format-agent-log.py

The body of `agent.log` is raw stream-JSON. Use `format-agent-log.py` to convert it to a readable trace:

```bash
python3 <plugin-root>/hooks/lib/format-agent-log.py \
  < <project>/llake/.state/agents/brave-river-140317/agent.log \
  | less
```

The formatted output shows tool calls, results, assistant text, token usage per turn, and the final cost/duration:

```
[14:03:22] INIT | model=claude-sonnet-4-5 tools=Read,Write,Edit
[14:03:23] === TURN 1 === (in=4200 out=180 cache_read=0 cache_create=4100)
[14:03:23] TEXT | Reading the transcript to assess content...
[14:03:23] CALL | Read(/project/llake/.state/sessions/abc123/transcript.md)
[14:03:24] RESULT | Read → # Session Transcript ...
...
[14:03:55] DONE | turns=6 cost=$0.0312 duration=32.4s stop=end_turn
```

If the agent hit a budget cap, the `DONE` line shows `stop=max_budget`. If it errored, look for `ERROR |` lines.

### Step 5 — Check the triage result

For session capture, triage runs as a separate pass. Its classification is written to:

```
<project>/llake/.state/sessions/<session-id>/triage-result.txt
```

Note: the `sessions/<session-id>/` directory is deleted after a successful or skipped capture. If the session was skipped, the directory is removed during cleanup. To read the triage result before it is deleted, parse the `agent.log` section under `=== TRIAGE (...)  ===`.

To extract just the triage agent's final text from a saved log:

```bash
python3 <plugin-root>/hooks/lib/format-agent-log.py \
  < <project>/llake/.state/agents/brave-river-140317/agent.log \
  | grep "TEXT |" | tail -5
```

Also check `hooks.log` for the `triage-done` line written after each triage pass:

```
2026-04-23 14:03:40 | triage-done   | triage SKIP by brave-river-140317_triage: routine test session, no novel decisions
```

### Step 6 — Check for IS_LLAKE_AGENT suppression

If the hook bailed immediately with `skipped: recursion guard`, the environment variable `IS_LLAKE_AGENT=true` was set in the shell that triggered the hook. This is the expected guard behavior when agents run Claude Code internally. See [[is-llake-agent-guard]] for details.

To check whether the guard is active in a shell:

```bash
echo "$IS_LLAKE_AGENT"
```

If this prints `true` in your interactive shell (not in an agent subprocess), something has leaked the variable into your environment. Unset it and re-test:

```bash
unset IS_LLAKE_AGENT
```

## Common Causes

### 1. Session not captured: too few turns or words

**Symptom**: `hooks.log` shows `skipped: empty session` or `skipped: thin session`.

**Fix**: Lower the thresholds in `llake/config.json`:

```json
{
  "sessionCapture": {
    "minTurns": 1,
    "minWords": 50
  }
}
```

Default values: `minTurns: 2`, `minWords: 150`.

### 2. Agent timed out mid-run

**Symptom**: `agent.log` ends with `=== TIMEOUT during capture ===`; `hooks.log` shows `timeout: agent ... killed after 600s`.

**Fix**: Increase the timeout or reduce the agent's scope. In `llake/config.json`:

```json
{
  "sessionCapture": {
    "timeoutSeconds": 1200
  }
}
```

For ingest:

```json
{
  "ingest": {
    "timeoutSeconds": 1800
  }
}
```

### 3. Agent hit budget cap

**Symptom**: `format-agent-log.py` output shows `stop=max_budget`.

**Fix**: Raise `maxBudgetUsd` in `llake/config.json`:

```json
{
  "sessionCapture": {
    "maxBudgetUsd": 10.00
  },
  "ingest": {
    "maxBudgetUsd": 20.00
  }
}
```

### 4. Missing prompt template file

**Symptom**: `hooks.log` shows `skipped: missing prompt files (triage or capture)` or `skipped: missing ingest prompt file`.

**Fix**: Verify the plugin installation. The expected files are:

```
<plugin-root>/hooks/prompts/triage.md.tmpl
<plugin-root>/hooks/prompts/capture.md.tmpl
<plugin-root>/hooks/prompts/ingest.md.tmpl
```

Run `/llake-doctor` to diagnose and repair the installation.

### 5. Prompt render failure (unresolved placeholder)

**Symptom**: `agent.log` is empty or very short; the hook exited before spawning the Claude process. The `AGENT_LOG` header may be present but the body absent.

**Fix**: Check if `render-prompt.py` exited nonzero. It prints to stderr:

```
render-prompt: unresolved placeholders: SESSION_DIR
```

This means a `{{VAR}}` in a template has no matching `KEY=value` argument in the hook script. See [[render-prompt]] and [[render-prompt-strict-exit]] for the resolution contract.

### 6. IS_LLAKE_AGENT leaked into interactive shell

**Symptom**: Every hook invocation immediately writes `skipped: recursion guard` to `hooks.log`.

**Fix**: `unset IS_LLAKE_AGENT` in the affected shell. This variable is exported by hooks before spawning agents and should only exist in the agent subprocess environment.

### 7. post-merge hook not wired

**Symptom**: No `hooks.log` entry appears after `git pull`, even though there are new commits.

**Fix**: Check that the git hook shim is installed:

```bash
cat "$(git rev-parse --git-common-dir)/hooks/post-merge"
```

Expected content:

```bash
#!/bin/bash
exec "<plugin-root>/hooks/post-merge.sh" "$@"
```

If missing, wire it:

```bash
printf '#!/bin/bash\nexec "%s/hooks/post-merge.sh" "$@"\n' \
  "$(git rev-parse --show-toplevel)/lorelake" \
  > "$(git rev-parse --git-common-dir)/hooks/post-merge" \
  && chmod +x "$(git rev-parse --git-common-dir)/hooks/post-merge"
```

## Prevention

- Run `/llake-doctor` after every install or upgrade to verify all wiring is intact.
- Keep `hooks.log` in view when testing hook changes: `tail -f <project>/llake/.state/hooks.log`.
- Do not export `IS_LLAKE_AGENT` in shell profiles or dotfiles.
- When raising `timeoutSeconds`, also raise `maxBudgetUsd` proportionally — a longer timeout means a larger potential cost.

## See Also

- [[agent-run]] — kill trap behavior, timeout vs. user-kill distinction
- [[format-agent-log]] — full reference for agent.log format
- [[is-llake-agent-guard]] — recursion guard details
- [[session-end-hook]] — full session-end hook reference
- [[post-merge-hook]] — full post-merge hook reference
