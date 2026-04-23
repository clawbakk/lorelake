---
title: "ADR-003: Bash 3.2 Portability Requirement"
description: "Why all hook shell code must be bash 3.2 compatible (macOS stock bash constraint)"
tags: [decisions, architecture]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[agent-run]]"
  - "[[bash-3-2-portability]]"
---

# ADR-003: Bash 3.2 Portability Requirement

## Decision

All hook shell code — including `hooks/session-end.sh`, `hooks/post-merge.sh`, and shared libraries under `hooks/lib/` — must be compatible with bash 3.2. No bash 4+ features are permitted. Contributors must not use `$BASHPID`, `declare -A` (associative arrays), `mapfile`/`readarray`, `**` globbing, or other features introduced after bash 3.2.

The constraint is documented in `hooks/lib/agent-run.sh`'s header comment: _"Bash portability: targets macOS /bin/bash 3.2. Do not use $BASHPID."_

## Context

macOS ships with bash 3.2 at `/bin/bash`. This version dates from 2007 and has not been updated on macOS because later bash versions (4.x and 5.x) are licensed under GPLv3, which Apple does not distribute with the OS.

LoreLake hooks are wired into git (via `.git/hooks/post-merge`) and into Claude Code (via `SessionEnd` and `SessionStart` event handlers) on the user's local machine. The shebang line `#!/bin/bash` resolves to `/bin/bash` on macOS, which is bash 3.2.

A developer on stock macOS who has not explicitly installed bash via Homebrew will have only bash 3.2 available. If a hook uses a bash 4+ feature, the hook will fail silently or produce a cryptic error, and LoreLake will simply stop working for that user with no obvious explanation.

The specific pattern that commonly causes issues is `$BASHPID`. In bash 4+, `$BASHPID` gives the PID of the current subshell (different from `$$` when inside a subshell). In bash 3.2, `$BASHPID` is undefined and expands to an empty string. The `agent-run.sh` library uses `sh -c 'echo $PPID'` instead, which is POSIX-portable and works everywhere. This is why `hooks/session-end.sh` contains `MY_PID=$(sh -c 'echo $PPID')` rather than `MY_PID=$BASHPID`.

See [[agent-run]] for the kill-trap and watchdog implementation that relies on this pattern, and [[bash-3-2-portability]] for a reference list of known pitfalls.

## Rationale

**LoreLake must work on stock macOS out of the box.** Requiring Homebrew bash as a prerequisite would make the plugin unusable for a significant portion of the target audience — developers who use macOS with default tooling. The installation experience for a Claude Code plugin should be a single command, not "first install Homebrew, then install bash, then...".

**The bash 3.2 constraint has very low practical cost.** The hook scripts are glue code: they read config, compute paths, check conditions, and spawn background processes. None of these tasks require bash 4+ features. The most important restriction — no `$BASHPID` — is handled by the portable `sh -c 'echo $PPID'` idiom, which is already established in `agent-run.sh`. Associative arrays (`declare -A`) could simplify a few argument-passing patterns but are not needed.

**Silent failures are worse than the constraint.** If a bash 4+ feature is accidentally introduced and the code is tested only on Linux (where `/bin/bash` is typically 5.x), a macOS user will see the hook do nothing with no error message — the hook exits nonzero and git/Claude Code silently discards it. Keeping bash 3.2 as the target means all testing environments reflect production.

## Alternatives Considered

| Alternative | Reason rejected |
|---|---|
| **Require bash 4+ (document as prerequisite)** | Requires Homebrew on macOS; breaks for all stock macOS users; adds installation friction |
| **Use zsh instead of bash** | zsh is macOS's default shell since Catalina, but it is not universal on Linux and CI environments; zsh syntax differs meaningfully from bash; hooks would need a different shebang and testing matrix |
| **Rewrite hook glue in Python** | Python 3 is already used for lib utilities (`read-config.py`, `render-prompt.py`, `extract_transcript.py`); entry-point hooks are a natural fit for shell because they are invoked by git and Claude Code as shell scripts. Moving to Python would require shebang `#!/usr/bin/env python3` and add process startup overhead for every hook invocation |
| **Use `/bin/sh` (POSIX only)** | More portable than bash 3.2 but loses bash features that are genuinely useful and safe: arrays (even bash 3.2 arrays), process substitution `<(...)`, `[[ ]]` conditionals. The hooks already rely on bash-specific idioms that are well within 3.2. |

## Consequences

**What this commits you to:**

- Every contributor who writes or modifies hook shell code must know the bash 3.2 boundary. New contributors often reach for `$BASHPID` or `declare -A` reflexively and must be reminded.
- Shellcheck can partially enforce this via `--shell=bash` but does not flag all 3.2 incompatibilities (it targets the version installed on the CI runner, which may be 5.x). Manual review is still required for new shell code.
- The `sh -c 'echo $PPID'` idiom for obtaining a subshell's PID is non-obvious and must be preserved. Replacing it with `$BASHPID` would break macOS silently.
- CI environments (Linux, typically bash 5.x) will not catch bash 4+ regressions introduced accidentally. The plugin should ideally be tested in a macOS environment or with an explicit `bash --version` check in tests.
- Process substitution `<(command)` and bash 3.2 arrays (`ARRAY=()`, `${ARRAY[@]}`) are fine to use — they predate bash 4. Only features gated on bash 4+ are forbidden.

## See Also

- [[agent-run]] — shared kill-trap helper that established the `sh -c 'echo $PPID'` idiom for subshell PID detection
- [[bash-3-2-portability]] — catalogue of specific bash 4+ features to avoid and their bash 3.2 equivalents
