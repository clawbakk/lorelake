---
title: "LoreLake Schema — Code-Content Standard"
description: "Standards for pages documenting code (bootstrap and ingest writers)"
---

# Code-Content Standard

Loaded by **bootstrap** and **ingest**. These two writers document the codebase. The standards below are non-negotiable.

## Standard 1 — Code-content completeness

Pages documenting the codebase must hold up to the **new-employee test:**

> Put yourself in the shoes of a new employee onboarding to this project. As you read the wiki, every question that surfaces in your head must be answered — on the same page, or one or two `[[wikilinks]]` away. After the read-through you should be able to start working on any in-scope task without flagging a senior engineer; if a follow-up question arises mid-task, the wiki is where you find the answer.

A "complete" page captures both **structure** (what the thing is and what it consists of) and **semantic value** (why it matters and how it shapes the system). Structure-only pages — function lists, class hierarchies, signature dumps — fail the test. The page must also tell the reader what role the subject plays in the broader project, what it enables, what would break if it changed or vanished, and what assumptions it propagates outward.

A "complete" page answers:

- **What is it?** Subject identity (subsystem, module, function, class, configuration block, protocol, data structure, workflow, integration, anything).
- **Why does it exist?** Purpose, the problem it solves, the alternatives it was chosen over.
- **What does it consist of?** Structure — its parts, members, contents.
- **What is its semantic value?** The role it plays in the larger project — what depends on it, what it enables, what invariants it upholds, what would break if it changed, the assumptions it propagates to callers.
- **How is it used?** Concrete examples — real call sites, message flows, configuration values in context.
- **Show concrete examples on the page when applicable.** Embed code snippets for functions/classes, sample configuration with surrounding context, payload examples for protocols, worked invocations for workflows, sample inputs/outputs. Prefer examples already in the source (tests, fixtures, doctest-style comments, README snippets) over invented ones — they are anchored in real behavior and won't drift.
- **What does it interact with?** Inputs, outputs, dependencies, integration seams, side effects.
- **What constrains it?** Conventions, invariants, lifecycle, error modes, gotchas.
- **Where does it connect?** `[[wikilinks]]` to related pages, decisions, gotchas.

The specific facets vary by paradigm. Object-oriented code: classes, methods, access modifiers. Functional code: pure/impure, types, signatures. Configuration: keys, defaults, effects, interactions. Data flow: messages, sequencing, edge cases. Infrastructure: components, lifecycle, dependencies. The principle is constant: **the page paints a full picture of its topic, including why that topic matters to the project.** Brevity is not a virtue when it forces the reader back to the source — or back to a colleague.

## Standard 3 — Security (no sensitive data)

**No sensitive data in any wiki page, ever.** This rule overrides every other content guideline.

Specifically forbidden in any page, frontmatter, code reference, or example:

- Credentials of any form: API keys, access tokens, refresh tokens, JWTs, OAuth secrets, SSH keys (public or private), passwords
- Database connection strings (with or without credentials), full connection URIs
- Private hostnames, internal URLs that bypass authentication, internal IP addresses
- Personal data: real names, email addresses, phone numbers, physical addresses, employee IDs
- Any value that appears in `.env`, secrets managers, or vaulted config

When uncertain, apply both tests:

1. **Exploitation test:** *"If a hacker knew this value, could they exploit it?"* If yes → do not capture.
2. **Public-disclosure test:** *"If this leaks publicly, is there even a 1% chance of harm?"* If yes → do not capture.

If a fact requires the value to make sense, **abstract it**: refer to "the API key" not the key itself, "the production database" not the connection string, "an internal endpoint" not the URL. The wiki must remain useful even if accidentally published; values that would make publishing harmful must not be there.

A "complete" page that contains a password is not complete — it is broken.
