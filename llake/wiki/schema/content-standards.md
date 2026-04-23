---
title: "Content Standards"
description: "Standards 1, 2, 3 — new-employee test, conversation fidelity, no sensitive data"
tags: [schema, content-standards, quality]
created: 2026-04-23
updated: 2026-04-23
status: current
related:
  - "[[schema-overview]]"
  - "[[page-format]]"
  - "[[three-writer-model]]"
  - "[[session-end-hook]]"
---

# Content Standards

## Overview

LoreLake enforces three content standards. They are non-negotiable: a page that violates any of them is considered broken, regardless of how much other content it contains. Standards 1 and 3 govern pages that document code (written by bootstrap and ingest). Standard 2 governs pages derived from conversations (written by capture). Standard 3 applies to every writer.

This page documents what each standard requires, which writers are bound by it, and how to apply it correctly.

## Standard 1 — Code-content completeness (the new-employee test)

**Applies to: bootstrap and ingest.**

Pages documenting code must hold up to the **new-employee test**:

> Put yourself in the shoes of a new employee onboarding to this project. As you read the wiki, every question that surfaces in your head must be answered — on the same page, or one or two `[[wikilinks]]` away. After the read-through you should be able to start working on any in-scope task without flagging a senior engineer. If a follow-up question arises mid-task, the wiki is where you find the answer.

A "complete" page captures both **structure** and **semantic value**. Structure-only pages — function lists, class hierarchies, signature dumps — fail the test. The page must also explain what role the subject plays in the broader project, what it enables, what would break if it changed or vanished, and what assumptions it propagates outward.

### The nine questions a complete page answers

A complete code-documenting page answers all nine of the following. The specific framing varies by paradigm (object-oriented, functional, configuration, data flow, infrastructure) but the underlying principle is constant: the page paints a full picture of its topic, including why that topic matters.

1. **What is it?** Subject identity — subsystem, module, function, class, configuration block, protocol, data structure, workflow, integration, or any other unit of meaning in the codebase.

2. **Why does it exist?** Purpose, the problem it solves, the alternatives it was chosen over. "It handles retries" is not enough — why is it here, and not somewhere else, or not at all?

3. **What does it consist of?** Structure: its parts, members, contents. For a module, its exports. For a config block, its keys. For a protocol, its message types.

4. **What is its semantic value?** The role it plays in the larger system: what depends on it, what it enables, what invariants it upholds, what would break if it changed, the assumptions it propagates to callers.

5. **How is it used?** Concrete examples — real call sites, message flows, configuration values in context. A page without examples is incomplete by definition.

6. **What does it interact with?** Inputs, outputs, dependencies, integration seams, side effects. What does it call? What calls it? What does it read from or write to?

7. **What constrains it?** Conventions, invariants, lifecycle rules, error modes, gotchas. What must be true for it to work? What breaks it?

8. **Where does it connect?** `[[wikilinks]]` to related pages, decisions that shaped it, gotchas that apply to it.

9. **Show concrete examples.** This is an amplification of question 5. Embed code snippets for functions and classes, sample configuration with surrounding context, payload examples for protocols, worked invocations for workflows, sample inputs/outputs. Prefer examples already in the source (tests, fixtures, doctest-style comments, README snippets) over invented ones — they are anchored in real behavior and won't drift.

### Brevity is not a virtue

A short page is not a good page. If a page answers all nine questions in five lines, that is fine. If a page is brief because it omits the semantic value or skips examples, it fails the test. The question to ask when finishing a page: "Could a new engineer read only this page and immediately work on this subsystem without asking anyone?" If the answer is no, the page is incomplete.

### Paradigm notes

The nine questions apply across paradigms, with appropriate translation:

- **Object-oriented code:** cover classes, methods, access modifiers, inheritance, contracts.
- **Functional code:** cover pure vs. impure, types, signatures, composition patterns.
- **Configuration:** cover every key, its default, its effect, and how it interacts with other keys.
- **Data flow / messaging:** cover message types, sequencing, edge cases, retry behavior.
- **Infrastructure:** cover components, lifecycle, dependencies, failure modes, scaling constraints.

## Standard 2 — Conversation-content fidelity

**Applies to: capture.**

The capture agent does not document code — it preserves the substance of a Claude Code session. Standard 2 governs how that substance is recorded.

### The immutable Key Facts block

Every discussion entry includes a **Key Facts (immutable)** block. This block contains concrete, specific facts from the conversation: what was discussed, what was decided, exact numbers, exact terms, exact rationale. Once written, this block is **never edited**, even if derived wiki pages evolve or are later contradicted.

Why: As the codebase changes and wiki pages are updated, the original context of a decision can be obscured or rewritten. The immutable Key Facts block provides a permanent audit trail: future readers can see exactly what was said and decided, not just what it evolved into.

```markdown
## Key Facts (immutable)

- Decided to use SQLite over PostgreSQL for local state; rationale: zero-dependency deployment.
- Timeout set to 30 seconds after testing showed p95 latency at 8 seconds.
- Two-pass triage design chosen to keep background cost bounded on high session volume.
```

Key Facts must be:
- **Concrete and specific**: exact numbers, exact terms, exact decisions. No vague summaries.
- **Never edited after creation**: even if a decision is reversed later.
- **Free of sensitive data**: Standard 3 overrides Key-Facts immutability. If sensitive data slips in, redact it — immutability protects intent, not leaked secrets.

### Discussion entry format

