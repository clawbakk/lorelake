# Ingest v2 — Worked Plan Examples

Each example below shows the kind of code change being ingested and the
resulting plan.json. Use these as templates for shaping your own plans.

---

## Example 1 — Bug fix that IS a gotcha

**Code change:** A bug fix in `src/payment/charge.py` corrects a rounding
error where amounts were computed in floats and rolled to the nearest cent
incorrectly. The fix switches to `Decimal`. The commit message hints that
this was a recurring class of bug.

**Wiki implication:**
- Update the page documenting `payment/charge.py` to mention the new
  Decimal-based pipeline.
- Create a new gotcha page documenting the float-vs-Decimal trap so future
  contributors don't reintroduce it.

**Plan:**

```json
{
  "version": "1",
  "skip_reason": null,
  "summary": "Switch payment charge to Decimal; document the float rounding gotcha.",
  "updates": [
    {
      "slug": "payment-charge",
      "rationale": "Document the Decimal-based pipeline and link to the gotcha.",
      "ops": [
        {
          "op": "replace",
          "find": "Amounts are computed as Python floats and rolled to cents at the end.",
          "with": "Amounts are computed using `decimal.Decimal` to avoid binary-float rounding errors. See [[gotcha-float-money]]."
        },
        {
          "op": "frontmatter_add_related",
          "items": ["[[gotcha-float-money]]"]
        }
      ]
    }
  ],
  "creates": [
    {
      "slug": "gotcha-float-money",
      "category": "gotchas",
      "front_matter": {
        "title": "Don't use float for money — use Decimal",
        "description": "Binary floats lose precision; sums of cents drift. Use Decimal everywhere money flows.",
        "tags": ["gotchas", "money", "precision"],
        "created": "2026-04-25",
        "updated": "2026-04-25",
        "status": "current",
        "related": ["[[payment-charge]]"]
      },
      "body": "# Don't use float for money — use Decimal\n\n## What goes wrong\n\nPython's `float` is binary IEEE-754. Sums of cent-precision values drift...\n\n## The fix\n\nUse `decimal.Decimal` for all money quantities. See `src/payment/charge.py:42` for the canonical example.\n\n## See also\n\n- [[payment-charge]]\n"
    }
  ],
  "deletes": [],
  "bidirectional_links": [
    { "a": "payment-charge", "b": "gotcha-float-money" }
  ],
  "log_entry": {
    "operation": "ingest",
    "commit_range": "abc1234..def5678",
    "summary": "Switch payment charge to Decimal; document the float rounding gotcha.",
    "pages_affected": ["payment-charge", "gotcha-float-money"]
  }
}
```

---

## Example 2 — Refactor with file rename, no new pages

**Code change:** `src/utils/parse_args.py` was renamed to
`src/cli/argparser.py` and refactored. The exported function names didn't
change, but every wiki `file:line` reference is now wrong.

**Wiki implication:**
- Update every page that references `src/utils/parse_args.py:NN` so the
  reference points to the new path.
- No new pages, no deletes.

**Plan:**

```json
{
  "version": "1",
  "skip_reason": null,
  "summary": "Update file:line references after parse_args.py → cli/argparser.py rename.",
  "updates": [
    {
      "slug": "cli-entry",
      "rationale": "References the renamed file.",
      "ops": [
        {
          "op": "replace",
          "find": "`src/utils/parse_args.py:42`",
          "with": "`src/cli/argparser.py:51`"
        }
      ]
    },
    {
      "slug": "config-loader",
      "rationale": "References the renamed file.",
      "ops": [
        {
          "op": "replace",
          "find": "`src/utils/parse_args.py:88`",
          "with": "`src/cli/argparser.py:97`"
        }
      ]
    }
  ],
  "creates": [],
  "deletes": [],
  "bidirectional_links": [],
  "log_entry": {
    "operation": "ingest",
    "commit_range": "abc1234..def5678",
    "summary": "Update file:line references after parse_args.py → cli/argparser.py rename.",
    "pages_affected": ["cli-entry", "config-loader"]
  }
}
```

---

## Example 3 — Source file deleted, wiki page also deleted

**Code change:** `src/legacy/old_codec.py` was deleted entirely — it was an
abandoned experiment that the team has now removed. The wiki has a page
`old-codec` documenting it.

**Wiki implication:**
- Delete the wiki page.
- The applier will scrub `[[old-codec]]` from every other page's `related:`
  list automatically (cascade).

**Plan:**

```json
{
  "version": "1",
  "skip_reason": null,
  "summary": "Remove old-codec page after the legacy codec was deleted.",
  "updates": [],
  "creates": [],
  "deletes": [
    { "slug": "old-codec", "rationale": "src/legacy/old_codec.py removed; experiment retired." }
  ],
  "bidirectional_links": [],
  "log_entry": {
    "operation": "ingest",
    "commit_range": "abc1234..def5678",
    "summary": "Remove old-codec page after the legacy codec was deleted.",
    "pages_affected": ["old-codec"]
  }
}
```

If the page documents an experiment that's still useful as historical
reference, prefer an `update` that sets `status: deprecated` instead of a
delete.

---

## Example 4 — New ADR with bidirectional links

**Code change:** A commit introduces a new ADR-style decision (recorded in
the source as `docs/adr/0007-rate-limit-strategy.md`) covering rate-limit
strategy. The decision references the existing pages `rate-limiter` and
`auth-flow`.

**Wiki implication:**
- Create the new ADR page.
- Add bidirectional links between the ADR and the two existing pages.

**Plan:**

```json
{
  "version": "1",
  "skip_reason": null,
  "summary": "Add ADR-007 (rate-limit strategy) with bidirectional links to the affected modules.",
  "updates": [
    {
      "slug": "rate-limiter",
      "rationale": "Surface the new ADR in the related list and reference it in the body.",
      "ops": [
        {
          "op": "frontmatter_add_related",
          "items": ["[[adr-007-rate-limit-strategy]]"]
        },
        {
          "op": "append_section",
          "after_heading": "## Decisions",
          "content": "- [[adr-007-rate-limit-strategy]] — chose token bucket over leaky bucket.\n"
        }
      ]
    },
    {
      "slug": "auth-flow",
      "rationale": "Mention the cross-cutting decision.",
      "ops": [
        {
          "op": "frontmatter_add_related",
          "items": ["[[adr-007-rate-limit-strategy]]"]
        }
      ]
    }
  ],
  "creates": [
    {
      "slug": "adr-007-rate-limit-strategy",
      "category": "decisions",
      "front_matter": {
        "title": "ADR-007: Rate Limit Strategy",
        "description": "Chose token bucket over leaky bucket for the API rate limiter.",
        "tags": ["decisions", "rate-limit"],
        "created": "2026-04-25",
        "updated": "2026-04-25",
        "status": "current",
        "related": ["[[rate-limiter]]", "[[auth-flow]]"]
      },
      "body": "# ADR-007: Rate Limit Strategy\n\n## Context\n\n... full ADR body ...\n\n## See also\n\n- [[rate-limiter]]\n- [[auth-flow]]\n"
    }
  ],
  "deletes": [],
  "bidirectional_links": [
    { "a": "adr-007-rate-limit-strategy", "b": "rate-limiter" },
    { "a": "adr-007-rate-limit-strategy", "b": "auth-flow" }
  ],
  "log_entry": {
    "operation": "ingest",
    "commit_range": "abc1234..def5678",
    "summary": "Add ADR-007 (rate-limit strategy) with bidirectional links to the affected modules.",
    "pages_affected": ["adr-007-rate-limit-strategy", "rate-limiter", "auth-flow"]
  }
}
```
