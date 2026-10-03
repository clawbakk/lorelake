---
updated: 2026-10-03
---
# Architecture

Top-level system design — the three-writer model, plugin/project separation, and runtime directory layout.

| Page | Description |
|---|---|
| [[three-writer-model]] | How bootstrap, ingest, and capture divide write surface and responsibilities |
| [[plugin-project-duality]] | The plugin repo vs a target project's llake/ install — what lives where |
| [[runtime-layout]] | The llake/ directory structure in a target project and what each file does |
| [[ingest-v3-pipeline]] | Staged ingest with $0 leads, an analysis agent that writes a brief, a recall pass, parallel per-page writers, $0 checks, a fix round, and code-owned finalize with a gap record |
