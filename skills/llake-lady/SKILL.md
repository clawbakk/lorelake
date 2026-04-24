---
name: llake-lady
description: Use when installing LoreLake into a project for the first time. Invoked via /llake-lady.
disable-model-invocation: true
---

# LoreLake Lady — Install Wizard

The user invoked `/llake-lady`. Your job is to install LoreLake into their current project by walking the eight phases below, in order. Do not skip, reorder, or improvise — the install plan in `templates/plan.md.tmpl` and the config in `templates/config.default.json` are canonical.

You are an orchestrator, not an installer. You write **exactly two files yourself**: `<project>/llake/config.json` and `<project>/llake/install-plan.md`. Everything else — directory scaffolding, `.gitignore`, git hooks, Claude Code settings — is the job of the **executor subagent** you spawn in Phase 7, which walks the plan's checkboxes.

**Why split wizard and executor?** The plan is a self-contained markdown document. Any future Claude Code session can re-execute it by reading the file ("execute this plan: `<path>`") — the wizard is not required after Phase 5. Keeping *deciding* (wizard) separate from *acting* (executor) is what makes that true. It also means the same plan is idempotent across interrupts: the executor reads `llake/log.md` on entry, finds the last completed phase, and resumes.

---

## Resolve paths before doing anything

Two absolute paths drive every phase. Compute them once at the start; use them literally thereafter.

- **`$PLUGIN_ROOT`** — where this skill lives. Claude Code exposes the skill's base directory at invocation (the `skills/llake-lady/` directory). `$PLUGIN_ROOT` is two levels up from there. If you are unsure, run `realpath` against the SKILL.md path you were given and strip `/skills/llake-lady/SKILL.md`.
- **`$PROJECT_ROOT`** — the user's current working directory (`pwd` at invocation time). This is the project being installed into.

Echo both paths back to the user in a single line before Phase 1 so a wrong CWD is caught before any files are written.

---

## Phase 1 — Prerequisites check

Verify in order. On any failure, emit a clear message naming the missing prerequisite and the fix, then stop. **Do not auto-install anything** — the user owns their toolchain.

1. **Project root looks real.** At least one of these must exist in `$PROJECT_ROOT`:
   - `.git/`
   - `CLAUDE.md`
   - A common manifest: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`

   If none: "The current directory does not look like a project root. `cd` into the project and re-run `/llake-lady`."

2. **Git-initialized.** Run `git -C "$PROJECT_ROOT" rev-parse --show-toplevel`. If it fails, do **not** stop — emit a warning and continue:
   > "`$PROJECT_ROOT` is not a git repo. The post-merge ingest hook will be deferred. Run `git init` then `/llake-doctor` to finish wiring."

3. **Plugin templates readable.** Confirm these files exist and are readable:
   - `$PLUGIN_ROOT/templates/config.default.json`
   - `$PLUGIN_ROOT/templates/plan.md.tmpl`
   - `$PLUGIN_ROOT/templates/index.md.tmpl`

   If any is missing: "Plugin install appears incomplete (missing `<file>`). Reinstall the plugin."

4. **No existing install.** If `$PROJECT_ROOT/llake/` exists (even as an empty directory): stop. "LoreLake is already installed in this project at `<absolute path>/llake/`. Run `/llake-doctor` to diagnose or repair, or remove that directory first."

---

## Phase 2 — Discovery

Read, don't ask. Gather every signal silently:

- `$PROJECT_ROOT/CLAUDE.md` — domain signal, conventions, branch hints. Already injected in this session if present; re-read if you need specifics.
- `$PROJECT_ROOT/README.md` — purpose, usage.
- Any present manifests: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle` — project type, top-level dependencies, and the `name` field (used later as `PROJECT_NAME`).
- `git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD` — best guess at the default branch. The repo's current branch may not be `main`/`master`; use whatever `HEAD` resolves to. If the repo is brand-new and `HEAD` resolves to a symbolic ref with no commits, fall back to `main`.

**Do NOT try to infer project-specific wiki categories.** Only the four fixed categories (`discussions`, `decisions`, `gotchas`, `playbook`) are created at install. Project-specific categories emerge during `/llake-bootstrap` and ongoing ingest — any prediction now would bias the wiki.

Keep the findings in session memory. Don't dump raw file contents back to the user.

---

## Phase 3 — Mode selection

Ask the user exactly one question, via the `AskUserQuestion` tool. The recommended option (**Auto**) goes first.

- **Auto** *(recommended)* — apply defaults plus discoveries silently, write the config, generate the plan, and spawn the executor without further prompts.
- **Interactive** — walk through each config section, confirming defaults and discovered values; pause for plan review before execution.

Phrase the question as "How would you like me to install LoreLake?". Wait for the answer — it determines Phase 4 vs Phase 4-alt and whether Phase 6 fires.

---

## Phase 3.5 — Critical config prompts

Even in auto mode, two config values have no sensible default: `ingest.branch` (depends on the repo) and `ingest.include` (depends on the codebase layout). Ask the user both, using `AskUserQuestion`, regardless of the mode chosen in Phase 3.

