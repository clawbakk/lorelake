---
title: "Configuration Layering"
description: "How user config.json and config.default.json are merged — the dot-key lookup contract"
tags: [config, layering, architecture]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[config-schema]]"
  - "[[read-config]]"
---

# Configuration Layering

## Overview

LoreLake uses a two-layer configuration system. At read time, a dot-key path is looked up first in the project's `llake/config.json`, and only if the key is absent does the lookup fall back to the plugin's `templates/config.default.json`. This design means a project's `config.json` only needs to contain the keys that differ from defaults — it is an override file, not a full configuration declaration.

The `hooks/lib/read-config.py` script implements this lookup. Every hook that needs a config value calls it at runtime; there is no config pre-loading or caching step.

---

## The two layers

| Layer | File location | Owned by | Purpose |
|-------|--------------|----------|---------|
| **User config** | `<project>/llake/config.json` | Project repo | Project-specific overrides; only keys that differ from defaults |
| **Plugin defaults** | `<plugin-repo>/templates/config.default.json` | Plugin repo (this repo) | Authoritative defaults for every supported key |

The plugin defaults file ships with the plugin and is never modified by agents or users in the field. It is the single source of truth for what the valid keys are and what they mean. See [[config-schema]] for the full reference.

---

## How the lookup works

`read-config.py` accepts two positional arguments: the path to the user's `config.json` and a dot-separated key path.

```
python3 hooks/lib/read-config.py <user-config-path> <dot.key.path>
```

The algorithm, step by step:

