---
title: "read-config.py"
description: "Dot-key config lookup with user config + defaults layering"
tags: [lib, config, shell-interface]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[config-layering]]"
  - "[[config-schema]]"
---

## Overview

`hooks/lib/read-config.py` is the single entry point all hook shell scripts use to read configuration values. It accepts a dot-key path (e.g. `sessionCapture.enabled`) and returns the value as a shell-printable string. It checks the project's `config.json` first; if the key is absent it falls back to the plugin's `templates/config.default.json`. This layered lookup is the whole point — shell scripts never have to know whether a user has overridden a value.

## CLI Interface

```
python3 hooks/lib/read-config.py <user-config-path> <dot.key.path>
```

| Argument | Description |
|---|---|
| `<user-config-path>` | Absolute path to the project's `llake/config.json`. May not exist — missing file is treated as empty config. |
| `<dot.key.path>` | Period-separated key path into the JSON tree (e.g. `ingest.branch`, `transcript.headSize`). |

**Output** (stdout, always one line):

| Value type | Output |
|---|---|
| String | Value as-is |
| Number | Value as-is (e.g. `1200`) |
| Boolean | `true` or `false` |
| `null` | Empty string |
| Array or object | JSON-encoded string |
| Key not found anywhere | Empty string |

**Exit codes**: always `0`. An unknown key returns empty string and exits 0 — the script never exits nonzero in normal use. (If called with fewer than 2 args it also exits 0 with empty output.)

## Dot-Key Syntax

The key path mirrors the JSON structure of the config files. Each segment is separated by a period:

```
ingest.branch           → config["ingest"]["branch"]
sessionCapture.enabled  → config["sessionCapture"]["enabled"]
transcript.headSize     → config["transcript"]["headSize"]
ingest.allowedTools     → config["ingest"]["allowedTools"]   (emits JSON array)
```

Intermediate missing keys short-circuit to "not found" cleanly — no exceptions.

## Lookup Priority

1. **User config** (`llake/config.json`) — if the key exists here, return it immediately.
2. **Plugin defaults** (`templates/config.default.json`) — consulted only when the user config is missing, unreadable, or does not contain the key.

If the user config file does not exist or is not valid JSON, `load()` returns `{}` silently and the lookup falls through to defaults. This means defaults always serve as a safe floor.

## Example Invocations

These match the test suite in `tests/lib/test_read_config.py`:

```bash
# User has set ingest.branch = "develop" — user value wins
python3 hooks/lib/read-config.py llake/config.json ingest.branch
# → develop

# User omitted ingest.timeoutSeconds — falls back to default
python3 hooks/lib/read-config.py llake/config.json ingest.timeoutSeconds
# → 1200

# Boolean — emits true/false string
python3 hooks/lib/read-config.py llake/config.json ingest.enabled
# → true

# Array — emits JSON
python3 hooks/lib/read-config.py llake/config.json ingest.allowedTools
# → ["Read","Write","Edit","Glob","Grep","Bash"]

# Unknown key — empty string, exit 0
python3 hooks/lib/read-config.py llake/config.json ingest.notARealKey
# → (empty)

# Non-existent user config — falls back to defaults
python3 hooks/lib/read-config.py /no/such/file.json ingest.branch
# → main
```

## How Shell Hooks Use It

Every hook that needs a config value calls `read-config.py` directly via command substitution. The helper is wired in as a one-liner:

```bash
BUDGET=$(python3 "$SCRIPT_DIR/lib/read-config.py" "$CONFIG" "ingest.maxBudgetUsd")
MODEL=$(python3 "$SCRIPT_DIR/lib/read-config.py" "$CONFIG" "ingest.model")
TIMEOUT=$(python3 "$SCRIPT_DIR/lib/read-config.py" "$CONFIG" "ingest.timeoutSeconds")
ENABLED=$(python3 "$SCRIPT_DIR/lib/read-config.py" "$CONFIG" "ingest.enabled")
```

Values used this way include: agent model, max budget (USD), timeout (seconds), enabled flag, branch name, allowed tools list, transcript sampling parameters (`headSize`, `tailSize`, `middleMaxSize`), and the `minTurns`/`minWords` thin-session thresholds.

## Defaults Path Resolution

The script locates defaults automatically — it walks up two directories from its own location (`__file__`) to reach the plugin root, then appends `templates/config.default.json`. This means the defaults path is always the plugin repo's copy, regardless of where the caller's current directory is.

## Key Points

- Never exits nonzero; unknown keys return empty string.
- Boolean values are always the strings `true` or `false` (not Python `True`/`False`), so shell `[[ "$val" == "true" ]]` works directly.
- Arrays and objects are JSON-encoded; shell callers that need them pass the value to `python3 -c` or `jq` for further parsing.
- Missing or invalid user config is silently treated as `{}` — defaults always cover the gap.
- The defaults file (`templates/config.default.json`) is the canonical source of truth for all default values; do not duplicate defaults in hook scripts.

## Code References

| Symbol | Location |
|---|---|
| `load(path)` | `hooks/lib/read-config.py:24-29` |
| `get_nested(node, key_path)` | `hooks/lib/read-config.py:32-38` |
| `format_value(value)` | `hooks/lib/read-config.py:41-48` |
| `main()` | `hooks/lib/read-config.py:51-73` |
| Default values source | `templates/config.default.json` |
| Test suite | `tests/lib/test_read_config.py` |

## See Also

- [[config-layering]] — full explanation of the two-file config system and all available keys
- [[config-schema]] — schema reference for both `config.json` and `config.default.json`
