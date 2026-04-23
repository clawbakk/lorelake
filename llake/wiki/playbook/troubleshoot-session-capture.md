---
title: "Troubleshoot Session Capture"
description: "Decision tree for why sessions are not being captured — triage, budget, minTurns, guard"
tags: [playbook, debugging, session-capture, triage]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[session-end-hook]]"
  - "[[triage-template]]"
  - "[[extract-transcript]]"
  - "[[is-llake-agent-guard]]"
---

# Troubleshoot Session Capture

This guide explains why session capture may not produce wiki entries and provides a decision tree to locate the exact failure point.

## Symptoms

- A Claude Code session ended but no discussion entry appeared in `llake/wiki/discussions/`.
- `llake/wiki/decisions/` or `llake/wiki/gotchas/` has no new pages from recent work.
- `llake/.state/hooks.log` shows no `session-end` entry at all, or shows repeated `skipped:` outcomes.

## Diagnosis

Work through these gates in order. Each gate has a definitive check. Stop when you find the cause.

### Gate 1 — Is session capture enabled?

```bash
python3 <plugin-root>/hooks/lib/read-config.py \
  <project>/llake/config.json \
  "sessionCapture.enabled"
```

If the output is `false`, session capture is disabled globally.

**Fix**: Set it to `true` in `llake/config.json`:

```json
{
  "sessionCapture": {
    "enabled": true
  }
}
```

Default: `true`.

### Gate 2 — Is the IS_LLAKE_AGENT guard suppressing the hook?

```bash
grep "recursion guard" <project>/llake/.state/hooks.log | tail -5
```

If you see these lines, the hook is firing inside an agent session (where `IS_LLAKE_AGENT=true`). This is correct and expected for agent-spawned sessions. For your own interactive sessions, check whether the variable leaked:

```bash
echo "$IS_LLAKE_AGENT"
```

If it prints `true` in your normal interactive shell, unset it:

```bash
unset IS_LLAKE_AGENT
```

See [[is-llake-agent-guard]] for background.

### Gate 3 — Did the session meet minTurns and minWords?

When a session is too short, `session-end.sh` skips it before spawning any agent.

```bash
grep "session-end" <project>/llake/.state/hooks.log | tail -10
```

Look for:
- `skipped: empty session (N turns < M)` — the session had too few assistant/user exchanges.
- `skipped: thin session (N words < M)` — the transcript was too sparse.

To see the raw counts for a specific session, the `.turns` and `.words` sidecar files are written to `llake/.state/sessions/<session-id>/` during extraction and deleted immediately after the filter check. If the session was skipped, those files are gone. However, you can re-extract the transcript manually:

```bash
python3 <plugin-root>/hooks/lib/extract_transcript.py \
  ~/.claude/conversations/<session-id>.jsonl \
  /tmp/transcript-check.md \
  <session-id> \
  10 20 30 100 2000
```

Then read the sidecars:

```bash
cat /tmp/transcript-check.md.turns   # turn count
cat /tmp/transcript-check.md.words   # word count
```

Compare against current thresholds:

```bash
python3 <plugin-root>/hooks/lib/read-config.py \
  <project>/llake/config.json "sessionCapture.minTurns"
python3 <plugin-root>/hooks/lib/read-config.py \
  <project>/llake/config.json "sessionCapture.minWords"
```

**Fix**: Lower the thresholds in `llake/config.json`:

```json
{
  "sessionCapture": {
    "minTurns": 1,
    "minWords": 50
  }
}
```

Defaults: `minTurns: 2`, `minWords: 150`.

### Gate 4 — Did triage classify as SKIP?

Triage is the first of the two passes. A cheap agent reads the transcript and decides `CAPTURE`, `PARTIAL`, or `SKIP`. Only if the result is `CAPTURE` or `PARTIAL` does the full capture agent run.

Check `hooks.log` for the triage outcome:

```bash
grep "triage-done" <project>/llake/.state/hooks.log | tail -10
```

A typical line:

```
2026-04-23 15:04:12 | triage-done   | triage SKIP by brave-river-150400_triage: routine config change, no novel insight
```

To read the full triage reasoning, find the agent directory for the session and parse its log:

```bash
# Find the agent ID from hooks.log (look for the 'done: spawned agent' line)
grep "session-end" <project>/llake/.state/hooks.log | tail -10

# Read the triage section of that agent's log
python3 <plugin-root>/hooks/lib/format-agent-log.py \
  < <project>/llake/.state/agents/<agent-id>/agent.log \
  | grep -A 100 "TRIAGE"
```

To manually re-run triage on a saved transcript and see what the agent decides, first save a transcript (see Gate 3 for how to extract one), then render the triage prompt and run it:

```bash
# Render the triage prompt
TRIAGE_PROMPT=$(python3 <plugin-root>/hooks/lib/render-prompt.py \
  --templates-dir <plugin-root>/templates \
  <plugin-root>/hooks/prompts/triage.md.tmpl \
  <project>/llake/config.json \
  "SESSION_DIR=/tmp/test-session")

# Create the session dir and copy your transcript there
mkdir -p /tmp/test-session
cp /tmp/transcript-check.md /tmp/test-session/transcript.md

# Run triage manually (read-only, capped at $0.50)
claude --model sonnet --effort high \
  -p "$TRIAGE_PROMPT" \
  --allowedTools "Read" \
  --max-budget-usd 0.50 \
  --output-format stream-json --verbose 2>&1 \
  | python3 <plugin-root>/hooks/lib/format-agent-log.py
```

