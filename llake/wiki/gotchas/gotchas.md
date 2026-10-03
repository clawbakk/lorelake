---
title: "Gotchas"
description: "Category index for gotchas."
tags: [gotchas]
created: 2026-04-22
updated: 2026-09-22
---

# Gotchas

Known pitfalls, quirks, and easy-to-miss constraints in the LoreLake codebase.

| Page | Description |
|---|---|
| [[bash-3-2-portability]] | macOS ships bash 3.2 — forbidden features and safe replacements for hook shell code |
| [[render-prompt-strict-exit]] | Unresolved {{VAR}} in a template causes nonzero exit — must wire both template and hook caller together |
| [[is-llake-agent-guard]] | All hooks bail early when IS_LLAKE_AGENT=true — prevents infinite capture recursion from background agents |
| [[session-preamble-snapshot-timing]] | The bootstrap session's own SessionStart already fired before the wiki existed, so it never sees what it just built |
| [[watchdog-sleep-orphans]] | `kill $WATCHDOG_PID` kills the watchdog subshell but orphans its `sleep`; reap watchdogs with `kill_tree` |
| [[append-only-merge-conflicts]] | `log.md` and the fixed-category indexes conflict on every parallel merge unless `llake/.gitattributes` marks them `merge=union`. Never add wiki pages to the rules |
