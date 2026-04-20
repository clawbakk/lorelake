---
title: "LoreLake Schema — Conversation-Content Standard"
description: "Standards for pages derived from conversations (capture writer)"
---

# Conversation-Content Standard

Loaded by **capture**. The capture agent does not document code; it preserves the substance of a Claude Code session.

## Standard 2 — Conversation-content fidelity

Discussion entries (created by the capture agent) include a **Key Facts (immutable)** block — concrete, specific facts from the conversation: what was discussed, what was decided, exact numbers, exact terms, exact rationale. This block is **never edited after creation**, even if related wiki pages evolve later. Other sections (summary, links, continuation appendices) may be updated; the Key Facts are permanent.

This preserves the source-of-truth audit trail. As derived pages evolve, the original conversation context can be obscured or contradicted; the immutable Key Facts let future readers see what was actually said and decided.

ADRs and gotchas/playbook pages produced by capture follow the standard page format defined in `core.md` — frontmatter, overview, main content, code references, see-also. The capture agent must read existing content before writing updates, ensuring coherence.

## Discussion entry format

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

### Continuation format

When a follow-up session continues the same topic arc, the capture agent **appends** to the existing discussion file rather than creating a new one. Content above the `---` separator is immutable.

```markdown
[existing content — never modified]

---

## Continuation: <What Happened> (<YYYY-MM-DD>)

### Key Facts (immutable)
- New facts from this session...

### Summary
Brief narrative.

### Pages Created/Updated
- [[page]] — created/updated (description)
```

Frontmatter updates on continuation: `updated:` to the new date; new `related:` entries merged in. The discussions/ page count is NOT incremented — one topic arc = one entry.

## Standard 3 — Security (no sensitive data)

**No sensitive data in any wiki page, ever.** This rule overrides every other content guideline — *including* the Key-Facts immutability rule. If sensitive data slips into a Key Facts block (yours or a prior session's), redacting it is not only allowed but required. Immutability protects intent; it does not require leaking secrets.

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

A discussion entry that contains a password is not preserving truth — it is leaking it.
