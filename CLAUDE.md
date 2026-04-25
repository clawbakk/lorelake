# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository role

This repo **is the LoreLake plugin**, not a LoreLake install. It ships hooks, lib helpers, prompt templates, skills, and the `schema/` directory (split-by-consumer schema files). It does not ship wiki content. A "project" is a separate repo where the plugin is installed — that repo gets a `<project>/llake/` directory holding config, wiki, and runtime state.

Keep these two concepts strictly separate when reading or writing code:

- **Plugin root** (this repo) — immutable-to-agents code under `hooks/`, `templates/`, `schema/`.
- **Project LoreLake** — data-only, under `<project>/llake/`. Owned by the user's project and modified by the agents this plugin spawns.

The canonical operating spec is split across `schema/` (start at `schema/index.md` for the loading guide). It is the spec; the hooks here implement it.

## Common commands

No build step — pure bash + `python3`. Dev dep is only `pytest` (`pip install -r requirements-dev.txt`).

```
# All Python lib tests
python3 -m pytest tests/lib/ -q

# One Python test file
python3 -m pytest tests/lib/test_render_prompt.py -v

# Single pytest test
python3 -m pytest tests/lib/test_read_config.py::test_user_value_wins -v

# All bash lib tests
bash tests/hooks/test_constants.sh
bash tests/hooks/test_detect_project_root.sh
bash tests/hooks/test_agent_id.sh

# Shell syntax check for a hook
bash -n hooks/session-end.sh
bash -n hooks/post-merge.sh

# Shellcheck (if installed)
shellcheck hooks/post-merge.sh hooks/session-end.sh hooks/session-start.sh hooks/lib/agent-run.sh
```

The plugin is invoked via three entry-point hooks wired into Claude Code and git. They are not run directly during development — test changes via the unit tests above, then via a real install in a test project.

## Architecture — three writers, three triggers

LoreLake is maintained by three writers. Two are background agents spawned by hooks; the third (bootstrap) is an in-session skill that runs in the user's foreground Claude Code session and dispatches subagents via the `Task` tool. All three share the same conventions and safety rails but have distinct write surfaces (enforced by `allowedTools` at the shell level and path rules in each prompt or SKILL.md).

