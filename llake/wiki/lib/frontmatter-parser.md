---
title: frontmatter.py
description: "Deliberately minimal YAML frontmatter parser and serializer — no pyyaml, raises rather than guessing"
tags: [lib, frontmatter, yaml, python, wiki]
created: 2026-09-20
updated: 2026-09-20
status: current
related:
  - "[[apply-ingest-plan]]"
  - "[[build-ingest-context]]"
  - "[[page-format]]"
---
# frontmatter.py

## Overview

`hooks/lib/frontmatter.py` reads and writes the YAML frontmatter block at the top of every wiki page. It is pure stdlib — LoreLake has no runtime Python dependencies, and adding `pyyaml` to a plugin that must work in any user's environment is not worth the convenience.

It is used by [[apply-ingest-plan]] on every page round-trip and by [[build-ingest-context]] to build the wiki catalog.

## The supported subset

Exactly four shapes, matching what [[page-format]] specifies:

```yaml
title: Page Title                 # bare scalar
description: "Quoted string"      # quoted scalar
tags: [gotchas, bash, hooks]      # inline scalar list
related:                          # block list
  - "[[page-a]]"
  - "[[page-b]]"
```

Anything richer — nested maps, multi-line scalars, anchors, comments — raises `FrontmatterParseError`.

**The strictness is the feature.** A permissive parser given a structure it does not model would drop it on round-trip, and since the applier's normal path is read-modify-write, that loss would be silent and permanent. Raising instead routes the page to `failed.json` with reason `FrontmatterParseError`, where a human or the fixer can see it. Wiki pages are not supposed to contain richer structures; if one does, that is a page to fix, not a parser to loosen.

## API

| Function | Contract |
|---|---|
| `split(text)` | Returns `(frontmatter_text, body_text)`. No leading `---` delimiter, or no closing one, returns `("", text)` — a page without frontmatter is valid input, not an error. |
| `parse(fm_text)` | Returns a dict. Raises `FrontmatterParseError` on anything outside the subset. |
| `serialize(d)` | Renders back to text **without** the `---` delimiters. Callers add them. |

Insertion order is preserved (dicts are ordered in Python 3.7+), so a page's frontmatter keys do not get shuffled by an unrelated edit — which keeps diffs honest.

## Serialization rules

`serialize` is not the inverse of `parse` byte-for-byte; it normalizes. Two rules govern the output:

- **List rendering.** A list goes inline (`tags: [a, b, c]`) only if it is non-empty, has at most 5 items, and every item is a bare word that does not start with `[[`. Otherwise it becomes a block list with each item double-quoted. The `[[` exclusion exists because `related:` entries would otherwise render as `related: [[[a]], [[b]]]`, which is both unreadable and ambiguous YAML.
- **Scalar quoting.** A string is left bare only if it matches `^[a-z0-9][a-z0-9._-]*$`; everything else — anything with spaces, capitals, colons, or an empty value — is double-quoted. `title` and `description` therefore always end up quoted, and `status: current` stays bare.

The practical consequence: the first time the applier touches a hand-written page, its frontmatter may be reformatted even where the values did not change. That is a one-time normalization toward a single canonical form, not churn.

## Round-trip caution

`parse` strips surrounding quotes from values, and `serialize` re-adds them per the rules above. A value whose quoting was semantically meaningful in YAML (`"true"` as a string versus `true` as a boolean) is preserved by `_needs_quotes` special-casing, but the general principle holds: this module round-trips *the subset it models*, faithfully, and refuses everything else.

## Key Points

- Pure stdlib by design; LoreLake ships no runtime Python dependencies.
- Supports exactly four shapes: bare scalar, quoted scalar, inline list, block list.
- Raises `FrontmatterParseError` rather than silently dropping structures it cannot model.
- A page with no frontmatter is valid input and returns `("", text)`.
- `serialize` omits the `---` delimiters — callers wrap the output themselves.
- Lists of `[[wikilinks]]` always render as block lists, never inline.
- Key insertion order is preserved, so edits produce minimal diffs.
- First contact with a hand-written page may normalize its frontmatter formatting once.

## Code References

- `hooks/lib/frontmatter.py:1-15` — module docstring stating the supported subset and the rationale
- `hooks/lib/frontmatter.py:21-22` — `FrontmatterParseError`
- `hooks/lib/frontmatter.py:25-36` — `split`
- `hooks/lib/frontmatter.py:59-97` — `parse`, including block-list handling
- `hooks/lib/frontmatter.py:109-118` — `_needs_quotes` and the bare-scalar regex
- `hooks/lib/frontmatter.py:121-145` — `serialize` and the inline-vs-block list rule
- `hooks/lib/apply_ingest_plan.py:156-165` — a representative read-modify-write round trip
- `tests/lib/test_frontmatter.py` — unit tests

## See Also

- [[page-format]] — the frontmatter contract this implements
- [[apply-ingest-plan]] — heaviest consumer; maps parse errors into `failed.json`
- [[build-ingest-context]] — uses it to build `wiki-index.json`, tolerating parse failures
