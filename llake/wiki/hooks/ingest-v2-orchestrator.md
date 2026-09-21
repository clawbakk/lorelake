---
title: "Ingest v2 Orchestrator"
description: "run_ingest_v2 in hooks/lib/ingest-v2.sh — sequences context build, planner, applier, fixer, and cursor advancement"
tags: [hooks, shell, ingest, orchestration, agents]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[ingest-v2-pipeline]]"
  - "[[post-merge-hook]]"
  - "[[apply-ingest-plan]]"
  - "[[build-ingest-context]]"
  - "[[hook-log]]"
  - "[[post-merge-lock]]"
  - "[[claude-p-tools-flag]]"
  - "[[ingest-cursor]]"
  - "[[ingest-gate]]"
---
# Ingest v2 Orchestrator

## Overview

`hooks/lib/ingest-v2.sh` defines one public function, `run_ingest_v2`, plus the private helper `_run_ingest_v2_fixer`. It is sourced by `hooks/post-merge.sh` only when `ingest.pipeline` is `v2`, and called from inside the already-forked, already-locked background subshell. It is the shell glue that sequences the five stages described in [[ingest-v2-pipeline]].

It is a **function library, not a script** — it reads a dozen variables from the caller's scope rather than taking them as parameters.

## Contract with the caller

These must be set by `hooks/post-merge.sh` before the call:

`PROJECT_ROOT`, `LLAKE_ROOT`, `WIKI_ROOT`, `STATE_DIR`, `AGENTS_DIR`, `CONFIG_FILE`, `LIB_DIR`, `PROMPTS_DIR`, `TEMPLATES_DIR`, `SCHEMA_DIR`, `LAST_SHA`, `CURRENT_SHA`, `COMMIT_RANGE`, `LOG_FILE`, and the bash array `INCLUDE_PATHS`.

The function `advance_ingest_cursor` must also be in scope. This file does not source it. `post-merge.sh` sources `hooks/lib/ingest-cursor.sh` at top level, so it is visible inside the forked subshell. See [[ingest-cursor]].

By the time `run_ingest_v2` is called, the batching gate in `post-merge.sh` has already returned `RUN`. Ranges with no relevant changes, and small young piles, never reach this function. See [[ingest-gate]].

The first three *positional* arguments are the agent ID, agent directory, and agent log path. They are parameters rather than scope reads for a specific reason: the caller pre-computes them and creates the directory **before** forking, so the watchdog subshell has a valid `AGENT_LOG` and `MAX_TIMEOUT_SEC` the instant it might fire. A watchdog that trips before the orchestrator has set up its own logging would have nowhere to write the timeout marker.

```bash
run_ingest_v2 "$V2_AGENT_ID" "$V2_AGENT_DIR" "$V2_AGENT_LOG"
```

## Config reads and the hard failure on tools

All `ingest.v2.*` keys are read up front (`hooks/lib/ingest-v2.sh:29-50`). Two reads are treated as fatal:

```bash
plan_tools=$(python3 -c "import json,sys; print(','.join(json.loads(sys.argv[1])))" "$plan_tools_json")
if [ -z "$plan_tools" ]; then
  echo "ingest-v2: ingest.v2.plannerAllowedTools is missing or malformed" >&2
  echo "ingest-v2: cannot proceed without a known tool allowlist (refusing to run with empty)" >&2
  return 1
fi
```

There is no fallback allowlist. An earlier version defaulted silently to `Read,Glob,Grep`, which meant a malformed config produced an agent running with tools nobody had chosen — and the operator had no signal that their configuration had been ignored. Failing loudly is the correct behaviour for a security-relevant setting. The same check guards `fixerAllowedTools`.

`--model` and `--effort` are handled the opposite way: an empty config value produces **no flag at all** rather than an empty string argument, so the CLI default applies. This mirrors the legacy pipeline (`hooks/lib/ingest-v2.sh:122-125`, `hooks/lib/ingest-v2.sh:252-255`).

## Stale tempfile sweep

```bash
if [ -d "$WIKI_ROOT" ]; then
  find "$WIKI_ROOT" -type f -name '.*.md.tmp' -delete 2>/dev/null || true
fi
```

`_atomic_write` in the applier writes `.<page>.md.tmp` next to its target and then `os.replace`s it. A `SIGKILL` between the two leaves the dotfile behind, where it shows up in `git status` and confuses the user. Sweeping at the *start* of a run (rather than in a trap) is deliberate — a trap cannot run after `SIGKILL` (`hooks/lib/ingest-v2.sh:52-58`).

## Stage sequencing and failure handling

Every stage writes a banner into `agent.log` and, on failure, one line into `hooks.log` before returning nonzero. Because the caller's `EXIT` trap releases the post-merge lock, an early `return 1` is always safe.

| Stage | Code | On failure |
|---|---|---|
| 1 — context | `hooks/lib/ingest-v2.sh:78-94` | `failed: stage1`, return 1 |
| 2 — planner render | `hooks/lib/ingest-v2.sh:96-117` | `log_render_failure PLANNER`, `render-failed: planner`, return 1 |
| 2 — planner run | `hooks/lib/ingest-v2.sh:127-149` | `failed: planner (exit N)`, return 1 |
| 3 — applier | `hooks/lib/ingest-v2.sh:159-177` | `failed: applier (<mode>)`, return 1 |
| 4 — fixer | `hooks/lib/ingest-v2.sh:219-289` | non-fatal; first-pass results stand |
| 5 — finalize | `hooks/lib/ingest-v2.sh:188-214` | `aborted: ... cursor held` if outputs are missing |

