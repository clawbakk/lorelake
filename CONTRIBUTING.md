# Contributing to LoreLake

Thanks for wanting to contribute. A few things to know before you open a PR.

## Running tests

No build step — LoreLake is bash + Python. Install the dev dependency once:

```bash
pip install -r requirements-dev.txt
```

Then run the full suite:

```bash
# Python lib tests
python3 -m pytest tests/lib/ -q

# Bash lib tests
bash tests/hooks/test_constants.sh
bash tests/hooks/test_detect_project_root.sh
bash tests/hooks/test_agent_id.sh

# Optional — shellcheck, if installed
shellcheck hooks/post-merge.sh hooks/session-end.sh hooks/session-start.sh hooks/lib/agent-run.sh
```

## Spec-first workflow

Non-trivial changes start with a design note. Read [`CLAUDE.md`](./CLAUDE.md) for the repo's conventions and the three-writer architecture. If you're proposing a change that affects the schema, a hook, or a skill, open a GitHub issue describing the design before writing the PR — it is cheaper to align on scope up front than to rework a finished implementation.

## Pull-request checklist

- [ ] Tests updated or added for any behavior change.
- [ ] Shell scripts pass `bash -n` and (when available) `shellcheck`.
- [ ] `CHANGELOG.md` has an `## [Unreleased]` entry describing the change.
- [ ] No credentials, emails, or personal data in the diff.
- [ ] User-facing docs (README, INSTALL, SECURITY) updated if the change is user-visible.

## Versioning

LoreLake follows [Semantic Versioning](https://semver.org/):

- **Major (x.0.0):** breaking changes to the schema, the plugin manifest, or the `<project>/llake/` layout.
- **Minor (0.x.0):** new writers or skills, new config keys, schema additions that remain backward-compatible.
- **Patch (0.0.x):** bug fixes, doc-only changes, internal refactors.

Keep `CHANGELOG.md` current as part of the same PR that makes the change.

## Reporting security issues

Security reports go through the private advisory channel documented in [`SECURITY.md`](./SECURITY.md), not a public issue.
