# Security Policy

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security reports. Instead, open a private security advisory:

<https://github.com/clawbakk/lorelake/security/advisories/new>

Expected first response: within 7 days. We'll acknowledge receipt, triage, and coordinate disclosure before any public fix ships.

## LoreLake's security posture

LoreLake writes a plain-markdown wiki inside your project. Its one binding content rule — enforced by Standard 3 of the [code-content standard](./schema/code-content-standard.md) and the [conversation-content standard](./schema/conversation-content-standard.md) — is:

> No credentials, connection strings, internal hostnames or IPs, or personally identifying information are written to the wiki.

This is a rule the agents enforce during capture and ingest, not a convention. If you notice a wiki page that violates it, report it via the private advisory above.

## Transcript handling

Session capture reads Claude Code's own transcript file read-only. The hook performs no network I/O; nothing leaves your machine. The triage pass samples a local excerpt (head + middle + tail). The capture pass writes only inside `<project>/llake/`.

## No telemetry

LoreLake does not emit usage telemetry. The plugin makes no network calls of its own; the only outbound traffic originates from Claude Code itself when you run a session.
