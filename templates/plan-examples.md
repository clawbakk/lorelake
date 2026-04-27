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

---

## Example 5 — Multi-commit range with one skipped commit

**Code change:** Three commits land together:

1. `a1b2c3d` — adds a new `RetryPolicy` class in `src/infra/retry.py` with
   configurable exponential backoff. Adds unit tests.
2. `e4f5a6b` — renames `src/infra/http_helpers.py` →
   `src/infra/http_client.py` as part of a planned import-path cleanup
   (Task 7 of the v2 fixes plan). No new behaviour, no call-site changes.
3. `7c8d9e0` — updates the `RetryPolicy` to respect a per-environment
   `MAX_RETRY_BUDGET_MS` config key read from the environment; adds the key
   to `docs/env-vars.md` in the source repo.

**Wiki implication:**
- Create a new `infra-retry-policy` page documenting the class, its
   configuration surface, and the backoff algorithm.
- Update the existing `infra-overview` page to reference the new page.
- Update `config-env-vars` to document `MAX_RETRY_BUDGET_MS`.
- Skip the rename commit — it is a pure import-path refactor; all call
   sites were updated automatically and no wiki reference uses the old path.

**Plan:**

```json
{
  "version": "1",
  "skip_reason": null,
  "summary": "Document new RetryPolicy class and MAX_RETRY_BUDGET_MS config key.",
  "updates": [
    {
      "slug": "infra-overview",
      "rationale": "Surface the new RetryPolicy in the infrastructure overview.",
      "ops": [
        {
          "op": "append_section",
          "after_heading": "## Components",
          "content": "- [[infra-retry-policy]] — exponential backoff with per-environment budget cap.\n"
        },
        {
          "op": "frontmatter_add_related",
          "items": ["[[infra-retry-policy]]"]
        }
      ]
    },
    {
      "slug": "config-env-vars",
      "rationale": "Document the new MAX_RETRY_BUDGET_MS variable added in commit 7c8d9e0.",
      "ops": [
        {
          "op": "append_section",
          "after_heading": "## Variables",
          "content": "### MAX_RETRY_BUDGET_MS\n\nCaps the total wall-clock time (ms) that the `RetryPolicy` will spend across all retry attempts for a single call. Defaults to `5000`. See [[infra-retry-policy]].\n"
        }
      ]
    }
  ],
  "creates": [
    {
      "slug": "infra-retry-policy",
      "category": "components",
      "front_matter": {
        "title": "RetryPolicy — exponential backoff",
        "description": "Configurable exponential-backoff retry wrapper used across all infra HTTP calls.",
        "tags": ["components", "infra", "reliability"],
        "created": "2026-04-26",
        "updated": "2026-04-26",
        "status": "current",
        "related": ["[[infra-overview]]", "[[config-env-vars]]"]
      },
      "body": "# RetryPolicy — exponential backoff\n\n## What it does\n\n`RetryPolicy` in `src/infra/retry.py` wraps any callable with configurable exponential backoff. It reads `MAX_RETRY_BUDGET_MS` from the environment to cap total retry wall-clock time.\n\n## Configuration\n\n| Parameter | Default | Notes |\n|---|---|---|\n| `max_attempts` | 4 | Hard ceiling on retry count |\n| `base_delay_ms` | 200 | First retry delay |\n| `jitter` | `True` | Adds ±10% to each delay |\n| `MAX_RETRY_BUDGET_MS` (env) | 5000 | Total budget; abort if exceeded |\n\n## See also\n\n- [[infra-overview]]\n- [[config-env-vars]]\n"
    }
  ],
  "deletes": [],
  "bidirectional_links": [
    { "a": "infra-retry-policy", "b": "infra-overview" },
    { "a": "infra-retry-policy", "b": "config-env-vars" }
  ],
  "commits_addressed": [
    {
      "sha": "a1b2c3d",
      "pages": ["infra-retry-policy", "infra-overview"]
    },
    {
      "sha": "7c8d9e0",
      "pages": ["config-env-vars", "infra-retry-policy"]
    }
  ],
  "commits_skipped": [
    {
      "sha": "e4f5a6b",
      "reason": "Pure rename of importable lib files (Task 7 of the v2 fixes plan); no semantic change to document — the rename is invisible at the call sites."
    }
  ],
  "log_entry": {
    "operation": "ingest",
    "commit_range": "a1b2c3d..7c8d9e0",
    "summary": "Document new RetryPolicy class and MAX_RETRY_BUDGET_MS config key.",
    "pages_affected": ["infra-retry-policy", "infra-overview", "config-env-vars"]
  }
}
```