### Question 1 — Branch

Suggested default = the branch you resolved in Phase 2. Present one option with that branch preselected, plus an explicit "other" option the user can type into. Phrase the question: "Which branch should the post-merge ingest hook watch?"

Accept the answer as a non-empty string. If the user picks the suggested default, use the discovered branch; if they type an override, use that.

### Question 2 — Ingest scope

Build the suggested default by scanning `$PROJECT_ROOT`:

1. List directories at the top level only (one directory deep).
2. Exclude: any entry whose name begins with `.` or `_`, plus the literal names `llake`, `node_modules`, `venv`, `.venv`, `dist`, `build`, `target`, `.next`, `.nuxt`, `.cache`, `coverage`, `tmp`.
3. Sort the remaining names alphabetically. Append a trailing `/` to each (matching the `src/` style already shipped in `config.default.json`).

Present the resulting list as the proposed `ingest.include`. If the list is empty after filtering, fall back to `["src/"]` and state that in the question: "No obvious code directories found; defaulting to `src/`. Override if your code lives elsewhere."

Accept the answer as a JSON array of strings. If the user keeps the default, use the discovered list; if they edit it, parse and validate the edited version.

### Storing the answers

Keep both answers in session memory under the names `answered_branch` and `answered_include`. Phase 4 (auto) and Phase 4-alt (interactive) both consume them.

---

## Phase 4 — Render config (Auto mode)

Render `$PROJECT_ROOT/llake/config.json` as a **full** copy of `templates/config.default.json`, not a minimal subset. Pedagogy beats minimalism — the user should see every knob immediately. Apply these adjustments:

- **Preserve every `_comment` field** at its section. These are the inline documentation future-you (the user) will read when editing the file by hand.
- **Apply discovered values where they are unambiguous:**
  - `ingest.branch` → the value `answered_branch` collected in Phase 3.5.
  - `ingest.include` → the array `answered_include` collected in Phase 3.5.
  - `prompts.ingest.EXAMPLES` → fill only if `CLAUDE.md` supplies enough domain-specific signal to author two or three short, concrete worked examples in the style of `templates/generic-examples.md`. **Do not invent fabricated examples.** If uncertain, leave it as `""` and the prompt renderer will fall back to the shipped generic set.
- **Every other key stays at its default.** The user can edit `config.json` later; over-customization at install is friction with no payoff.

Create `$PROJECT_ROOT/llake/` (the directory does not exist yet) so that `Write` can land. The executor idempotently reconciles the full directory tree later — only `llake/` itself is needed now.

Write the config. Move to Phase 5.

### Phase 4-alt — Render config (Interactive mode)

Walk each section of `templates/config.default.json` in order (`llake`, `sessionCapture`, `ingest`, `transcript`, `logging`, `prompts`).

> Skip `ingest.branch` and `ingest.include` — Phase 3.5 already collected them. Use the `answered_branch` and `answered_include` values directly. All other keys in the `ingest` section (and every other section) are still walked.

For each leaf key (ignore `_comment` and `_schemaVersion` — those are plumbing):

1. Show the default value from the template.
2. Show the discovered value if any (e.g., the branch you found).
3. Accept the default/discovered value or take an override.

At the end, render the full resulting object (preserving `_comment` annotations) to `$PROJECT_ROOT/llake/config.json`.

---

## Phase 5 — Plan generation

Read `$PLUGIN_ROOT/templates/plan.md.tmpl`. Substitute these placeholders:

| Placeholder | Value |
|---|---|
| `{{PROJECT_NAME}}` | Use this order of preference: the `name` field of a top-level manifest (`package.json`, `pyproject.toml`, `Cargo.toml`, etc.); otherwise, the basename of `$PROJECT_ROOT`. |
| `{{DATE}}` | Today, `YYYY-MM-DD`. |
| `{{PLUGIN_PATH}}` | `$PLUGIN_ROOT` — absolute, fully resolved, **not** a symlink. The plan embeds this so the executor (or any future session) can find the plugin from a different working directory. |
| `{{PROJECT_ROOT}}` | Absolute path to the project. |
| `{{EMBEDDED_CONFIG}}` | The JSON you wrote in Phase 4, pretty-printed, with `_comment` fields preserved. This makes the plan self-contained — the user can regenerate `config.json` from the plan alone if needed. |
| `{{PLAN_PATH}}` | The absolute path `$PROJECT_ROOT/llake/.state/install-plan.md` — the executor reads this to know which file to delete in its final step. |

Write the filled plan to `$PROJECT_ROOT/llake/.state/install-plan.md`. Create `$PROJECT_ROOT/llake/.state/` first if it does not exist — the executor will create the rest of the `.state/` subtree in its Phase 1, but the plan itself must land before then. The `.state/` directory is covered by the installer's `.gitignore` entry, so the plan never enters git history.

This is the last file the wizard writes. Phase 7's executor handles everything else — including deleting this plan file as its final step.

---

## Phase 6 — User review *(interactive mode only)*

