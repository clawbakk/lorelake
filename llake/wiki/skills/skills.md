# Skills

User-invoked Claude Code skills. All four have `disable-model-invocation: true` — they are never auto-triggered.

| Page | Description |
|---|---|
| [[llake-lady-skill]] | Sets up llake/ in a project, wires the post-merge hook, and runs doctor to verify |
| [[llake-doctor-skill]] | Idempotent health checker that diagnoses and repairs LoreLake install drift |
| [[llake-bootstrap-skill]] | One-time in-session orchestrator that populates the wiki from scratch via parallel subagents |
| [[llake-lint-skill]] | On-demand lint pass for broken links, one-sided related:, stale pages, and missing sections |
