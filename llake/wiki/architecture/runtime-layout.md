---
title: "Runtime Layout"
description: "The llake/ directory structure in a target project and what each file does"
tags: [architecture, layout, runtime, filesystem]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[plugin-project-duality]]"
  - "[[three-writer-model]]"
  - "[[config-schema]]"
  - "[[post-merge-hook]]"
  - "[[session-end-hook]]"
---

## Overview

Every project where LoreLake is installed gets a single `llake/` directory at the project root. This directory is the complete data boundary for LoreLake in that project. The plugin code never reads or writes outside it (except to read transcript files from Claude Code's own conversation store). Understanding what each file and directory does — who writes it, who reads it, what breaks if it is missing — is essential for debugging, recovery, and manual intervention.

---

## Full Directory Tree

```
<project>/llake/
  config.json              # project configuration (user-editable)
  index.md                 # wiki category catalog (root of the wiki)
  log.md                   # append-only activity log (hooks + agent completions)
  last-ingest-sha          # SHA cursor for post-merge ingest
  wiki/
    decisions/             # architectural and design decisions
    gotchas/               # non-obvious behaviors, traps, and known issues
    discussions/           # session capture output (conversation-derived)
    playbook/              # how-to guides and runbooks
    <custom>/              # project-specific categories (bootstrap/ingest may create)
  .state/                  # gitignored runtime working directory
    hooks.log              # rolled hook audit log
    agents/
      <adj-noun-HHMMSS>/
        agent.log          # full execution trace for one agent run
        ingest.pid         # PID file (present only while agent is running)
        triage.pid         # PID file for triage pass (session capture only)
        capture.pid        # PID file for capture pass (session capture only)
    sessions/
      <session-id>/
        transcript.md      # sampled, extracted session transcript
        triage-result.txt  # triage classification + reason (CAPTURE/PARTIAL/SKIP)
        meta               # session lock file (agent ID, start time, timestamp)
```

---

## File-by-File Reference

### `config.json`

**Who writes it:** `/llake-lady` (initial creation), `/llake-doctor` (repair). Users may edit it manually.

**Who reads it:** Every hook script, via `hooks/lib/read-config.py`. The config library checks this file first and falls back to `templates/config.default.json` in the plugin for any missing key.

**Purpose:** Project-specific configuration. Controls which branch ingest monitors, budget caps, model choices, session capture settings, include paths, and more. See [[config-schema]] for all keys.

**What breaks if missing:** All three hooks call `read-config.py` before spawning any agent. The `post-merge` hook exits silently (`[ -f "$CONFIG_FILE" ] || exit 0`) if `config.json` does not exist — this is also how projects without LoreLake installed are identified. SessionEnd uses the config for turn/word thresholds and model choices; if missing, it falls back to defaults from `config.default.json` but only if the path is still resolvable.

**Recovery:** Run `/llake-doctor`. It will regenerate a minimal `config.json` using defaults.

---

### `index.md`

**Who writes it:** Bootstrap (initial population), ingest (updates as categories and pages are added), `/llake-doctor` (repairs missing or corrupt entries).

**Who reads it:** `session-start.sh` — the index is concatenated with `templates/session-preamble.md` and injected as `additionalContext` at every Claude Code session start. This is how Claude learns what pages exist in the wiki before the user types their first message.

**Purpose:** The root catalog of the wiki. Lists categories and key pages so the session-start hook can give Claude an orientation to the project knowledge base without loading every page.

**What breaks if missing:** Session start proceeds silently (the hook checks `[ -f "$INDEX_FILE" ]` before reading it). Claude will not receive any wiki context at session start, defeating one of LoreLake's core value propositions. Ingest will also lose track of existing structure.

**Recovery:** Run `/llake-bootstrap` (full re-population) or `/llake-doctor` (attempts to reconstruct from existing wiki pages).

---

### `log.md`

**Who writes it:** Every hook script (`session-start.sh`, `session-end.sh`, `post-merge.sh`) appends one-liners on start and completion. Agents also append their own completion lines via `hooks/lib/agent-run.sh`.

**Who reads it:** Humans and `/llake-doctor` for debugging. Not read by any agent during normal operation.

**Purpose:** Append-only activity log. Provides a human-readable audit trail of every hook invocation, skip reason, agent launch, and agent completion. Lines follow the format:

```
2026-04-23 14:33:11 | post-merge    | started → done: spawned agent brave-lake-143311
2026-04-23 14:35:02 | agent-done    | completed: agent brave-lake-143311 finished (exit 0, commits: abc1234..def5678)
```

The hook also handles its own rotation: when `log.md` exceeds `logging.maxLines` lines, it trims to `logging.rotateKeepLines` lines. A crashed hook (one that started but never wrote its completion line) is detected on the next invocation and appended with ` → CRASHED`.

**What breaks if missing:** Hooks create it automatically on first write. Nothing else depends on it.

---

### `last-ingest-sha`

**Who writes it:** `hooks/post-merge.sh` (the shell script, **not** the agent). The hook writes the current HEAD SHA to this file in three situations:
1. After a pre-flight diff determines no relevant files changed (advances the cursor without spawning an agent)
2. After detecting an invalid commit range due to a force-push (resets to current HEAD)
3. The ingest agent itself is expected to update it after successfully completing ingest — see `ingest.md.tmpl` for the agent's instruction to write the new SHA at the end of its run

**Who reads it:** `post-merge.sh` on every invocation. The hook diffs `last-ingest-sha..HEAD` to determine what commits to pass to the agent.

**Purpose:** The ingest cursor. Without it, the hook cannot know which commits have already been ingested, leading to either re-processing the entire history or missing commits.

**What breaks if missing:** `post-merge.sh` exits with `"skipped: no last-ingest-sha file"` — ingest is silently disabled until the file is created. No data is lost; the hook just will not fire.

**Recovery:** Write the current HEAD SHA to the file:
```bash
git rev-parse HEAD > llake/last-ingest-sha
```
Then run `/llake-doctor` to verify.

---

### `wiki/<category>/*.md`

**Who writes it:** Bootstrap, ingest, and capture, each within their write-surface rules (see [[three-writer-model]]).

**Who reads it:** 
- `session-start.sh` reads `index.md` (the catalog) to inject context
- Agents read existing pages before updating them
- Humans read pages directly

**Purpose:** The actual knowledge base. Each file is a markdown page with YAML frontmatter. Fixed categories are `decisions/`, `gotchas/`, `discussions/`, `playbook/`. Bootstrap and ingest may create additional project-specific categories.

**What breaks if missing (individual pages):** Nothing structural — missing pages simply mean that topic has not been documented yet. The category directories must exist for agents to write into them; `/llake-doctor` creates missing category directories.

**What breaks if missing (all wiki pages):** The wiki is empty. Run `/llake-bootstrap` to repopulate from the codebase.

---

### `.state/` (gitignored)

The `.state/` directory is created by hook scripts (`mkdir -p "$STATE_DIR"`) on first use. It is gitignored and should not be committed. It holds ephemeral runtime state that does not need to persist across environments.

#### `.state/hooks.log`

**Who writes it:** `session-end.sh` (via `$LOG_FILE` variable). Not the same as `log.md` — this is the raw hook audit log used during agent lifecycle tracking. Actually, looking at the hook source, `LOG_FILE` points to `.state/hooks.log` while `log.md` and `hooks.log` are distinct: the hook appends one-liners to `hooks.log` in `.state/`, not to `llake/log.md`. The `log.md` in `llake/` is the user-facing log.

Wait — examining the source: in `session-end.sh` and `post-merge.sh`, `LOG_FILE="$STATE_DIR/hooks.log"` — the hooks write to `.state/hooks.log`. The `llake/log.md` is the **user-visible** activity log that the ingest/capture agents may append to as part of their wiki-writing work.

**Purpose:** Hook-level audit log (start/end lines for each hook invocation and agent completion). Rolled automatically using `logging.maxLines` / `logging.rotateKeepLines` from config.

**Recovery:** If deleted, it is recreated on next hook invocation. No data is lost.

#### `.state/agents/<id>/`

Each agent run gets its own subdirectory identified by a human-readable ID in the format `<adjective>-<noun>-HHMMSS` (e.g., `brave-lake-143311`). This ID is generated by `hooks/lib/agent-id.sh` and used in log lines for traceability.

**`agent.log`:** Full execution trace of the agent run, formatted by `hooks/lib/format-agent-log.py` from the `claude --output-format stream-json` stream. Includes tool calls, tool results, model output, and completion status. This is the primary debugging artifact for a failed or misbehaving agent.

**`*.pid` files:** Written at agent spawn, deleted on completion. If a `.pid` file is present when no agent is running, the agent crashed. Used by the kill-trap in `agent-run.sh` for tree-killing on timeout.

**What breaks if missing:** The `agents/` directory is created with `mkdir -p` before each agent run. Nothing depends on old agent directories existing.

#### `.state/sessions/<session-id>/`

Created by `session-end.sh` for each session being processed. Deleted after the capture agent completes (or if the session is skipped).

**`transcript.md`:** The sampled, extracted transcript in markdown format, written by `hooks/lib/extract_transcript.py`. The triage and capture agents read this file — it is their primary input. Sampling uses a head+middle+tail strategy to fit the transcript within a budget.

**`triage-result.txt`:** Written by the triage agent via `--extract-result`. Contains the classification (`CAPTURE`, `PARTIAL`, or `SKIP`) on the first line, optionally followed by the reason. The capture agent receives the classification and reason as prompt variables.

**`meta`:** The session lock file. Prevents duplicate capture runs for the same session. Contains:
```
agent: brave-lake-143311
started: 2026-04-23 14:33:11
timestamp: 1745329991
```
The `timestamp` field is a Unix epoch used to detect stale locks. If a lock is older than `sessionCapture.lockStalenessSeconds`, it is broken and the session is reprocessed.

**What breaks if missing:** If the `sessions/` directory or the `meta` file is deleted mid-run, the agent continues normally (it already has the transcript in memory). The deduplication guard will not fire on subsequent invocations for that session.

---

## Gitignore Expectations

The project's `.gitignore` (or `llake/.gitignore`) should ignore the `.state/` directory:

```
llake/.state/
```

`/llake-lady` sets this up during install. The user-visible files (`config.json`, `index.md`, `log.md`, `last-ingest-sha`, `wiki/**`) should be committed.

---

## Key Points

- `llake/` is the complete data boundary; no plugin code lives here.
- `config.json` is the config entry point; missing keys fall back to `templates/config.default.json` in the plugin.
- `last-ingest-sha` is written by the hook shell script before the agent runs, not by the agent.
- `.state/` is gitignored; it holds ephemeral runtime artifacts (agent logs, PID files, session scratch space).
- `agent.log` under `.state/agents/<id>/` is the primary debugging artifact for a failed background agent.
- Session directories under `.state/sessions/` are cleaned up after each capture run; their presence mid-run indicates an active agent.
- `hooks.log` in `.state/` is the hook-level audit log; `log.md` in `llake/` is the user-visible wiki activity log.

---

## Code References

- `hooks/session-end.sh:36-43` — all path variables derived from `PROJECT_ROOT`
- `hooks/session-end.sh:130-134` — agent directory creation (`mkdir -p "$AGENT_DIR"`)
- `hooks/session-end.sh:184-196` — session lock logic (meta file read/write)
- `hooks/post-merge.sh:48-54` — path variables including `SHA_FILE`
- `hooks/post-merge.sh:141-163` — `last-ingest-sha` read and validation
- `hooks/lib/extract_transcript.py` — writes `transcript.md` + `.turns`/`.words` sidecars
- `hooks/lib/agent-run.sh` — PID file management and kill-trap
- `hooks/lib/format-agent-log.py` — converts stream-json to `agent.log` content

---

## See Also

- [[plugin-project-duality]] — why project data and plugin code are in separate locations
- [[three-writer-model]] — which writers produce which files
- [[config-schema]] — all configuration keys and their defaults
- [[post-merge-hook]] — how `last-ingest-sha` is used in the ingest flow
- [[session-end-hook]] — full session capture flow including session directory lifecycle
