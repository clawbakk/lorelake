---
title: "Shell Constants"
description: "Shared shell constants used across LoreLake hooks"
tags: [hooks, shell, constants, configuration]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[detect-project-root]]"
  - "[[session-end-hook]]"
  - "[[post-merge-hook]]"
  - "[[session-start-hook]]"
---

## Overview

`hooks/lib/constants.sh` defines the fixed structural names that every LoreLake hook uses to locate the plugin's runtime directories inside a project. These values are universal across all installs — they must not vary per project and must not be user-configurable. Centralizing them here ensures that if the directory naming convention ever changes, a single file is the only place to update it.

## Constants

| Variable | Value | Purpose |
|----------|-------|---------|
| `LLAKE_DIR_NAME` | `llake` | Name of the top-level LoreLake directory inside a project (e.g., `<project>/llake/`) |
| `WIKI_DIR_NAME` | `wiki` | Name of the wiki subdirectory inside the LoreLake dir (e.g., `<project>/llake/wiki/`) |

Both variables are exported with `export`, making them available to any subprocess spawned by a sourcing hook script.

## Full Source

```sh
# LoreLake plugin — fixed structural constants.
# Sourced by every hook script. These names are universal across all installs;
# they MUST NOT vary per project.

export LLAKE_DIR_NAME="llake"
export WIKI_DIR_NAME="wiki"
```

## Why Centralize These?

Hook scripts construct paths like `"$PROJECT_ROOT/$LLAKE_DIR_NAME/$WIKI_DIR_NAME"` rather than hardcoding `"$PROJECT_ROOT/llake/wiki"`. This has two benefits:

1. **Single point of change** — if the directory names ever need to change (e.g., for a major version migration), only `constants.sh` needs editing, not every hook script.
2. **Explicit contract** — the constants file serves as documentation that these names are intentionally fixed, not coincidentally consistent. A developer reading a hook script who sees `$LLAKE_DIR_NAME` knows to look here for the authoritative value rather than assuming it is a local variable.

## Sourcing Pattern

Every hook script sources this file early in its execution:

```sh
LLAKE_LIB_DIR="$(dirname "$0")/lib"
source "$LLAKE_LIB_DIR/constants.sh"
```

After sourcing, `$LLAKE_DIR_NAME` and `$WIKI_DIR_NAME` are available for path construction throughout the script.

## What Must NOT Change

The values `llake` and `wiki` are not arbitrary choices — they match the directory structure documented throughout the schema and created by the install skill. Any agent or script writing files into a project uses these exact names. Changing them without a corresponding migration would break every existing LoreLake install.

## Key Points

- Only two constants are defined here; they cover all directory naming needed by hook scripts.
- Both are `export`-ed so child processes (e.g., inline scripts) inherit them without re-sourcing.
- Tests in `tests/hooks/test_constants.sh` verify the exact string values, catching accidental edits.
- Do not add user-configurable values here. User config belongs in `config.json` and is read via `hooks/lib/read-config.py`. Constants here are immutable-to-users.

## Code References

- `hooks/lib/constants.sh:1-7` — full implementation
- `tests/hooks/test_constants.sh:1-20` — value regression tests

## See Also

- [[detect-project-root]] — uses `$LLAKE_DIR_NAME` to identify project roots during the marker walk
- [[session-start-hook]] — sources constants to build the `llake/index.md` path
- [[session-end-hook]] — sources constants to locate wiki and state directories
- [[post-merge-hook]] — sources constants to locate wiki and state directories