**Skipped entirely in auto mode.** The auto-mode contract is "trust the defaults, just do it" — pausing here would be pure friction. The executor's output and the embedded plan remain inspectable afterward.

In **interactive mode**, print the plan path and pause:

> "Install plan written to `<absolute path>`. Please review it, then reply with `execute` to proceed. Any other response aborts — the plan is saved and can be resumed later."

Wait for the user's reply. Treat `execute` case-insensitively, ignoring leading/trailing whitespace.

- If `execute`: proceed to Phase 7.
- Otherwise: print "Plan saved at `<path>`. To resume, type in any Claude Code session: `execute this plan: <path>`." Then stop — do not run Phase 7 or Phase 8.

---

## Phase 7 — Execute

### Auto mode
Print a single-line announcement and then immediately spawn the executor subagent. Do not prompt the user again.

> "Executing install plan (`<plan path>`). The executor will create `<project>/llake/`, append to `.gitignore`, wire `.git/hooks/post-merge`, and run `/llake-doctor` as the final verification phase."

### Interactive mode (after user typed `execute`)
Spawn the executor subagent directly — the user already saw the plan, no further announcement needed.

### Executor subagent invocation

Use the `Agent` tool with `subagent_type: "general-purpose"`. Send a self-contained prompt (the subagent has no view of this conversation):

```
You are the LoreLake install executor.

Walk the install plan at <PLAN_PATH> one checkbox at a time, in phase order, appending a log line to <project>/llake/log.md after each phase completes.

Inputs:
- Plan path: <absolute path to install-plan.md>
- Project root: <absolute path to project>
- Plugin path: <absolute path to plugin>

Rules:
1. Read the plan file in full before starting. Every step is idempotent — before executing a step, check whether the target file or state already matches the plan; skip if so.
2. On entry, tail <project>/llake/log.md if present. Start from the phase after the last "Phase N complete" line. Resumption is the default path, not an edge case.
3. Execute phases in the order they appear in the plan (Phase 1 → 6). Each checkbox within a phase is an instruction; checkbox updates in the plan file are optional, the log is the source of truth.
4. After each PHASE completes (not each checkbox), append the exact log line specified at the end of that phase to <project>/llake/log.md. The format is a "## [YYYY-MM-DD] install-plan | Phase N complete: <short description>" heading.
5. Write surface: <project>/llake/**, <project>/.gitignore, and <project>/.git/hooks/post-merge. Do NOT write anywhere else. Claude Code hook registration is handled by the plugin manifest, not the installer.
6. Phase 4 invokes /llake-doctor. Invoke that skill via the Skill tool available in this session. If doctor reports issues, surface its full report verbatim in your summary.
7. Phase 5 is informational — do NOT run /llake-bootstrap. Bootstrap is the user's next step, not yours.
8. Your last action after Phase 4 completes is to delete the plan file at the path passed as "Plan path". Do not keep it, do not move it — delete only. If the delete fails (e.g. permissions), surface the error in your summary but do not treat it as a plan failure.

When done, print a concise summary: the paths you wrote, any warnings (non-git repo, skipped steps, etc.), and the doctor report from Phase 4.
```

Substitute the three absolute paths into `<PLAN_PATH>`, `<absolute path to project>`, and `<absolute path to plugin>` in the prompt.

Wait for the subagent's summary. Relay its key points in Phase 8.

---

## Phase 8 — Completion summary

Print a concise summary that always names:

- **Install plan:** executed and deleted (was at `<project>/llake/.state/install-plan.md`; removed after Phase 4 to keep the wiki store clean)
- **Config:** `<project>/llake/config.json`
- **Log:** `<project>/llake/log.md` (records each phase the executor completed; the tail is a resume cursor)
- **Doctor:** the report the executor surfaced from Phase 4 of the plan (or a note that doctor reported zero issues)
- **Next recommended step:** `/llake-bootstrap` when the user is ready to populate the initial wiki.

If the project was not a git repo, also remind the user: "Run `git init`, then `/llake-doctor`, to finish wiring the post-merge hook."

Do not re-run `/llake-doctor` here — the plan's Phase 4 already did.

In **interactive-abort** (the user declined at Phase 6): Phase 8 does not run. The Phase 6 abort message was the final output.

---

## Behaviors out of scope

- Editing `<project>/CLAUDE.md`. The SessionStart hook handles operating-context injection for every session.
- Bootstrapping wiki content — that is `/llake-bootstrap`.
- Suggesting project-specific categories — those emerge during bootstrap/ingest.
- Diagnosing or repairing an existing install — that is `/llake-doctor`.
- Auto-installing git or any other tool.
- Writing anywhere outside `<project>/llake/`. The executor handles the limited set of outside writes (`.gitignore`, `.git/hooks/post-merge`); the wizard does not.

## References

- Plan template: `templates/plan.md.tmpl`.
- Config template: `templates/config.default.json`.
- Index template: `templates/index.md.tmpl`.
- Sibling skills: `/llake-doctor` (diagnose/repair), `/llake-bootstrap` (populate wiki).