The last `TEXT |` lines will contain the triage verdict and reasoning. See [[triage-template]] for details on what the triage agent looks for.

**Fix**: If triage is too aggressive, you cannot change the triage agent's classification logic without editing the triage prompt template. If a class of sessions is consistently misclassified, file a bug or adjust the triage prompt (`<plugin-root>/hooks/prompts/triage.md.tmpl`).

### Gate 5 — Did the capture agent hit budget?

After triage says `CAPTURE` or `PARTIAL`, the capture agent runs with the full budget.

Check whether the capture run was cut short by budget:

```bash
python3 <plugin-root>/hooks/lib/format-agent-log.py \
  < <project>/llake/.state/agents/<agent-id>/agent.log \
  | grep "DONE\|ERROR" | tail -5
```

Budget exhaustion shows as:

```
[15:04:55] DONE | turns=12 cost=$5.0001 duration=87.3s stop=max_budget
```

The cost will be at or near the configured limit. The wiki may be partially written (some pages exist, others missing).

Check the current budget:

```bash
python3 <plugin-root>/hooks/lib/read-config.py \
  <project>/llake/config.json "sessionCapture.maxBudgetUsd"
```

Default: `5.00`.

**Fix**: Raise the budget in `llake/config.json`:

```json
{
  "sessionCapture": {
    "maxBudgetUsd": 10.00
  }
}
```

Also check `triageBudgetUsd` if the triage pass itself is running out (default: `0.50`, hardcoded in the hook — not configurable through `config.json`):

### Gate 6 — Did the capture agent time out?

```bash
grep "timeout" <project>/llake/.state/hooks.log | tail -5
```

Or check the agent log trailer:

```bash
tail -10 <project>/llake/.state/agents/<agent-id>/agent.log
```

A timeout trailer looks like:

```
=== TIMEOUT during capture (session exceeded 600s) at 2026-04-23 15:14:17 ===
```

Check the current timeout:

```bash
python3 <plugin-root>/hooks/lib/read-config.py \
  <project>/llake/config.json "sessionCapture.timeoutSeconds"
```

Default: `600` (10 minutes).

**Fix**: Increase the timeout:

```json
{
  "sessionCapture": {
    "timeoutSeconds": 1200
  }
}
```

### Gate 7 — Was the session transcript missing?

```bash
grep "no transcript found" <project>/llake/.state/hooks.log | tail -5
```

The hook tries to locate the JSONL file at the path passed by Claude Code in the session-end event. If that path is empty or the file does not exist, it falls back to searching `~/.claude/conversations/` by session ID.

Check that Claude Code's conversation store exists and contains files:

```bash
ls ~/.claude/conversations/ | head -10
```

If the directory is empty or missing, Claude Code may not be writing conversation files. This is a Claude Code configuration issue, not a LoreLake issue.

## Finding the Transcript for a Session

If the session was processed (not skipped at Gate 3), the transcript is written to:

```
<project>/llake/.state/sessions/<session-id>/transcript.md
```

Note: This directory is deleted at the end of each capture run (success or skip). It exists only while the capture agent is running. To examine a transcript post-run, re-extract it from the original JSONL as shown in Gate 3.

The original Claude Code JSONL is at:

```
~/.claude/conversations/<session-id>.jsonl
```

## Configuration Keys Reference

| Key | Default | Effect |
|---|---|---|
| `sessionCapture.enabled` | `true` | Master toggle — disables all session capture when `false` |
| `sessionCapture.minTurns` | `2` | Minimum assistant/user exchanges to capture |
| `sessionCapture.minWords` | `150` | Minimum word count in the extracted transcript |
| `sessionCapture.maxBudgetUsd` | `5.00` | Budget cap for the capture agent |
| `sessionCapture.timeoutSeconds` | `600` | Watchdog timeout for the entire two-pass run |
| `sessionCapture.triageModel` | `sonnet` | Model for the triage pass |
| `sessionCapture.triageEffort` | `high` | Effort level for the triage pass |
| `sessionCapture.captureModel` | `sonnet` | Model for the capture pass |
| `sessionCapture.captureEffort` | `high` | Effort level for the capture pass |

All of these can be overridden in `<project>/llake/config.json`. Omitted keys fall back to the defaults shown above from `<plugin-root>/templates/config.default.json`.

## Prevention

- Run `tail -f <project>/llake/.state/hooks.log` during development to watch hook outcomes in real time.
- After changing `minTurns` or `minWords`, run a test session to verify the counts exceed the thresholds.
- Keep `maxBudgetUsd` and `timeoutSeconds` in proportion — a model taking 10 minutes will typically spend more than the default $5.00 cap.

## See Also

- [[session-end-hook]] — full hook implementation reference
- [[triage-template]] — what triage looks for and how it classifies sessions
- [[extract-transcript]] — how transcripts are sampled from JSONL
- [[is-llake-agent-guard]] — recursion guard explanation