| Writer | Kind | Triggered by | Entry point | Workflow source |
|---|---|---|---|---|
| **bootstrap** | in-session skill | user invokes `/llake-bootstrap` | `skills/llake-bootstrap/SKILL.md` | SKILL.md (no prompt template — runs in the user's session, delegates via `Task`) |
| **ingest** | background `claude -p` agent | git `post-merge` on configured branch | `hooks/post-merge.sh` | `hooks/prompts/ingest.md.tmpl` |
| **capture** | background `claude -p` agent | Claude Code `SessionEnd` | `hooks/session-end.sh` (two-pass: triage → capture) | `hooks/prompts/triage.md.tmpl` → `capture.md.tmpl` |

There is also a fourth hook — `hooks/session-start.sh` — which is pure context injection: it concats `templates/session-preamble.md` and the project's `llake/index.md` into Claude Code's session as `additionalContext`. It spawns no agent.

Session capture is **two-pass by design**: a cheap triage agent (short prompt, `Read`-only, small budget) classifies the transcript as `CAPTURE`/`PARTIAL`/`SKIP`. Only on capture/partial does the full capture agent run. This keeps the background cost bounded even on high session volume.

## Shared subsystems

- **Project-root detection** (`hooks/lib/detect-project-root.sh`): env override (`LLAKE_PROJECT_ROOT`) → marker walk for `llake/config.json` → caller falls back to `git rev-parse` if desired. SessionStart/SessionEnd use marker walk; post-merge uses git. The lib never calls git itself.
- **Recursion guard**: every background agent is spawned with `IS_LLAKE_AGENT=true`. All three hooks bail early when that env is set, so agent-driven sessions don't re-trigger capture/ingest.
- **Agent IDs** (`hooks/lib/agent-id.sh`): readable `<adj>-<noun>-HHMMSS` IDs, used in log lines and agent directory names.
- **Agent lifecycle** (`hooks/lib/agent-run.sh`): shared kill-trap helpers. `setup_kill_trap` binds TERM/INT → user-kill, USR1 → timeout. Hooks send themselves USR1 from a watchdog subshell after `timeoutSeconds`. `_agent_cleanup` tree-kills descendants, writes a marker to the agent log, and appends a one-liner to `hooks.log`.
- **Config layering** (`hooks/lib/read-config.py`): dot-key lookup that checks the user's `config.json` first and falls back to this repo's `templates/config.default.json`. Booleans emit `true`/`false`; arrays/objects emit JSON. Tests assume this fallback works end-to-end (`test_read_config.py`).
- **Prompt rendering** (`hooks/lib/render-prompt.py`): substitutes `{{VAR}}` from CLI `KEY=value` args, then per-template custom slots at `config.prompts.<template-name>.<KEY>`, then `{{KEY|fallback:path}}` reads a file (resolved against `--templates-dir` when relative). Exits nonzero on any unresolved placeholder — deliberately strict.
- **Transcript extraction** (`hooks/lib/extract_transcript.py`): reads Claude Code's JSONL session file read-only, filters to visible messages, and samples head+middle+tail (or head+gap-continuations+tail if post-compaction continuations are present). Writes markdown plus `.turns`/`.words` sidecars used by the thin-session filter. Session capture skips sessions below `minTurns` or `minWords`.
- **Agent log formatting** (`hooks/lib/format-agent-log.py`): converts Claude CLI `stream-json` into human-readable agent-execution traces. `--extract-result` pulls the final text for callers that need it (e.g., the triage classification).

## Runtime layout (in the target project, not this repo)

```
<project>/llake/
  config.json            # user config; plugin fills gaps from config.default.json
  index.md               # category catalog (root of the wiki)
  log.md                 # append-only activity log
  last-ingest-sha        # cursor for post-merge ingest
  wiki/<category>/*.md   # pages (fixed categories + project-specific)
  .state/                # gitignored runtime working dir
    hooks.log            # rolled hook audit log
    agents/<id>/         # per-agent working dir (agent.log, *.pid)
    sessions/<id>/       # transcript.md + lock meta for capture
```

Plugin code never writes outside `<project>/llake/`. Agent prompts enforce this; shell-level `allowedTools` + explicit path rules in each prompt template back it up.

## Content standards (from `schema/code-content-standard.md` and `schema/conversation-content-standard.md`)

Agents writing wiki content must satisfy three non-negotiable standards. When editing prompts (`hooks/prompts/*.md.tmpl`) or skill specs (`skills/*/spec.md`), preserve them:

1. **Code-content completeness** (bootstrap, ingest): every page must be sufficient to onboard a contributor without reading the source. Brevity is not a virtue if it forces the reader back to code.
2. **Conversation-content fidelity** (capture): discussion entries include an **immutable Key Facts** block — never edited after creation even if derived pages evolve. Continuations append to the same file (one topic arc = one entry).
3. **Security**: no credentials, connection strings, internal hostnames/IPs, or personal data anywhere in the wiki. Abstract (`"the API key"`) instead of capturing the value.

## Skills

Three user-invoked skills (`disable-model-invocation: true` — never auto-triggered). Each has a `spec.md` under `skills/<name>/`; the actual skill files are scaffolded later via `/skill-creator`:

- `/llake-lady` — install wizard. Writes an install plan, then spawns an executor subagent to walk it.
- `/llake-doctor` — diagnose + repair. Idempotent; runs as the install plan's final phase and again on upgrades / fresh clones.
- `/llake-bootstrap` — runs in the user's active CC session; reads `ingest.include` from the project's `config.json`, decomposes the in-scope codebase, and dispatches subagents via `Task` to populate the wiki from scratch. No `claude -p`, no background, no prompt template.

Bootstrap has **no `bootstrap.*` config keys** — model/effort/budget/timeout all come from the user's CC session. Only `ingest.include` is consulted.

## Conventions to preserve

- **macOS bash 3.2 portability** in shell code: no `$BASHPID`, no bash-4 features. `agent-run.sh` calls this out explicitly.
- **No state in the plugin repo**: everything runtime-generated lives in `<project>/llake/.state/`. Updates to the plugin must reach all installs instantly (no per-project copies of hooks or prompts — `plan.md.tmpl` embeds the plugin path).
- **Config fallback contract**: tests in `tests/lib/test_read_config.py` encode that `config.default.json` is the source of truth for defaults. Do not duplicate defaults into user-facing configs or hook scripts.
- **Prompt renderer is strict**: unresolved placeholders exit nonzero. When adding a new `{{VAR}}`, wire it through both the caller (hook shell script) and the template's placeholder — tests will catch it.
- **Schema is immutable to agents**: the `schema/` files document the spec; bootstrap/ingest/capture prompts all forbid writing to any of them.
- **Custom slot values can contain literal `{{...}}` text**: the renderer's strictness applies to TEMPLATE placeholders, not to the content of slot values. Documentation that mentions placeholder syntax in a slot value is preserved verbatim. Tested by `test_literal_braces_in_slot_value_are_preserved`.
