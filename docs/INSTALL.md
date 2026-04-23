# Installing LoreLake

> LoreLake is a Claude Code plugin. You'll need Claude Code installed and working first. See <https://docs.anthropic.com/claude-code> if you don't yet.

## Prerequisites

- **Claude Code** — a recent version with plugin support.
- **bash 3.2+** — macOS system bash is fine; the plugin is deliberately compatible with bash 3.2.
- **Python 3** — the lib helpers are plain Python 3 scripts; any modern Python 3 works.
- **git** — strongly recommended. The post-merge ingest hook only activates in git repos. LoreLake will install without git, but the post-merge flow will be deferred until you run `git init` and then `/llake-doctor`.
- **A project to track.** LoreLake is installed per-project. You'll `cd` into your project and run `/llake-lady` from there.

## Install

### Path A — Claude Code marketplace (recommended)

Inside any Claude Code session:

```
/plugin install lorelake
```

Restart Claude Code if prompted, then continue to "First-project setup."

### Path B — Install from GitHub

Inside Claude Code:

```
/plugin install git+https://github.com/clawbakk/lorelake
```

Pin to a specific release:

```
/plugin install git+https://github.com/clawbakk/lorelake@v0.1.0
```

### Path C — Local development install (contributors)

For working on LoreLake itself, symlink the repo into your plugins directory:

```bash
ln -s "$(pwd)" ~/.claude/plugins/lorelake
```

Restart Claude Code. Changes to hook scripts take effect on the next hook fire; changes to `hooks/hooks.json` or `.claude-plugin/plugin.json` require a plugin reload.

## First-project setup

1. `cd` into the project you want to track.
2. In Claude Code, run:

   ```
   /llake-lady
   ```

   The install wizard asks whether to configure the project automatically or interactively (default: **Auto**), creates `<project>/llake/`, wires the git `post-merge` hook, and runs `/llake-doctor` as its final phase.

3. Populate the initial wiki (optional but recommended):

   ```
   /llake-bootstrap
   ```

   This runs in your active Claude Code session, reads `<project>/llake/config.json` → `ingest.include`, and dispatches subagents to write the first pages.

4. From here on, LoreLake runs itself. Sessions end → capture may fire. Merges → ingest may fire.

## Verification

Any time you suspect drift, run:

```
/llake-doctor
```

It checks structure, config, hook wiring, and the plugin manifest, then repairs what it can in place. It's idempotent and safe to run anytime.

## Updating

- **Marketplace install:** `/plugin update lorelake`.
- **GitHub install:** `/plugin update lorelake` pulls the latest commit on the default branch; use the pinned form (`@v0.1.0`) for reproducibility.
- **Local / symlink install:** `git pull` in the plugin repo.

After any update, run `/llake-doctor` in each tracked project to reconcile new config keys or hook wiring.

## Uninstalling

Globally:

```
/plugin uninstall lorelake
```

Per project: nothing automatic removes `<project>/llake/`. It is your data — delete it yourself if you want to stop tracking the project. The `.git/hooks/post-merge` shim fails silently if the plugin directory is no longer present — git ignores the exit code of post-merge hooks, so `git pull` continues unaffected.

## Troubleshooting

- **`/llake-lady` says "LoreLake is already installed"** — the `<project>/llake/` directory already exists. Run `/llake-doctor` to reconcile, or remove the directory and re-run.
- **Session start shows no LoreLake preamble** — the plugin isn't enabled for the current session. Check your Claude Code plugin list.
- **Post-merge hook doesn't fire on `git pull`** — git hooks are per-clone. Each collaborator who clones a LoreLake-tracked project needs to run `/llake-doctor` once to wire their local `.git/hooks/post-merge`. This is a git limitation, not a LoreLake one.
- **Wiki doesn't appear after `/llake-bootstrap`** — check `<project>/llake/log.md` for a terminal `bootstrap` entry. If only `bootstrap-task` entries are present, bootstrap was interrupted; re-running it offers a resume option.
- **Something else broken** — `/llake-doctor` is idempotent; run it first. If it can't repair the issue, delete `<project>/llake/` and reinstall.

## Support

File an issue at <https://github.com/clawbakk/lorelake/issues>. Please include your OS, Claude Code version, and the relevant tail of `<project>/llake/.state/hooks.log`.