### Classifying an applier failure

The applier exits 2 for an unreadable or unparseable plan and 1 for a schema-invalid one, but the operator wants finer detail than that in `hooks.log`. The orchestrator greps the tail of `agent.log` for the applier's own stderr text:

```bash
fail_msg="schema-invalid"
if tail -20 "$AGENT_LOG" | grep -q "non-JSON planner output"; then
  fail_msg="non-JSON-plan"
elif tail -20 "$AGENT_LOG" | grep -q "schema-invalid JSON"; then
  fail_msg="malformed-JSON"
fi
```

This is a genuine coupling: those two strings are a contract between `apply_ingest_plan.py` and this file, and changing either message without the other silently degrades every future failure line to `schema-invalid`. See [[apply-ingest-plan]].

## The fixer pass

`_run_ingest_v2_fixer` runs when `failed.json` is non-empty and `maxFixerRetries > 0`. It renders `ingest.v2.fix.md.tmpl` with four slots: the original plan, the failure list, the **current** bodies of the affected pages (built by `build_failed_bodies.py`, which handles missing pages and the fenced-block formatting that does not survive shell escaping), and the context directory path.

Two details matter:

- The fix pass is applied with `--no-log-entry`, so a single ingest run produces a single `log.md` entry rather than two.
- On success, `final-failed.json` is copied over `failed.json`, making it the canonical remaining-failures list that finalize reads. If the fix-pass applier itself fails, the copy is skipped and the original failures stand.

A fixer render failure returns 0, not 1 — losing the repair pass must not discard successfully applied first-pass work.

## Cursor policy

```bash
if [ ! -f "$APPLIED" ] || [ ! -f "$FAILED" ]; then
  # applier killed mid-pass — cursor held
  return 1
fi
# ...
advance_ingest_cursor "$CURRENT_SHA"
```

The orchestrator advances the cursor itself, and it does so even when `n_failed > 0` — partial success is still progress, and the failures are recorded in `log.md` for a human. The one case that must hold the cursor is an applier that never wrote its outputs, because then nothing is known about what was or was not applied.

The write goes through `advance_ingest_cursor` (`hooks/lib/ingest-v2.sh:203`). Besides the SHA, it stamps `.state/last-ingest-at`, the clock the batching gate's age arm reads. v2 used to write only the SHA inline. That would have left the gate measuring age from a stale moment after every v2 run. `tests/hooks/test_post_merge_gate.sh` (`test_v2_success_writes_clock`) now pins that a successful v2 run advances both.

## Key Points

- `run_ingest_v2` is a sourced function that reads most of its inputs from the caller's scope; only agent ID, dir, and log are positional.
- Those three are positional so the watchdog has a valid log path before the function starts.
- A missing or malformed `plannerAllowedTools` / `fixerAllowedTools` is fatal — there is deliberately no default allowlist.
- Empty `model`/`effort` config values produce no CLI flag rather than an empty argument.
- Stale `.<page>.md.tmp` files are swept at the start of each run, because a trap cannot clean up after `SIGKILL`.
- Applier failure classification depends on grepping two exact stderr strings out of `agent.log`.
- The fixer is best-effort: its failures never discard first-pass results, and it applies with `--no-log-entry`.
- The cursor advances on partial success and is held only when `applied.json`/`failed.json` are missing. The advance goes through `advance_ingest_cursor`, so the gate's clock moves with it.

## Code References

- `hooks/lib/ingest-v2.sh:1-17` — header documenting the caller-scope contract
- `hooks/lib/ingest-v2.sh:29-50` — config reads
- `hooks/lib/ingest-v2.sh:34-47` — hard failure on missing tool allowlists
- `hooks/lib/ingest-v2.sh:52-58` — stale tempfile sweep
- `hooks/lib/ingest-v2.sh:65-73` — agent log header
- `hooks/lib/ingest-v2.sh:78-94` — Stage 1 invocation
- `hooks/lib/ingest-v2.sh:127-141` — planner invocation and `PIPESTATUS` capture
- `hooks/lib/ingest-v2.sh:166-171` — applier failure classification
- `hooks/lib/ingest-v2.sh:188-214` — finalize and cursor advancement (`advance_ingest_cursor` at line 203)
- `hooks/lib/ingest-v2.sh:219-289` — `_run_ingest_v2_fixer`
- `hooks/lib/build_failed_bodies.py` — failed-page-bodies slot builder
- `tests/lib/test_build_failed_bodies.py` — its unit tests
- `tests/hooks/test_post_merge_v2.sh` — integration coverage

## See Also

- [[ingest-v2-pipeline]] — the architecture this function implements
- [[post-merge-hook]] — the caller
- [[apply-ingest-plan]] — Stage 3, and the source of the classified error strings
- [[build-ingest-context]] — Stage 1
- [[hook-log]] — `log_render_failure` used by both render-failure handlers
- [[claude-p-tools-flag]] — why both `--tools` and `--strict-mcp-config` are passed
