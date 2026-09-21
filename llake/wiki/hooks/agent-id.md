---
title: "Agent ID Generator"
description: "Generates human-readable <adj>-<noun>-HHMMSS-<rand4> agent IDs for log lines and working dirs"
tags: [hooks, shell, agents, logging]
created: 2026-04-23
updated: 2026-09-20
status: current
related:
  - "[[agent-run]]"
  - "[[session-end-hook]]"
  - "[[post-merge-hook]]"
---

## Overview

`hooks/lib/agent-id.sh` is a small shell library that generates human-readable identifiers for background agents spawned by LoreLake hooks. Every agent gets a unique ID of the form `<adjective>-<noun>-HHMMSS-<rand4>` (e.g., `swift-owl-143022-9c1e`). These IDs appear in log lines, agent working directory names, and the hooks audit log, making it easy to correlate entries across files without decoding opaque UUIDs.

## How It Works

The library defines two indexed arrays of words and one function:

```sh
LLAKE_AGENT_ADJECTIVES=(swift brave calm bold keen wise warm cool wild free
                         bright quiet gentle lazy happy dancing flying running
                         sleeping jumping)
LLAKE_AGENT_NOUNS=(fox owl bear wolf hawk deer hare crow lion whale tiger eagle
                   panda otter raven bunny falcon pirate knight wizard)

generate_agent_id() {
  local adj_idx=$((RANDOM % ${#LLAKE_AGENT_ADJECTIVES[@]}))
  local noun_idx=$((RANDOM % ${#LLAKE_AGENT_NOUNS[@]}))
  local suffix
  suffix=$(printf '%04x' $((RANDOM % 65536)))
  echo "${LLAKE_AGENT_ADJECTIVES[$adj_idx]}-${LLAKE_AGENT_NOUNS[$noun_idx]}-$(date +%H%M%S)-${suffix}"
}
```

`RANDOM` is a built-in bash variable that returns a pseudo-random integer on each read. The modulo operation maps it to a valid array index. The six-digit time component (`date +%H%M%S`) makes the ID's creation time immediately readable. The trailing four hex characters are a second `$RANDOM` draw and exist purely for collision avoidance: `HHMMSS` only distinguishes agents started in different seconds, and the word pair alone collides once in 400. Two agents starting in the same second now collide with probability 1/65536, which is adequate for a single project's spawn rate. The suffix is not cryptographic and must not be treated as one.

### Example IDs

```
swift-owl-143022-9c1e
brave-falcon-091537-04b7
dancing-panda-235801-f2a0
```

### Why not UUIDs?

UUIDs such as `3f2504e0-4f89-11d3-9a0c-0305e82c3301` are guaranteed unique but carry no information. A human-readable ID lets an operator scanning `hooks.log` or the `.state/agents/` directory immediately see:

- **Which agent** is being described (same ID appears in agent log, pid file, and hooks.log).
- **When it started** (the HHMMSS suffix).
- **Whether two log entries are the same agent** without cross-referencing a UUID registry.

## Where Agent IDs Are Used

Once `generate_agent_id` is called in a hook script, the resulting value is stored in `LLAKE_AGENT_ID` and used in two places:

1. **Agent working directory** — the directory `.state/agents/<id>/` is created for each agent run. It holds `agent.log` and phase-specific `.pid` files.
2. **`hooks.log` entries** — every significant lifecycle event (start, timeout, user-kill, completion) logged to `.state/hooks.log` includes the agent ID so all events for one agent run can be grepped together.

## Sourcing the Library

The file is sourced (not executed) by hook scripts:

```sh
source "$LLAKE_LIB_DIR/agent-id.sh"
LLAKE_AGENT_ID=$(generate_agent_id)
```

After that, `LLAKE_AGENT_ID` is available for directory creation and log formatting.

## Bash 3.2 Portability

The library uses only features available in macOS's bundled `/bin/bash` (version 3.2):

- Indexed arrays (not associative arrays, which require bash 4+).
- `$RANDOM` built-in.
- `$(...)` command substitution.
- Standard `date` invocation.

See [[bash-3-2-portability]] for the full constraint list.

## Key Points

- IDs follow the pattern `^[a-z]+-[a-z]+-[0-9]{6}-[0-9a-f]{4}$` — the four-hex suffix is **required**, and `tests/hooks/test_agent_id.sh` pins it.
- Two consecutive calls will almost always differ: `$RANDOM` provides word-choice variance, and the HHMMSS suffix changes every second.
- The vocabulary pools (20 adjectives, 20 nouns) yield 400 combinations, multiplied by 65536 suffixes per second.
- Sub-agents derive their IDs by suffixing the parent's, e.g. `<id>_planner`, `<id>_fixer`, `<id>_triage`, `<id>_capture` — so a single grep over `hooks.log` collects every phase of one run.
- The file must be *sourced*, not executed. It defines no shebang line and exports nothing; callers own the `LLAKE_AGENT_ID` variable.

## Code References

- `hooks/lib/agent-id.sh:8-17` — `generate_agent_id`, including the suffix derivation
- `hooks/lib/agent-id.sh:5-6` — the adjective and noun pools
- `tests/hooks/test_agent_id.sh` — format regex and `test_id_suffix_unique_in_same_second`

## See Also

- [[agent-run]] — uses `LLAKE_AGENT_ID` in hooks.log entries written by `_agent_cleanup`
- [[session-end-hook]] — calls `generate_agent_id` at startup
- [[post-merge-hook]] — calls `generate_agent_id` at startup
- [[bash-3-2-portability]] — portability constraints for all hook shell code
