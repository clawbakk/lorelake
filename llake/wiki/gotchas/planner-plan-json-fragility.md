---
title: "One bad byte can discard a whole planner run"
description: "A trailing comma used to make the applier reject a 68KB valid plan; recovery is now string-aware, but truncated plans remain unrecoverable"
tags: [gotchas, ingest, json, planner, cost]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[apply-ingest-plan]]"
  - "[[ingest-v2-pipeline]]"
  - "[[config-schema]]"
---
# One bad byte can discard a whole planner run

## What went wrong

The ingest v2 planner emitted 68KB of otherwise-valid JSON containing a **single trailing comma** on one line. `json.loads` is strict, `_normalize_plan_text` only stripped code fences, so the applier exited 2. `ingest-v2.sh` treats exit 2 as terminal — the fixer only runs on per-op failures *after* a successful parse, so there was nothing to repair it with.

One byte cost 10 updates, 17 bidirectional links, and $3.57 of planner spend, and held the ingest cursor. Nothing was wrong with the *content* of the plan.

This is the shape of the risk: the plan is a single all-or-nothing document, and everything valuable in a run is downstream of it parsing.

## The recovery, and why it is string-aware

`_parse_plan_text` now parses in two steps (`hooks/lib/apply_ingest_plan.py:545-560`):

```python
normalized = _normalize_plan_text(text)
try:
    return json.loads(normalized)
except json.JSONDecodeError as strict_err:
    try:
        return json.loads(_strip_trailing_commas(normalized))
    except json.JSONDecodeError:
        raise ValueError(str(strict_err))
```

Two properties are deliberate:

- **Strict first.** A well-formed plan is never rewritten. The repair path only runs on output that has already failed, so it cannot introduce a regression on the happy path.
- **The stripper is string-aware, not a regex.** Plan bodies are markdown, and markdown prose routinely contains `, ]` or `, }` — inside a sentence, a code sample, a table. A regex would silently rewrite bytes *inside string literals*, corrupting page content in a way no test would catch and no error would report. `_strip_trailing_commas` runs the same character-level state machine as the balanced-object scanner, tracking `in_string` and escapes, and only removes a comma it can prove is structural.

Genuinely broken JSON still fails. This recovers one known-benign LLM slip; it does not paper over corrupt output. The fix was verified against the plan that failed — it parses, validates, and applies clean (10 updates, 17 links, 0 failures) — plus 6000 fuzz payloads.

## What is still not recoverable

**Truncation.** If the planner hits `ingest.v2.plannerBudgetUsd` or its context limit mid-document, the plan stops mid-string. There is no repair, and this is by design: truncation *loses content*, where a trailing comma merely *corrupts syntax*. Inventing the missing half would be worse than failing.

**Plan size is uncapped.** There is no limit on how large a plan the planner may emit, by choice. The consequence is a known, accepted latent bug: a range that reliably produces an oversized plan will fail, hold the cursor, and — because the cursor is held — present the *same* range to the next post-merge, which produces the same oversized plan. That is a retry spiral, and it spends the planner budget each time around.

If you hit it, the symptom is a repeating `failed: applier (malformed-JSON)` or `non-JSON-plan` line in `hooks.log` for an unchanging commit range. The manual break is to advance `llake/last-ingest-sha` past the problem range yourself, accepting that those commits go undocumented, and then ingest a smaller range.

## Distinguishing the failure modes

The applier emits three different diagnostics, and `ingest-v2.sh` greps for them to write a precise `hooks.log` line:

| Applier stderr | `hooks.log` | Meaning |
|---|---|---|
| `non-JSON planner output` | `failed: applier (non-JSON-plan)` | No JSON object found at all — the agent wrote prose, or refused |
| `schema-invalid JSON in plan` | `failed: applier (malformed-JSON)` | JSON-ish but unparseable even after comma stripping — usually truncation |
| schema validator errors | `failed: applier (schema-invalid)` | Parsed fine, but the structure is wrong |

The first two are the planner failing; the third is the planner misunderstanding the format. They call for different responses, which is why they are separated.

## Key Points

- The plan is one all-or-nothing document; everything downstream depends on it parsing.
- A single trailing comma once discarded a fully valid 68KB plan and $3.57 of spend.
- Recovery parses strictly first, so well-formed plans are never rewritten.
- The trailing-comma stripper is a character state machine, not a regex — markdown bodies contain `, ]` as prose.
- Truncated plans are unrecoverable by design: truncation loses content rather than corrupting syntax.
- Plan size is uncapped by choice; an oversized plan produces a cursor-held retry spiral.
- Break the spiral by manually advancing `last-ingest-sha` past the offending range.
- Three distinct stderr strings let `hooks.log` name the exact failure mode.

## Code References

- `hooks/lib/apply_ingest_plan.py:437-497` — `_normalize_plan_text`: fences, then balanced-object scan
- `hooks/lib/apply_ingest_plan.py:500-542` — `_strip_trailing_commas`, the string-aware stripper
- `hooks/lib/apply_ingest_plan.py:545-560` — `_parse_plan_text`, strict-then-retry
- `hooks/lib/apply_ingest_plan.py:686-702` — the three-step read and its distinct diagnostics
- `hooks/lib/ingest-v2.sh:166-171` — where those strings become a `hooks.log` line
- `tests/lib/test_apply_ingest_plan.py` — parse-recovery and failure-classification tests

## See Also

- [[apply-ingest-plan]] — the parse path in context
- [[ingest-v2-pipeline]] — the cursor policy that makes a held range retry
- [[config-schema]] — `ingest.v2.plannerBudgetUsd` and `diffChunkBytes`
- [[debug-hook-failures]] — reading `hooks.log` and agent logs
