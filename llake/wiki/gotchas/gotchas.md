---
title: "Gotchas"
description: "Category index for gotchas."
tags: [gotchas]
created: 2026-04-22
updated: 2026-09-16
---

# Gotchas

Known pitfalls, quirks, and easy-to-miss constraints in the LoreLake codebase.

| Page | Description |
|---|---|
| [[bash-3-2-portability]] | macOS ships bash 3.2 — forbidden features and safe replacements for hook shell code |
| [[render-prompt-strict-exit]] | Unresolved {{VAR}} in a template causes nonzero exit — must wire both template and hook caller together |
| [[is-llake-agent-guard]] | All hooks bail early when IS_LLAKE_AGENT=true — prevents infinite capture recursion from background agents |
| [[session-preamble-snapshot-timing]] | The bootstrap session's own SessionStart already fired before the wiki existed, so it never sees what it just built |
