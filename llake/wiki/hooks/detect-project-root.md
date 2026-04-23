---
title: "Detect Project Root"
description: "Three-level strategy to locate the project's llake/config.json from any shell context"
tags: [hooks, shell, project-root, detection]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[constants]]"
  - "[[session-start-hook]]"
  - "[[session-end-hook]]"
  - "[[post-merge-hook]]"
---

## Overview

`hooks/lib/detect-project-root.sh` provides the `detect_project_root` function: a portable, git-free way to locate the directory that contains `llake/config.json` starting from any working directory. Every LoreLake hook needs to know the project root before it can read config, find the wiki, or create runtime state. This library provides a consistent resolution strategy shared by all hooks.

## The Three-Level Strategy

Resolution proceeds in strict priority order, stopping at the first match:

### Level 1 — Environment Override

```sh
if [ -n "${LLAKE_PROJECT_ROOT:-}" ]; then
  echo "$LLAKE_PROJECT_ROOT"
  return 0
fi
```

If `LLAKE_PROJECT_ROOT` is set and non-empty in the environment, it wins unconditionally. This is the escape hatch for:

- **CI / automation** — pipelines that know the project root can inject it directly.
- **Testing** — `tests/hooks/test_detect_project_root.sh` uses this to isolate test projects in `/tmp` without polluting the real filesystem.
- **Multi-project setups** — environments where multiple LoreLake projects exist at different levels of the same directory tree.

### Level 2 — Marker Walk

```sh
local dir=$cwd
while [ -n "$dir" ] && [ "$dir" != "/" ]; do
  if [ -f "$dir/llake/config.json" ]; then
    echo "$dir"
    return 0
  fi
  dir=$(dirname "$dir")
done
```

Starting from the `cwd` argument, the function walks upward one directory at a time, checking whether `llake/config.json` exists at each level. The first directory where this file exists is the project root.

The walk stops at two conditions:
- `$dir` becomes empty (should not happen in practice with `dirname`).
- `$dir` reaches `/` — prevents walking above the filesystem root.

### Level 3 — Caller's git Fallback

If both levels above fail, `detect_project_root` returns exit code 1 without printing anything. The caller may then fall back to:

```sh
git -C "$cwd" rev-parse --show-toplevel
```

This is deliberate: the library itself never calls git (see "Why No Git" below). The caller decides whether a git fallback is appropriate.

## Full Function

```sh
detect_project_root() {
  local cwd=$1

  if [ -n "${LLAKE_PROJECT_ROOT:-}" ]; then
    echo "$LLAKE_PROJECT_ROOT"
    return 0
  fi

  local dir=$cwd
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if [ -f "$dir/llake/config.json" ]; then
      echo "$dir"
      return 0
    fi
    dir=$(dirname "$dir")
  done

  return 1
}
```

## Why No Git in This Library?

The function header is explicit:

> *This file does NOT call git itself — that is the caller's choice (post-merge always uses git; CC hooks always use marker walk). Keeps this lib pure.*

The reason is that different callers have different constraints:

- **`session-start.sh` and `session-end.sh`** (Claude Code hooks) — these run in response to Claude Code session events. The working directory may be any path the user has open, which may not be a git repository at all. Calling `git rev-parse` would produce an error and no useful result. These hooks rely entirely on the marker walk.

- **`post-merge.sh`** (git hook) — this runs inside a git repository by definition. Git is already in context, and `git rev-parse --show-toplevel` reliably returns the repo root. This hook uses git as its fallback.

Keeping git out of the library means it can be used safely from any context without worrying about whether git is available or whether the working directory is inside a repository.

## What Happens When Detection Fails

When all three levels fail (no env override, no `llake/config.json` found walking upward, caller's git fallback also fails or is not attempted), the hook script typically:

1. Logs a warning to stderr.
2. Exits early without spawning an agent.

This is intentional. It is better to silently skip a session than to crash noisily or write runtime state to a wrong location.

## Usage Pattern

```sh
source "$LLAKE_LIB_DIR/detect-project-root.sh"

PROJECT_ROOT=$(detect_project_root "$PWD")
if [ -z "$PROJECT_ROOT" ]; then
  # Optional: try git fallback
  PROJECT_ROOT=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) || true
fi

if [ -z "$PROJECT_ROOT" ]; then
  echo "LoreLake: could not detect project root, skipping." >&2
  exit 0
fi
```

## Test Coverage

`tests/hooks/test_detect_project_root.sh` covers all three scenarios:

1. **Env override wins** — sets `LLAKE_PROJECT_ROOT` to a temp path and calls with a random unrelated `cwd`; verifies the env value is returned regardless.
2. **Marker walk finds config** — creates `$TMP/proj2/llake/config.json`, calls from `$TMP/proj2/src/deep/nested`; verifies `$TMP/proj2` is returned.
3. **No marker returns nonzero** — creates a directory tree with no `llake/config.json`; verifies the function exits with a nonzero status.

## Key Points

- The function takes a `cwd` argument rather than reading `$PWD` internally, keeping it pure and testable.
- The marker is always `llake/config.json` — not just the presence of a `llake/` directory. This prevents false positives from partial installs.
- The walk is intentionally simple: no symlink resolution, no cross-device checks. LoreLake assumes normal filesystem layouts.
- The library exports nothing and sets no global variables — the caller always captures the output with `$(detect_project_root "$cwd")`.

## Code References

- `hooks/lib/detect-project-root.sh:1-30` — full implementation
- `hooks/lib/detect-project-root.sh:12-16` — env override check
- `hooks/lib/detect-project-root.sh:18-28` — marker walk loop
- `tests/hooks/test_detect_project_root.sh:1-43` — all three test scenarios

## See Also

- [[constants]] — defines `LLAKE_DIR_NAME` (`llake`) used in path construction alongside this function's result
- [[session-start-hook]] — uses marker walk only; no git fallback
- [[session-end-hook]] — uses marker walk only; no git fallback
- [[post-merge-hook]] — uses marker walk with git rev-parse as fallback
