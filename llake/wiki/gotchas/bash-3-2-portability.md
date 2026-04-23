---
title: "Bash 3.2 Portability"
description: "macOS ships bash 3.2 — forbidden features and safe replacements for hook shell code"
tags: [gotchas, bash, portability, hooks]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[agent-run]]"
  - "[[adr-003-bash-3-2-portability]]"
---

# Bash 3.2 Portability

## What It Is

macOS ships `/bin/bash` at version 3.2, which predates many bash 4+ features that developers commonly use. All LoreLake hook shell scripts (`hooks/session-end.sh`, `hooks/post-merge.sh`, `hooks/session-start.sh`, `hooks/lib/agent-run.sh`, and friends) must run correctly under bash 3.2. Using any bash 4+ construct silently fails or produces a syntax error at runtime on every stock macOS machine.

The following constructs are **forbidden** in LoreLake hook shell code:

| Forbidden | Reason | Safe replacement |
|---|---|---|
| `$BASHPID` | bash 4+ only | `$(sh -c 'echo $PPID')` |
| `declare -A` | associative arrays, bash 4+ | positional args or temp files |
| `mapfile` / `readarray` | bash 4+ | `while IFS= read -r` loop |
| `\|&` pipe operator | bash 4+ | `2>&1 \|` |
| `${var,,}` / `${var^^}` | case conversion, bash 4+ | `tr '[:upper:]' '[:lower:]'` / `tr '[:lower:]' '[:upper:]'` |
| `[[ ... ]]` regex match `=~` with capture groups | bash 4+ capture groups | use `grep` or `sed` for extraction |

## Why It Exists

Apple ships bash 3.2 as the system shell because GPLv3 licensing prevents them from distributing newer bash versions. Even developers who have installed a newer bash via Homebrew will have `/bin/bash` resolve to 3.2 unless they have explicitly changed their system configuration. Hook shebangs use `#!/bin/bash`, which resolves to the system bash. Since hooks must work out-of-the-box on every developer's machine without requiring manual setup, the code must target 3.2.

## Symptoms

- A `syntax error near unexpected token` in a hook script — most commonly when bash encounters `declare -A`, `mapfile`, or `|&`.
- A hook that appears to work but produces wrong output — for example, using `$BASHPID` where bash 3.2 simply expands it to an empty string, causing PID-dependent logic to fail silently.
- `shellcheck` passing locally (on a machine with a Homebrew bash 4+) but the hook failing on another developer's machine.

## The Fix

**1. Use `$(sh -c 'echo $PPID')` instead of `$BASHPID`.**

`$BASHPID` returns the PID of the current subshell. In bash 3.2, it does not exist. The portable replacement spawns a minimal POSIX shell and prints its parent PID, which is the subshell you care about.

Wrong:
```bash
MY_PID=$BASHPID
```

Right (from `hooks/lib/agent-run.sh`, line 223 / 225):
```bash
MY_PID=$(sh -c 'echo $PPID')
```

This exact pattern is used in both `session-end.sh` and `post-merge.sh` inside their background subshells where the PID is needed for `kill_tree`.

**2. Use `while IFS= read -r` instead of `mapfile`/`readarray`.**

Wrong:
```bash
mapfile -t INCLUDE_PATHS < <(python3 print_paths.py)
```

Right (from `hooks/post-merge.sh`):
```bash
INCLUDE_PATHS=()
while IFS= read -r line; do
  [ -n "$line" ] && INCLUDE_PATHS+=("$line")
done < <(python3 -c "..." "$INCLUDE_JSON" 2>/dev/null)
```

**3. Use `2>&1 |` instead of `|&`.**

Wrong:
```bash
claude -p "$PROMPT" |& python3 formatter.py >> "$AGENT_LOG"
```

Right (from `hooks/session-end.sh` and `hooks/post-merge.sh`):
```bash
claude -p "$PROMPT" --output-format stream-json --verbose 2>&1 \
  | python3 "$FORMATTER" >> "$AGENT_LOG"
```

**4. Use `tr` for case conversion instead of parameter expansion.**

Wrong:
```bash
CLASSIFICATION="${raw_result^^}"
```

Right (from `hooks/session-end.sh`, line 269):
```bash
CLASSIFICATION=$(head -1 "$TRIAGE_RESULT_FILE" | awk '{print $1}' | tr -d ':' | tr '[:lower:]' '[:upper:]')
```

**How to verify:**

Run `shellcheck` with explicit bash 3.2 targeting:
```bash
shellcheck --shell=bash hooks/post-merge.sh hooks/session-end.sh hooks/session-start.sh hooks/lib/agent-run.sh
```

For a quick local syntax check under the system bash:
```bash
bash -n hooks/post-merge.sh
bash -n hooks/session-end.sh
```

If you need to test interactively, force the system bash:
```bash
/bin/bash hooks/session-end.sh
```

## See Also

- [[agent-run]] — canonical usage of `$(sh -c 'echo $PPID')` in context
- [[adr-003-bash-3-2-portability]] — architectural decision record explaining why bash 3.2 is the portability floor
