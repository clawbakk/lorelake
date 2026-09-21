---
title: build_ingest_context.py
description: "Stage 1 of ingest v2 — turns a commit range into changes.json, per-file chunked diffs, and a wiki page catalog"
tags: [lib, ingest, git, context, python]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[ingest-v2-pipeline]]"
  - "[[frontmatter-parser]]"
  - "[[ingest-v2-orchestrator]]"
---
# build_ingest_context.py

## Overview

`hooks/lib/build_ingest_context.py` is Stage 1 of [[ingest-v2-pipeline]]. It reads a git commit range, applies `ingest.include` filtering, and writes a self-contained context directory that the planner agent consumes with nothing but `Read` and `Glob`.

Its purpose is to make the planner's read-only tool allowlist workable. Every question the planner would otherwise answer by shelling out to `git` — what changed, in which commit, with what message, against what existing wiki pages — is pre-answered as a file. Removing `Bash` from an agent's allowlist is only safe if you first remove its reasons to want it.

## Invocation

```
python3 build_ingest_context.py \
  --project-root PATH --wiki-root PATH \
  --last-sha SHA --current-sha SHA \
  [--include PATH]... \
  --out-dir PATH --diff-chunk-bytes N
```

`--include` repeats once per path and is forwarded to git as a pathspec on every command, so filtering is consistent between the commit list, the per-commit file lists, and the diffs. Exit is nonzero on any git or IO error, which the orchestrator reports as `failed: stage1`.

## Output

```
<out-dir>/
  changes.json
  diffs/
    hooks__lib__ingest-v2.sh.patch
    hooks__post-merge.sh.001.patch
    hooks__post-merge.sh.002.patch
    hooks__post-merge.sh.index.json
  wiki-index.json
```

### `changes.json`

```json
{
  "range": "<last-sha>..<current-sha>",
  "commits": [
    {"sha": "...", "short": "...", "author": "...", "date": "...",
     "subject": "...", "body": "...",
     "files": [{"path": "...", "status": "M"}]}
  ],
  "files_touched": ["..."]
}
```

Commits are ordered **oldest first** (`git log --reverse`), so the planner reads the range as a narrative rather than backwards. Full commit bodies are included: a commit message is often the only place the *why* of a change is written down, and it is the single highest-value input the planner gets. `status` is the first character of git's `--name-status` code (`A`, `M`, `D`, `R`).

The multi-line body is extracted using a `---END---` sentinel in the `git show` format string, which is what makes bodies containing blank lines parse correctly.

### `diffs/`

One unified diff per file in `files_touched`. Path separators become `__`, so `hooks/lib/x.sh` writes `hooks__lib__x.sh.patch`.

A diff larger than `--diff-chunk-bytes` (default 2000, from `ingest.v2.diffChunkBytes`) is split **on hunk boundaries** into `.001.patch`, `.002.patch`, and so on, each carrying a copy of the file header so every chunk is independently readable. A sidecar `<safe-name>.index.json` lists the chunk names in order.

Splitting on hunks rather than bytes is the whole point: a chunk that ends mid-hunk is a corrupt diff, and a planner reading it would draw conclusions from half a change. If a file has no hunks at all — a pure rename or mode change — it is emitted as a single patch regardless of size.

### `wiki-index.json`

A flat `slug → {path, category, title, description, tags, related, updated}` catalog of every page under `wiki/`, parsed with [[frontmatter-parser]]. `category` is the first path component below `wiki/`.

This is what lets the planner decide between `updates[]` and `creates[]` without reading 60 files. Parse failures are non-fatal: a page with broken frontmatter is catalogued with empty fields rather than dropped, so it remains visible as a target. A missing `wiki/` directory produces `{}` — the planner then proposes creates for everything, which is the correct behaviour on a fresh install.

Note that unlike the applier, this walker does **not** exclude `wiki/discussions/`. The planner may need to know a discussion page exists in order to link to it; it simply may not write there.

## Key Points

- Exists so the planner needs no `Bash` — every git question is pre-answered as a file.
- Commits are oldest-first and include full bodies, which usually carry the *why* of a change.
- `--include` is applied as a git pathspec to the commit list, the file lists, and the diffs alike.
- Large diffs split on hunk boundaries, never mid-hunk; each chunk repeats the file header.
- `<safe-name>.index.json` is the only reliable way to know a file's diff was chunked.
- `wiki-index.json` tolerates unparseable frontmatter rather than dropping the page.
- A missing `wiki/` yields `{}`, which is what a fresh install should look like to the planner.

## Code References

- `hooks/lib/build_ingest_context.py:19-24` — `git` subprocess wrapper; raises on nonzero
- `hooks/lib/build_ingest_context.py:27-31` — `list_commits`, oldest-first with pathspec
- `hooks/lib/build_ingest_context.py:34-56` — `commit_metadata` and the `---END---` body sentinel
- `hooks/lib/build_ingest_context.py:66-100` — `write_wiki_index`
- `hooks/lib/build_ingest_context.py:107-133` — `_split_unified_diff_into_hunks`
- `hooks/lib/build_ingest_context.py:136-168` — `write_per_file_diffs` and chunk-index emission
- `hooks/lib/build_ingest_context.py:171-201` — CLI `main`
- `hooks/lib/ingest-v2.sh:78-94` — the caller
- `tests/lib/test_build_ingest_context.py` — unit tests for all three outputs

## See Also

- [[ingest-v2-pipeline]] — the pipeline this feeds
- [[ingest-v2-orchestrator]] — the caller, and what it does when this stage fails
- [[frontmatter-parser]] — used to build `wiki-index.json`
- [[config-schema]] — `ingest.v2.diffChunkBytes` and `ingest.include`
