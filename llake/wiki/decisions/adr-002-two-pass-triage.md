---
title: "ADR-002: Two-Pass Triage for Session Capture"
description: "Why session capture uses a cheap triage pass before the full capture agent"
tags: [decisions, architecture]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[session-end-hook]]"
  - "[[triage-template]]"
  - "[[capture-template]]"
  - "[[three-writer-model]]"
---

# ADR-002: Two-Pass Triage for Session Capture

## Decision

Session capture uses a two-pass pipeline:

1. **Triage pass** — a cheap, `Read`-only agent with a $0.50 budget cap classifies the session as `CAPTURE`, `PARTIAL`, or `SKIP`.
2. **Capture pass** — runs only if triage returns `CAPTURE` or `PARTIAL`; uses the full configured budget (default ~$5.00) and the complete set of write-enabled tools to produce wiki pages and discussion entries.

A pre-triage gate based on `minTurns` and `minWords` thresholds filters out trivially short sessions without making any LLM call at all.

## Context

The session capture writer fires on every Claude Code `SessionEnd` event. This means it is triggered by all sessions — including:

- Quick single-question sessions ("what does this function do?")
- Accidental terminal opens that were immediately closed
- Configuration-only sessions with no substantive discussion
- Fully substantive sessions worth documenting

Without a filter, the full capture agent (with its large budget and broad write permissions) would run on every session regardless of whether there is anything worth capturing. Over a working week with many short sessions, this would produce empty or low-value wiki entries and impose unnecessary LLM cost.

The `sessionCapture.minTurns` and `sessionCapture.minWords` config keys (read via `read-config.py` from `config.default.json`) are applied before any LLM call. After that gate, the triage agent is the second line of filtering.

See [[session-end-hook]] for the full implementation, [[triage-template]] for the triage prompt, and [[capture-template]] for the capture prompt.

## Rationale

**Cost scales with session volume without a filter.** If a team runs 30 sessions a day, 20 of which are short or off-topic, running the full capture agent on all 30 costs 30x what it should. The triage pass costs a small fraction of a full capture run and pays for itself immediately at that session volume.

**Triage is fast.** The triage agent uses a short, focused prompt with `Read`-only access. It reads the sampled transcript from `sessions/<id>/transcript.md` and returns one of three tokens followed by a reason. The whole pass typically completes in seconds.

**The `PARTIAL` classification handles the hard middle.** Sessions that contain some useful signal but are not ideal capture candidates are not discarded — they are passed to the capture agent with the `PARTIAL` classification included in the prompt. This allows the capture agent to be appropriately conservative (e.g., extract only a gotcha or a single decision rather than producing a full discussion entry).

**High-effort triage minimizes false SKIPs.** The triage agent runs at a high effort setting (`sessionCapture.triageEffort`). The cost of a false SKIP — permanently losing a useful discussion — is much higher than the cost of a false CAPTURE (the capture agent runs and produces little of value). Triage is therefore tuned to err toward capturing.

**Fail-open on missing triage result.** If the triage result file is missing (agent crashed, I/O error), the hook defaults to `CAPTURE` rather than `SKIP`. The system favors data preservation over cost optimization when uncertain.

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| **Single-pass with a large prompt and budget** | Runs full cost on every session; no way to filter without an LLM call |
| **Rule-based filter only (no triage LLM)** | Word/turn counts cannot detect whether a session discussed something worth capturing; a long debugging session with no architectural insight would still run the full agent |
| **Three-pass (classify → summarize → write)** | Adds latency and complexity with marginal benefit; the capture agent can handle both summarizing and writing in a single focused pass |
| **Always SKIP unless user explicitly tags session** | Requires user action every session; the value of LoreLake is zero-friction capture |
| **Asynchronous triage (fire both simultaneously)** | Would waste capture budget on sessions that triage would have SKIPped; sequential ordering is required |

## Consequences

**What this commits you to:**

- Triage must complete before capture can start. Total latency for a captured session is triage time + capture time, not just capture time. In practice, triage adds a few seconds to a few tens of seconds.
- Two separate agent invocations means two separate sets of logs under `llake/.state/agents/<id>/agent.log` — the triage section is labeled `=== TRIAGE (<id>_triage) ===` and the capture section `=== CAPTURE (<id>_capture) ===` in the same file.
- The `PARTIAL` classification must be propagated into the capture prompt. The capture prompt receives `TRIAGE_CLASSIFICATION` and `TRIAGE_REASON` as template variables. If a new prompt template omits them, the capture agent loses context about borderline sessions.
- The pre-triage gate (`minTurns`, `minWords`) provides savings for truly trivial sessions without any LLM call, but the thresholds must be set conservatively — too high and real sessions get filtered before triage even sees them.
- A single watchdog timer covers both passes. If the total budget of triage + capture exceeds `sessionCapture.timeoutSeconds`, the watchdog kills the outer subshell, terminating whichever phase is in progress.

## See Also

- [[session-end-hook]] — full hook implementation including both passes and the watchdog
- [[triage-template]] — the triage prompt, classification criteria, and output format
- [[capture-template]] — the capture prompt, which receives the triage classification and reason
- [[three-writer-model]] — how session capture fits alongside ingest and bootstrap
