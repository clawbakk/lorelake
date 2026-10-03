---
title: "v3 agents escape kill_tree once the Python run is gone"
description: "v3 agents start in their own session; once the Python run dies, kill_tree cannot reach them — Python must kill its agents on every exit path"
tags: [gotchas, process-management, agents, ingest, v3]
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-orchestrator]]"
  - "[[ingest-v3-writers]]"
  - "[[agent-run]]"
---
# v3 agents escape kill_tree once the Python run is gone

## What goes wrong

Ingest v3 starts every `claude -p` agent with `start_new_session=True`. Each agent and its children then form their own process group, which `os.killpg` can stop as a unit without touching the run. The cost is that bash's `kill_tree` ([[agent-run]]) finds descendants by walking parent PIDs from the run's subshell. Once the Python process that spawned the agents is dead, they are reparented away and **the tree kill cannot find them**.

Before the fix, two paths left orphaned writer agents still editing wiki pages after the run had reverted:

- **A kill.** The bash trap's tree kill reached the children before Python could act. Python ran no signal handler of its own.
- **An exception in the pool.** Python unwound with agents still running and no cleanup.

## The fix

- **Pool cleanup.** On any exception, `dispatch._abort` kills every running agent's process group with a 2 s grace, reverts each writer/fixer bundle from its snapshot and records its cost, then re-raises.
- **Live registry.** `agent.py` keeps every started, unfinished agent; `kill_live()` stops them all. A kill signal that lands between fork and registration is deferred until the agent is registered (`defer_signal`).
- **Signal handlers.** `run.py` turns `SIGTERM`, `SIGHUP` and `SIGINT` into `Killed`, a `BaseException`, so no `except Exception` swallows it. `_on_killed` calls `kill_live`, reverts the run and exits 1 with the cursor held.
- **Kill order in bash.** The trap in `ingest-v3.sh` sends `SIGTERM` to Python first and waits up to `LLAKE_V3_KILL_GRACE_SECONDS` (20) before the tree kill and its own `revert-run`.

## How to apply

Code that spawns agents in their own session must register each one and kill it on **every** exit path: normal completion, exceptions and signals. Never rely on an outer tree kill. In bash, signal the spawning process first and give it time before killing the tree.

## Key Points

- `start_new_session=True` lets one agent be killed cleanly, and hides all agents from the outer tree kill once their parent is gone.
- v3 keeps a live-agent registry, kills agents from the pool's exception path and from a Python signal handler, and makes bash wait for Python before the tree kill.
- `Killed` is a `BaseException` on purpose.

## Code References

- `hooks/lib/ingest_v3/agent.py:31` — `_LIVE` registry and spawn-deferral state
- `hooks/lib/ingest_v3/agent.py:177` — `Popen(..., start_new_session=True, ...)`
- `hooks/lib/ingest_v3/dispatch.py:98` — `_abort`
- `hooks/lib/ingest_v3/run.py:62` — `_install_kill_handlers`
- `hooks/lib/ingest-v3.sh:69` — `_ingest_v3_on_kill`
- `tests/lib/test_v3_run_e2e.py` — orphan tests with the stub's `V3_STUB_STAY` / `V3_STUB_PIDFILE`

## See Also

- [[ingest-v3-orchestrator]] — the full kill path
- [[ingest-v3-writers]] — the pool and `Agent`
- [[agent-run]] — `kill_tree`