```markdown
---
title: "Session: <Topic>"
description: "<One-line summary>"
tags: [discussion, <relevant-topics>]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: immutable
related:
  - "[[affected-page-1]]"
commits:
  - "<short-sha>: <commit message>"
---

# Session: <Topic>

## Key Facts (immutable)

- Fact 1 (concrete, specific, never edited)
- Decision: chose X over Y because Z

## Summary

Brief narrative of what was accomplished and why.

## Pages Created/Updated

- [[page-1]] — created
- [[page-2]] — updated
```

### One topic arc per entry

One topic arc = one discussion entry. If a follow-up session revisits the same subject, the capture agent **appends** to the existing entry rather than creating a new file.

### Continuation format

When appending to an existing discussion entry, the content above the `---` separator is immutable. New content follows it:

```markdown
[existing content — never modified]

---

## Continuation: <What Happened> (<YYYY-MM-DD>)

### Key Facts (immutable)
- New facts from this session, just as immutable as the original block.

### Summary
Brief narrative of what happened in this follow-up session.

### Pages Created/Updated
- [[page]] — updated (description of change)
```

On continuation, the frontmatter `updated:` field is updated to the new date, and any new `related:` entries are merged in. The file itself is not renamed, and no new discussion file is created — the discussion entry count stays the same.

### What capture writes and does not write

The capture agent writes to:
- `wiki/discussions/` — discussion entries
- `wiki/decisions/` — ADRs arising from the session
- `wiki/gotchas/` — pitfalls surfaced during the session
- `wiki/playbook/` — procedures clarified or created during the session
- Category indexes for the above categories
- `log.md` (append)

The capture agent does **not** update architecture or project-specific category pages based on things discussed in the session. Those updates are ingest's responsibility — when the discussed code change is merged, the post-merge hook triggers ingest to update the relevant wiki pages. Capture records what was said; ingest records what landed.

## Standard 3 — Security (no sensitive data)

**Applies to: bootstrap, ingest, and capture. This standard overrides all others.**

No sensitive data in any wiki page, ever. This rule is absolute. A complete, well-written page that contains a password or connection string is not complete — it is broken.

### Forbidden data

Specifically forbidden in any page, frontmatter, code reference, or example:

- **Credentials of any form:** API keys, access tokens, refresh tokens, JWTs, OAuth secrets, SSH keys (public or private), passwords, bearer tokens.
- **Database connection strings:** with or without credentials, including full connection URIs (`postgres://user:pass@host/db`).
- **Internal network details:** private hostnames, internal URLs that bypass authentication, internal IP addresses (RFC 1918 ranges and others).
- **Personal data:** real names, email addresses, phone numbers, physical addresses, employee IDs, any data subject to privacy regulations.
- **Secrets-manager values:** any value that appears in `.env` files, secrets managers, or vaulted configuration.

### The two tests

When uncertain whether a value is safe to include, apply both tests:

1. **Exploitation test:** "If a hacker knew this value, could they exploit it?" If yes — do not capture.
2. **Public-disclosure test:** "If this leaks publicly, is there even a 1% chance of harm?" If yes — do not capture.

Both tests must pass (i.e., both answers must be "no") for a value to be safe to include.

### How to abstract

If a fact requires a sensitive value to make sense, abstract it:

| Instead of | Write |
|---|---|
| `sk-prod-abc123xyz` (API key) | "the production API key" |
| `postgres://app:hunter2@db.internal/prod` | "the production database connection string" |
| `10.0.1.45` (internal IP) | "the auth service host" |
| `jane.smith@example.com` | "the on-call engineer's contact" |

The wiki must remain useful even if accidentally published. Values that would make publishing harmful must not be there.

### Standard 3 and Key-Facts immutability

Standard 3 overrides Standard 2's immutability rule. If sensitive data appears in a Key Facts block — in a prior session's entry or one just written — **redact it immediately**. Immutability protects the audit trail of intent and decisions; it does not require the wiki to contain leaked credentials. A redacted Key Fact should read: `[redacted — contained sensitive data]` so the reader knows something was there without seeing the value.

## Writer-to-standard mapping

| Writer | Standard 1 (completeness) | Standard 2 (conversation fidelity) | Standard 3 (security) |
|---|---|---|---|
| Bootstrap | Required | Not applicable | Required |
| Ingest | Required | Not applicable | Required |
| Capture | Not applicable | Required | Required |

## Key Points

- Standard 1 requires pages to pass the new-employee test: a new engineer should be able to work on any in-scope task after reading the wiki, without asking anyone.
- A complete page captures both structure (what it is) and semantic value (why it matters). Structure-only pages fail the test.
- Standard 2's Key Facts block is immutable after creation — but Standard 3 overrides this: redact sensitive data even from immutable blocks.
- One topic arc = one discussion entry; follow-up sessions append continuations, never create new files.
- Capture records what was discussed; ingest records what landed in code. They do not duplicate each other's write surfaces.
- Standard 3 is absolute: it overrides every other guideline, and the two tests (exploitation + public-disclosure) must both pass for a value to be safe.

## Code References

- `schema/code-content-standard.md` — Standards 1 and 3 as loaded by bootstrap and ingest
- `schema/conversation-content-standard.md` — Standards 2 and 3 as loaded by capture
- `hooks/session-end.sh` — triggers the capture agent (two-pass triage → capture)
- `hooks/post-merge.sh` — triggers the ingest agent

## See Also

- [[schema-overview]] — the schema/ directory and its role in the system
- [[page-format]] — the structural format all pages must follow (independent of these content standards)
- [[three-writer-model]] — which writer is responsible for which content
- [[session-end-hook]] — how the capture agent is invoked and what it receives