1. **Load user config.** If the file does not exist or is not valid JSON, an empty dict is used. The script never errors on a missing or malformed user config — it silently falls back.
2. **Traverse the dot-key path in the user config.** For a key like `ingest.timeoutSeconds`, the script splits on `.` and walks the nested dict. If any segment is absent or the node is not a dict, the traversal returns `(None, False)`.
3. **If not found in user config, traverse the defaults.** The same traversal runs against `config.default.json`.
4. **Serialize the result.** The value is printed as a string:
   - Booleans → `"true"` or `"false"` (lowercase, not Python's `True`/`False`)
   - Arrays and objects → JSON-encoded string
   - `null` → empty string `""`
   - Anything else → `str(value)`
5. **If the key is absent from both files,** an empty string is printed and the script exits `0`. It never exits nonzero for a missing key.

The resolution logic in code (`hooks/lib/read-config.py:51-69`):

```python
user = load(user_config_path)       # {} on failure
defaults = load(DEFAULTS_PATH)      # always succeeds if plugin is intact

value, found = get_nested(user, key_path)
if not found:
    value, found = get_nested(defaults, key_path)
if not found:
    print("")
    sys.exit(0)

print(format_value(value))
```

---

## The contract

**`config.default.json` is the single source of truth for defaults.** This is a hard convention that the test suite enforces:

- Hook scripts must never hardcode default values. They must call `read-config.py` and use the returned value.
- User `config.json` files must never redeclare a key with its default value just to be explicit. If a key is omitted from the user config, it will resolve to the default automatically.
- The defaults file must not be duplicated anywhere — not in hook scripts, not in user configs, not in documentation that an agent might copy into a config.

**Why this matters:** If a default is duplicated and then the plugin default changes (e.g., `ingest.timeoutSeconds` is bumped from `1200` to `1800` in a plugin update), any project that explicitly listed the old default in its `config.json` will be silently frozen at the old value. The user will not benefit from the plugin update. Treating the defaults file as the single source of truth ensures that updates propagate to all installs automatically.

---

## Example: minimal user config

Suppose a project has a `develop` branch as its integration branch, a non-standard source layout, and wants to disable session capture. Its `config.json` needs only three overrides:

```json
{
  "sessionCapture": {
    "enabled": false
  },
  "ingest": {
    "branch": "develop",
    "include": ["lib/", "app/"]
  }
}
```

The following table shows what `read-config.py` returns for a selection of keys against this config:

| Dot-key path | User config value | Default | Resolved value |
|---|---|---|---|
| `sessionCapture.enabled` | `false` | `true` | `"false"` |
| `ingest.branch` | `"develop"` | `"main"` | `"develop"` |
| `ingest.include` | `["lib/", "app/"]` | `["src/"]` | `'["lib/", "app/"]'` |
| `ingest.model` | (absent) | `"opus"` | `"opus"` |
| `ingest.maxBudgetUsd` | (absent) | `10.00` | `"10.0"` |
| `sessionCapture.triageModel` | (absent) | `"sonnet"` | `"sonnet"` |
| `transcript.headSize` | (absent) | `10` | `"10"` |
| `logging.maxLines` | (absent) | `1000` | `"1000"` |
| `ingest.notARealKey` | (absent) | (absent) | `""` |

Notice:
- `sessionCapture.enabled` is `false` in user config, so `"false"` is returned (the default `true` is never reached).
- `ingest.model` is absent from user config; the default `"opus"` is used.
- Arrays are returned as JSON strings — the caller must parse them if it needs individual elements.
- An unknown key returns an empty string with exit code `0` — no error.

---

## What happens on a missing user config

If `<project>/llake/config.json` does not exist (e.g., on a fresh clone before bootstrap has run), `load()` catches the `IOError` and returns `{}`. Every subsequent dot-key lookup in the user config returns `(None, False)`, so every key falls through to the defaults. The system is fully operational without a user config file at all.

This is tested end-to-end in `tests/lib/test_read_config.py`:

```python
def test_missing_user_config_falls_back_to_defaults(tmp_path):
    nonexistent = tmp_path / "no-such-file.json"
    rc, out, _ = run([str(nonexistent), "ingest.branch"])
    assert rc == 0
    assert out == "main"
```

---

## What `_comment` and `_schemaVersion` are not

Both `config.default.json` and user `config.json` files may contain `_comment` and `_schemaVersion` keys. These are **metadata annotations** — they exist for human readers and for future tooling, but they are not live config values:

- No hook ever calls `read-config.py` with the key path `_schemaVersion` or `_comment`.
- If you did call `read-config.py <user-config> _comment`, it would return the comment string — but nothing in the system does this.
- Do not add logic to your project config based on `_schemaVersion`; migration tooling (if added in a future schema version) will handle it in the plugin.

---

## Where the script resolves defaults

The script locates `config.default.json` relative to its own position in the plugin repo:

```python
# hooks/lib/read-config.py:19-21
SCRIPT_DIR = Path(__file__).resolve().parent   # → hooks/lib/
PLUGIN_ROOT = SCRIPT_DIR.parent.parent         # → plugin repo root
DEFAULTS_PATH = PLUGIN_ROOT / "templates" / "config.default.json"
```

This means the defaults path is always `<plugin-repo>/templates/config.default.json` regardless of where the calling hook is located or what `$PWD` is at invocation time. The hook does not need to pass a defaults path — it is implicit in the script's installed location.

---

## Key Points

- User `config.json` is an override file. Include only keys that differ from defaults. Omitting a key is equivalent to accepting the default.
- `config.default.json` is the authoritative default registry. Do not duplicate its values elsewhere.
- The lookup is dot-key traversal: `"a.b.c"` → `config["a"]["b"]["c"]`. Intermediate missing keys do not error — they return empty string.
- All return values are strings. Booleans are `"true"`/`"false"`. Arrays/objects are JSON. Callers must parse if needed.
- A missing, empty, or malformed user config file is handled gracefully — the script always falls back to defaults.
- The `tests/lib/test_read_config.py` test suite encodes the full layering contract. If you change the defaults file or the script, run those tests.

---

## Code References

- `hooks/lib/read-config.py:19-21` — `DEFAULTS_PATH` computed relative to script location
- `hooks/lib/read-config.py:24-29` — `load()` silently returns `{}` on any file error
- `hooks/lib/read-config.py:32-38` — `get_nested()` dot-key traversal with `(value, found)` return
- `hooks/lib/read-config.py:59-68` — user-first, then defaults, then empty-string fallback
- `templates/config.default.json` — the full defaults file
- `tests/lib/test_read_config.py:31-35` — `test_user_value_wins`: user key beats default
- `tests/lib/test_read_config.py:37-40` — `test_default_when_user_omits`: missing user key resolves to default
- `tests/lib/test_read_config.py:68-73` — `test_missing_user_config_falls_back_to_defaults`: absent file is safe

---

## See Also

- [[config-schema]] — complete reference for every key in `config.default.json`
- [[read-config]] — detailed documentation of the `read-config.py` script itself
- [[post-merge-hook]] — example of a hook reading config via `read-config.py`
- [[session-end-hook]] — example of a hook reading config via `read-config.py`
