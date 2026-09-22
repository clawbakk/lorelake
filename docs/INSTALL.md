# Installing LoreLake

> LoreLake is a Claude Code plugin. You'll need Claude Code installed and working first. See <https://docs.anthropic.com/claude-code> if you don't yet.

## Prerequisites

- **Claude Code** — a recent version with plugin support.
- **bash 3.2+** — macOS system bash is fine; the plugin is deliberately compatible with bash 3.2.
- **Python 3** — the lib helpers are plain Python 3 scripts; any modern Python 3 works.
- **git** — strongly recommended. The post-merge ingest hook only activates in git repos. LoreLake will install without git, but the post-merge flow will be deferred until you run `git init` and then `/llake-doctor`.
- **A project to track.** LoreLake is installed per-project. You'll `cd` into your project and run `/llake-lady` from there.

## Install

### Path A — Claude Code marketplace (once listed)

Once LoreLake is accepted into the official Claude Code marketplace, one command installs it inside any Claude Code session:

```
/plugin install lorelake@clawbakk
```

Restart Claude Code if prompted, then continue to "First-project setup."

### Path B — Install from GitHub (recommended today)

Register the repo as a marketplace, then install:

```
/plugin marketplace add clawbakk/lorelake
/plugin install lorelake@clawbakk
```

Pin to a specific release by adding a ref to the marketplace source:

```
/plugin marketplace add clawbakk/lorelake@v0.1.0
/plugin install lorelake@clawbakk
```

### Path C — Local development install (contributors)

For working on LoreLake itself, register the local checkout as a marketplace:

```
/plugin marketplace add /absolute/path/to/lorelake
/plugin install lorelake@clawbakk
```

Local-path marketplaces are not cached — edits to hook scripts take effect on the next hook fire. Changes to `hooks/hooks.json` or `.claude-plugin/plugin.json` require `/reload-plugins`.

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

## Parallel branches and worktrees

`llake/log.md` and the four fixed-category indexes are append-only — every
capture and every ingest adds a row or an entry at the end. Two branches that
both captured a session would therefore conflict on every merge and rebase, so
the installer writes `<project>/llake/.gitattributes` marking exactly those
files `merge=union`: git keeps both sides' rows instead of writing conflict
markers. Wiki pages are deliberately left out — a conflict inside a page is a
real one, and you want to see it.

Two notes:

- **Installed before this existed?** Run `/llake-doctor`; it adds the rules.
  Branches that were created before the file landed still conflict once when you
  merge the ingest branch into them. Rebasing them instead picks the rules up and
  resolves automatically.
- **GitHub pull requests ignore `.gitattributes`.** A PR can still report a
  conflict in these files. Sync the branch locally (`git pull --rebase`), which
  now resolves on its own, then push.

## Tuning ingest frequency

Ingest is expensive — each run spawns a Claude agent. By default it does **not**
run on every merge. A gate in the `post-merge` hook batches changes until one of
two thresholds trips:

| Key | Default | Meaning |
|---|---|---|
| `ingest.schedule.enabled` | `true` | Master switch for batching. `false` restores a run on every merge that touches include paths. |
| `ingest.schedule.minChangedLines` | `1500` | Net lines changed (insertions + deletions) under `ingest.include` before ingest fires. |
| `ingest.schedule.maxAgeHours` | `24` | How long a non-empty pile may sit before it fires regardless of size. |

Merges that touch nothing under `ingest.include` are always skipped outright,
even with batching disabled.

Because `post-merge` is the only trigger, `maxAgeHours` is a rate limiter ("at
most one ingest per 24h"), not a scheduler. A week with no merges produces no
ingest — and a pile deferred before the quiet period stays queued, flushing on
the next merge.

The clock lives in `llake/.state/`, which is gitignored and therefore absent in
a fresh clone. The hook seeds it with the current time on first run rather than
treating it as overdue, so cloning a LoreLake-tracked project does not trigger
an immediate ingest.

Deferrals are logged to `llake/.state/hooks.log`, so you can tune from real data:

```
2026-09-20 14:22:10 | post-merge    | deferred: lines=412 files=3 age_h=6.1 need_lines=1500 need_age_h=24 (a1b2c3d..e4f5a6b)
```

**Forcing a run.** To ingest immediately regardless of the thresholds:

```bash
LLAKE_IGNORE_SCHEDULE=1 "$(git rev-parse --git-common-dir)/hooks/post-merge"
```

## Verification

Any time you suspect drift, run:

```
/llake-doctor
```

It checks structure, config, hook wiring, and the plugin manifest, then repairs what it can in place. It's idempotent and safe to run anytime.

## Updating

- **Marketplace install (any of Paths A or B):** `/plugin update lorelake@clawbakk`. For a pinned install, re-add the marketplace at the new ref: `/plugin marketplace add clawbakk/lorelake@v0.2.0`.
- **Local dev install (Path C):** `git pull` in the plugin repo, then `/reload-plugins` in Claude Code.

After any update, run `/llake-doctor` in each tracked project to reconcile new config keys or hook wiring.

## Uninstalling

Globally:

```
/plugin uninstall lorelake@clawbakk
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
