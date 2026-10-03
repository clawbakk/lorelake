---
updated: 2026-10-03
---
# Lib

Python library modules invoked by the shell hooks. Each module is a standalone CLI script with no shared state.

| Page | Description |
|---|---|
| [[read-config]] | Dot-key config lookup with user config + defaults layering |
| [[render-prompt]] | Strict {{VAR}} placeholder substitution for prompt template files |
| [[extract-transcript]] | JSONL session reader that samples and writes markdown transcripts with sidecar metadata |
| [[format-agent-log]] | Converts Claude CLI stream-json output to human-readable traces; --extract-result for callers |
| [[ingest-gate]] | Post-merge batching gate — EMPTY / WAIT / RUN from net range churn and time since the last ingest |
| [[ingest-v3-run-planning]] | plan.py and state.py — v3 run kinds (empty, gap-only, range, split, skip), the analysis failure counter, the run journal and dead-run recovery |
| [[ingest-v3-brief]] | The $0 inputs (removed names, hit/anchor/literal indexes, patches), the analysis and recall agents, brief assembly and validation, and deterministic bundling |
| [[ingest-v3-writers]] | The v3 agent spawn helper, writer pool with run cap and retries, writer and verifier prompts, the anchor rule, $0 post-write checks and the fix round |
| [[ingest-v3-snapshots]] | Pre-run copy, per-bundle page snapshots, three-state restore, write attribution that reverts out-of-surface writes, and the kill revert |
| [[ingest-v3-finalize]] | The code-owned end of a v3 run — updated dates, category index rows, gap record, log entry, cursor, skip record — and report.md run checks |
| [[ingest-v3-gap-record]] | llake/ingest-gaps.json — the pages a v3 run could not bring current, with causes and quoted stale claims, plus skipped ranges; carried into the next run |
