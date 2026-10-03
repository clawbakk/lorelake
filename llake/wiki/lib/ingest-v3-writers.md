---
title: "Ingest v3 Writers, Verifier, Checks and Fix Round"
description: "The v3 agent spawn helper, writer pool with run cap and retries, writer and verifier prompts, the anchor rule, $0 post-write checks and the fix round"
tags:
  - "lib"
  - "ingest"
  - "v3"
  - "agents"
  - "writers"
  - "python"
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-pipeline]]"
  - "[[ingest-v3-snapshots]]"
  - "[[ingest-v3-brief]]"
  - "[[ingest-v3-templates]]"
  - "[[ingest-v3-gap-record]]"
  - "[[claude-p-tools-flag]]"
  - "[[popen-stdin-locale-encoding]]"
  - "[[v3-agents-escape-tree-kill]]"
  - "[[frontmatter-parser]]"
---
# Ingest v3 Writers, Verifier, Checks and Fix Round

## Overview

This page covers the part of [[ingest-v3-pipeline]] that changes wiki pages:

- `agent.py` spawns every `claude -p` process in v3.
- `dispatch.py` runs writers, and optional verifiers, in a pool under the run budget cap.
- `prompts.py` builds their prompts.
- `anchors.py` holds the code-checked anchor rule.
- `checks.py` inspects every write at no cost.
- `fix.py` gives flagged pages one more writer pass.

Snapshots and reverts are described in [[ingest-v3-snapshots]].

## Spawning an agent — `agent.py`

`build_argv(model, effort, budget, tools, allowed, json_schema, system_file)` produces:

```
claude -p --model <m> --effort <e> --max-budget-usd <b>
  --setting-sources "" --strict-mcp-config --no-session-persistence
  --exclude-dynamic-system-prompt-sections --permission-mode dontAsk
  --tools <tools> --allowedTools <allowed> --output-format stream-json --verbose
  [--json-schema <schema>] [--append-system-prompt-file <shared prefix>]
```

- `tools` must stay within `Read, Glob, Grep, Edit, Write`, or `build_argv` raises `ValueError`. No stage gets `Bash`.
- `--setting-sources ""` and `--strict-mcp-config` keep the user's settings, hooks and MCP servers out of the agent. See [[claude-p-tools-flag]].
- `allow_rule(path)` returns `Edit(/<absolute path>)` for exactly one file. It refuses a directory or a path containing glob characters (`*?[]{}`), so a crafted page name cannot widen the permission.

`Agent(stage, argv, prompt, cwd, out_dir, timeout, cache_ttl, agent_id)` launches the process:

- **Working directory:** the project root, so agents read the code at head.
- **Environment:** adds `IS_LLAKE_AGENT=true`, `CLAUDE_CODE_PROMPT_CACHE_TTL=<cacheTtl>`, `LLAKE_AGENT_STAGE` and `LLAKE_AGENT_ID=<run id>_<stage>`.
- **Prompt:** written to stdin as UTF-8 whatever the locale (see [[popen-stdin-locale-encoding]]).
- **Session:** `start_new_session=True`, so the agent and its children form one process group. `kill()` sends `SIGTERM` to the group, waits a grace period, then sends `SIGKILL`. Every started agent is kept in a live registry so the run can stop them all with `kill_live` (see [[v3-agents-escape-tree-kill]]).
- **Files** in `stages/`: `<stage>.prompt.md`, `<stage>.jsonl` (the stream), `<stage>.jsonl.err` and `<stage>.summary.json`.

`clip_timeout` caps each agent's timeout at the time left before the run deadline, with a 5 s minimum.

`summarize` extracts from the stream:

- cost, turns and token classes;
- first-turn cache read/write, and peak context;
- permission denials and the structured output;
- every `Edit`/`Write`/`MultiEdit`/`NotebookEdit` call, used for write attribution;
- the failure class (`none`, `infra`, `work`; see [[ingest-v3-pipeline]]).

A kill with reason `infra-stop` counts as infra. Any other kill reason (`timeout`, `aborted`, `killed`) counts as work.

## The pool — `dispatch.pool`

Writers, verifiers and fixers all share one loop:

1. Past the deadline, the pool sets stop to `timeout`.
2. While stopping, it kills running agents with reason `infra-stop` or `timeout`.
3. It collects finished agents: records the stage in the ledger and calls `on_done`. An `infra` return from `on_done` sets stop to `infra`.
4. While stopping, every queued job goes to `on_skip` with the stop reason.
5. Otherwise it starts jobs while fewer than `writerConcurrency` are running:
   - **Warm-up.** A writer or fixer waits while another writer is running that has not finished its first turn, unless a writer has already completed. This caches the shared prefix once.
   - **Run cap.** A job starts only if `spent + budgets of running agents + its own budget <= maxRunBudgetUsd`. Otherwise it waits, or calls `on_skip(job, "run-cap")` when nothing is running.

