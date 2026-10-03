---
title: "Text pipes use the locale codec unless you pass an encoding"
description: "subprocess text pipes encode with the locale codec; under a C/ASCII locale a non-ASCII prompt raises UnicodeEncodeError — always pass encoding='utf-8'"
tags: [gotchas, python, encoding, subprocess, agents]
created: 2026-10-03
updated: 2026-10-03
status: current
related:
  - "[[ingest-v3-writers]]"
---
# Text pipes use the locale codec unless you pass an encoding

## What goes wrong

`subprocess.Popen(..., universal_newlines=True)` (or `text=True`) without `encoding=` encodes stdin and decodes stdout with the locale's preferred encoding. Hooks can run under cron, minimal shells and other environments with a `C`/POSIX locale, where that codec is ASCII. The ingest v3 prompt templates contain non-ASCII text such as em dashes and arrows. Writing such a prompt to the agent's stdin raised `UnicodeEncodeError` **at spawn**, so the agent never started.

## The fix

`Agent.start` in `hooks/lib/ingest_v3/agent.py` passes `encoding="utf-8", errors="replace"` to `Popen`, so prompts are always UTF-8. Every other v3 text path is explicit too: `common.git` runs git with `encoding="utf-8"`, and every file read and write in `ingest_v3` passes `encoding="utf-8"`. `names.tree_text` feeds `git cat-file --batch` bytes it encodes itself.

## How to apply

In any plugin Python that talks to a subprocess or a file in text mode, pass `encoding="utf-8"`, with `errors="replace"` where lossy output is acceptable. Do not depend on the user's locale.

## Key Points

- Text mode without `encoding=` means the locale codec, which is ASCII under `C`/POSIX.
- A non-ASCII prompt then fails before the agent starts.
- v3 forces UTF-8 on every pipe and file.

## Code References

- `hooks/lib/ingest_v3/agent.py:177` — `Popen(..., encoding="utf-8", errors="replace")`
- `hooks/lib/ingest_v3/common.py` — `git` helper with `encoding="utf-8"`
- `tests/lib/test_v3_agent.py` — non-ASCII prompt under an ASCII locale

## See Also

- [[ingest-v3-writers]] — the spawn helper
