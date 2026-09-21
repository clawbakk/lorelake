---
updated: 2026-09-20
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
