# Ingest v2 Plan Format

The planner emits a single JSON document. This file specifies its shape and
op semantics.

## Top-level shape

```json
{
  "version": "1",
  "skip_reason": null,
  "summary": "Short human-readable summary of the changes proposed.",
  "updates": [
    {
      "slug": "page-slug",
      "rationale": "Why this page needs to change.",
      "ops": [ /* op objects, see below */ ]
    }
  ],
  "creates": [
    {
      "slug": "new-page-slug",
      "category": "decisions",
      "front_matter": {
        "title": "Page Title",
        "description": "One-line summary",
        "tags": ["decisions", "topic"],
        "created": "YYYY-MM-DD",
        "updated": "YYYY-MM-DD",
        "status": "current",
        "related": ["[[other-page]]"]
      },
      "body": "# Title\n\n... full markdown body ...\n"
    }
  ],
  "deletes": [
    { "slug": "removed-page-slug", "rationale": "Why this page is obsolete." }
  ],
  "bidirectional_links": [
    { "a": "page-a", "b": "page-b" }
  ],
  "log_entry": {
    "operation": "ingest",
    "commit_range": "abc1234..def5678",
    "summary": "Same summary as the top-level summary, repeated here.",
    "pages_affected": ["page-slug", "new-page-slug", "removed-page-slug"]
  }
}
```

`log_entry.pages_affected` MUST equal the union of all slugs in `updates`,
`creates`, and `deletes`. Mismatches are rejected by the schema validator.

## The five op types

### `replace` — surgical text edit

```json
{ "op": "replace", "find": "exact text in original", "with": "replacement text" }
```

- The `find` string must occur **exactly once** in the original page body.
- Multiple `replace` ops on the same page must NOT share any bytes in the
  original (no overlap).
- The applier resolves all anchors against the ORIGINAL content (not the
  running result), then applies in reverse-position order so offsets don't
  shift.

### `append_section` — add content to the end of a heading's section

```json
{ "op": "append_section", "after_heading": "## Section Name", "content": "New text.\n" }
```

- `after_heading` must be the **exact heading line** (including the `##`
  prefix). It must occur exactly once in the page.
- Content lands at the end of that section — i.e., right before the next
  heading of the same or higher level (or at EOF).

### `body_replace` — replace the entire body (escape hatch)

```json
{ "op": "body_replace", "content": "# New body\n\nFull replacement.\n" }
```

- Frontmatter is preserved automatically. Only the body is replaced.
- `body_replace` is mutually exclusive with `replace` in the same update.
- Use sparingly: prefer surgical edits unless the page is being substantially
  restructured.

### `frontmatter_set` — set/replace a frontmatter field

```json
{ "op": "frontmatter_set", "key": "description", "value": "New description." }
```

- Replaces the value (or adds the key if missing).

### `frontmatter_add_related` — add to the related list (idempotent)

```json
{ "op": "frontmatter_add_related", "items": ["[[adr-005]]", "[[runtime]]"] }
```

- Already-present items are skipped (idempotent).
- Use this paired with `bidirectional_links[]` to ensure both sides see each
  other.

## Anchor uniqueness rule

Every `find` string in `replace` ops must occur **exactly once** in the
original file. Extend the find string with surrounding context until it is
unique. If you cannot make a find unique without absurd amounts of context,
fall back to `body_replace`.

### Counterexample — the apple problem

```
Original: "I want to eat the apple that grows on that tree"
Op A:     replace "I want to eat the apple"           → "I want to eat the orange"
Op B:     replace "the apple that grows on that tree" → "the apple that grows on that hill"
```

These two anchors share the bytes `the apple` in the original. The applier
rejects this as `EditOverlap`. Solutions:

- Merge into one `replace` with a longer combined anchor and combined
  replacement.
- Use `body_replace` if the change is large enough that surgical edits are
  unwieldy.

## Deletes are real deletes

Emit `deletes[]` only when the wiki page no longer documents anything that
exists. **A source file disappearing does not automatically mean the wiki page
should disappear** — sometimes the page documented a now-removed experiment
that's still useful as historical reference. When in doubt, update the page
(e.g., update its frontmatter `status` to `deprecated`) rather than delete it.

When a delete IS appropriate, the applier:
- Removes the page file.
- Scrubs `[[<slug>]]` from every other page's frontmatter `related:` list.
- Surfaces inline-body `[[<slug>]]` mentions as warnings in `applied.json`
  (but does not auto-rewrite them).

## Bidirectional links

For every new `[[wikilink]]` pointing from page A to page B, include a
matching entry in `bidirectional_links[]` so the applier reconciles the
reverse direction. The applier:
- Adds `[[B]]` to A's `related:` (idempotent).
- Adds `[[A]]` to B's `related:` (idempotent).
- Silently skips a pair if either side is in `deletes[]`.

## Skip path

If the changes have no impact worth recording in the wiki (e.g., only
formatting / whitespace, only test fixtures with no behavior implication),
emit a plan with `skip_reason` set to a short string and empty arrays for
updates / creates / deletes / bidirectional_links. The applier will write a
single skip entry to `log.md` so the operator sees that the planner ran and
chose to skip.

Do NOT skip merely because the change is small — small fixes often produce
the most valuable gotchas pages. Skip only when no reader of the wiki would
benefit from any update.

## See examples

Worked examples of plans for common scenarios live in the embedded examples
section that follows.