On any exception, including the run's `Killed`, `_abort` kills every running agent with a 2 s grace, reverts each writer/fixer bundle, records the agent's cost and re-raises. No agent outlives the pool.

## Writer jobs — `run_writers`

There is one job per bundle, named `writer-bNN`. Before a writer or fixer starts, its pages are snapshotted: to `bundles/bNN/snapshot`, to `retryK` for retries, or to `fix-snapshot` for fixers.

When a writer finishes, there are four cases:

- **Success.** Class `none` and a structured status valid against `writer-status.json`.
  - The pages are settled.
  - Status `corrected` maps to outcome `corrected`, `declared-gap` to `declared`, and `no-change` to `no-change`.
  - A page missing from the status gets `corrected` if it changed, else `no-change`, plus a history note.
  - `otherStale` reports go to the ledger.
  - With the verifier on, a verifier job for the changed pages goes to the front of the queue.
- **Failure.** Class `work`, or a missing or invalid status.
  - The bundle is reverted. A page that cannot be restored is marked `unrestored`, left as written and never retried.
  - Unless the job was already a retry, each page goes back to the front of the queue alone, as `writer-bNN-sK`.
  - Pages that are not retried end as `writer-failed`.
- **Infra failure, or any failure while stopping.** The bundle is reverted and its pages end as `infra` (or `timeout`). An infra failure stops all further dispatch.
- **Never dispatched.** The outcome is the skip reason: `run-cap`, `timeout` or `infra`. A page with no outcome at the end becomes `timeout`.

These outcomes feed the gap record (see [[ingest-v3-gap-record]]).

## Writer prompts — `prompts.py`

A writer prompt has two parts, rendered from templates (see [[ingest-v3-templates]]):

- **Shared prefix** (`ingest.v3.writer-shared.md.tmpl`). It is rendered once per run to `stages/writer.shared.md` and passed with `--append-system-prompt-file`. It holds the writer contract, today's date, the `writeMode` rule, the run's themes, removed and added names, and the wiki catalog. The catalog lists the valid link targets, including planned new pages.
- **Bundle part** (`ingest.v3.writer-bundle.md.tmpl`), the task message. It holds the range, the patches dir, the page list and one block per page.

Each page block (`page_block`) contains:

- class, severity, kind, themes and reason;
- a note if the page is carried from an earlier run, or an instruction to create it;
- the numbered stale claims and the new facts;
- removed-name hit lines, anchors into changed lines, changed log/error literals and evidence;
- up to 25 anchors that are already broken at head, with lines quoted by a claim listed first;
- for a fixer only, the flagged findings.

`writeMode` is `edit` (targeted `Edit` calls; `Write` only for a new page) or `write` (one whole-page `Write` per page). Any other value raises `ValueError`.

The writer contract, in short:

1. Edit only owned pages. Report stale text on other pages in `otherStale` (verbatim quote, head, severity).
2. Claims, hits, anchors and new facts are leads. Verify each at head; wrong leads go in `rejected`.
3. On state pages, make every listed claim true at head. No change narration ("previously", commit hashes, ticket names).
4. Never rewrite a record page's body. Set `status:` to `deprecated` or `stale` and add one dated `Superseded` or `Resolved` note under the title.
5. A page with a major claim must be corrected. A gap may be declared only for minor claims, listed in `claimsLeft`.
6. Fix or remove every `path:line` anchor on an added or changed line.
7. Keep frontmatter valid and `description:` true. Never touch `updated:` or category indexes. Link only to catalog pages. Never write secrets.
8. End with the structured status: one entry per owned page with `status`, `claimsLeft`, `rejected` and `note`.

## The verifier (optional)

`verifierMode` is `off` (the default), `accuracy` or `accuracy+residual`. When it is on, each writer bundle with changed pages gets a read-only verifier. The verifier sees each page's diff and its brief claims and returns `verifier-findings.json`:

- `accuracy`: an added statement that is false at head.
- `residual`: a brief claim still present in substance. Decision records are exempt.

If the verifier fails or is skipped, its pages are marked `unverified`. Findings whose quote is still on the page go to the fix round. Findings that remain at finalize and were not rejected by the writer become `flagged` claims with source `verifier:accuracy` or `verifier:residual`.

## The anchor rule — `anchors.py`

An anchor has the form `path:line[-end]` or `path#L12`, using a known source extension. It is good when all three hold:

- the path resolves to exactly one tracked file at head outside `llake/`, by exact path, unique suffix or unique basename;
- the line range is inside the file;
- the cited lines (±2) contain a backticked identifier from the anchoring line, or, failing that, share a word part of at least 4 characters with it (stop words excluded).

`check_anchors` returns a reason for each bad anchor and is used by the checks. `broken_anchors` lists a page's already-broken anchors as writer leads.

## Post-write checks — `checks.run_checks`

Checks run twice: after writing (the `write` pass) and after the fix round (the `fix` pass). They cover pages whose outcome is `corrected`, `declared` or `no-change`:

- **Status vs diff.** A page reported `no-change` but changed, or `corrected` but unchanged. In the second case the page's brief claims whose quote is on the page are flagged.
- **Frontmatter.** It must parse with `hooks/lib/frontmatter.py` (see [[frontmatter-parser]]) and have `title` and `description`.
  - If a writer broke valid frontmatter, the page is restored from its snapshot and its outcome becomes `reverted`.
  - If a fixer broke it, the page is reverted to its post-write state.
  - A page that cannot be restored becomes `reverted` and `unrestored`.
- **Dangling links.** A `[[slug]]` on an added line (not a `related:` list line) that names no page and no planned page is unlinked to plain text.
- **Flags.**
  - `check:removed-name`: a removed name still on a state page.
  - `check:quote-residue`: a brief claim's quote still on a corrected non-record page. Rejected claims and removed-name claims are exempt.
  - `check:anchor`: a bad anchor on an added line.
- **Concurrent changes.** Files under `llake/` that changed during the run, which the run neither owns nor attributed, are reported and never reverted.

Results go to `checks.json` (latest pass) and to `checks.write.json` / `checks.fix.json`.

## The fix round — `fix.run_fix_round`

The fix round runs when `fixRound` is `on` and writing did not stop on `infra` or `timeout`:

- **Findings.** For each changed page with outcome `corrected`, `declared` or `no-change`: its check flags, plus verifier findings whose quote is still on the page.
- **Jobs.** One fixer per bundle with findings, named `fixer-bNN`. It uses the writer model and budget, the same pool and the same caps. There are no single-page retries.
- **Success.** Pages are settled and marked `fixed`. A `declared-gap` status turns the outcome into `declared`.
- **Failure.** Pages are reverted to their post-write state.

There is one round only. Checks then run again. At finalize, a page whose fixer did not succeed is owed as a `flagged` gap.

## Key Points

- Every agent is spawned through `build_argv`: no `Bash`, `--permission-mode dontAsk`, and no user settings or MCP servers.
- Writers can edit only their own pages, through per-file `Edit(/path)` rules. Write attribution re-checks every write afterwards.
- The pool reserves budget before every spawn and warms the prompt cache with one writer first. Any exception kills and reverts every running agent.
- A failed bundle is retried page by page once. A second failure is `writer-failed`. An infra failure stops dispatch.
- Checks cost nothing. They revert broken frontmatter, unlink dangling links and flag residue, removed names and bad anchors.
- The fix round is a single pass. Whatever stays flagged becomes a gap.

## Code References

- `hooks/lib/ingest_v3/agent.py:54` — `build_argv`
- `hooks/lib/ingest_v3/agent.py:69` — `allow_rule`
- `hooks/lib/ingest_v3/agent.py:109` — `summarize`, the failure classes
- `hooks/lib/ingest_v3/agent.py:156` — `Agent`; the `Popen` call is at `agent.py:177`
- `hooks/lib/ingest_v3/dispatch.py:46` — `pool`
- `hooks/lib/ingest_v3/dispatch.py:98` — `_abort`
- `hooks/lib/ingest_v3/dispatch.py:140` — `job_spawn_args`
- `hooks/lib/ingest_v3/dispatch.py:191` — `run_writers`
- `hooks/lib/ingest_v3/prompts.py:22` — `shared_prefix`
- `hooks/lib/ingest_v3/prompts.py:83` — `bundle_prompt`
- `hooks/lib/ingest_v3/prompts.py:95` — `verifier_prompt`
- `hooks/lib/ingest_v3/anchors.py:79` — `check_anchors`
- `hooks/lib/ingest_v3/checks.py:80` — `run_checks`
- `hooks/lib/ingest_v3/fix.py:34` — `run_fix_round`
- `tests/lib/test_v3_agent.py`, `tests/lib/test_v3_dispatch.py`, `tests/lib/test_v3_checks.py`, `tests/lib/test_v3_fix.py`, `tests/lib/test_v3_writer_prompts.py`

## See Also

- [[ingest-v3-snapshots]] — snapshots, settle and reverts used here
- [[ingest-v3-brief]] — where bundles come from
- [[ingest-v3-templates]] — writer and verifier templates
- [[ingest-v3-gap-record]] — how outcomes become gaps
- [[claude-p-tools-flag]] — why `--tools` matters in `-p` mode
