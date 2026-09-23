# Enforcing a write surface on `claude -p` at the permission level

- Ticket: [LOR-3](https://linear.app/clawbakk/issue/LOR-3) (child of the LOR-1 map)
- Date: 2026-09-22
- Claude Code version under test: 2.1.280 (`claude --version`)
- Sources: official Claude Code docs at code.claude.com/docs (fetched 2026-09-22) plus a scratch-directory experiment described under Evidence. Nothing here was taken from secondary write-ups.

The question: can the harness itself, rather than prompt prose or the ingest v2 Python applier, confine a writing `claude -p` agent to `<project>/llake/wiki/**` minus `wiki/discussions/**`?

## Summary

**Q1. Do `--allowedTools` / `--disallowedTools` / `settings.json` rules accept path patterns for Edit, Write, MultiEdit, and how do deny and allow interact?**
Yes, with one naming rule: path rules are written as `Edit(<pattern>)` and `Read(<pattern>)` only. `Edit` rules "apply to all built-in tools that edit files", so one `Edit(...)` rule covers Edit, Write and the legacy MultiEdit. A path rule written as `Write(...)`, `MultiEdit(...)`, `NotebookEdit(...)` or `Glob(...)` is "accepted but never consulted" and warns at startup. Patterns use gitignore syntax with four anchors: `//abs/path` (filesystem root), `~/path` (home), `/path` (anchored at the settings source; for CLI flags that is the primary working directory), and `path` (current directory). Deny rules are evaluated before ask and allow, across every scope: "If a tool is denied at any level, no other level can allow it", and `deny` rules "block in every mode, including `bypassPermissions`". `--allowedTools` and `--disallowedTools` are exactly "allow rules for one session" and "deny rules for one session" in this syntax. Two limits matter for a whitelist: a gitignore `!` negation cannot carve a subtree out of a directory a deny rule blocks as a whole (so "deny everything except wiki" is not expressible; confirmed in experiment A3), and an allow rule on its own only removes the *prompt*. The positive boundary therefore comes from `--permission-mode dontAsk`, which auto-denies every call that would otherwise prompt. Experiments A and E: with `dontAsk` + `Edit(//<root>/llake/wiki/**)` allowed + `Edit(//<root>/llake/wiki/discussions/**)` denied, the agent wrote exactly the two in-surface files and was denied on discussions, `src/`, a sibling directory and `/tmp`. `acceptEdits` is not a substitute: it auto-approves every edit inside the working directory, and experiment A2 shows `src/new.md` being written under it.

**Q2. Can a `PreToolUse` hook block writes outside a path set in `-p` mode, and what does the agent see?**
Yes. A command hook that prints `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}` (or exits 2 with the reason on stderr) blocks the call; "A blocking hook also takes precedence over allow rules" and hook decisions "don't bypass permission rules" (a matching deny rule still blocks even if the hook says allow). The hook can be passed per invocation with `--settings '{"hooks":{...}}'`; experiment B2 confirms it runs in `-p`, and the agent receives a `tool_result` with `is_error: true` whose text is `PreToolUse:Write hook error: <permissionDecisionReason>`; the call is also listed in the JSON result's `permission_denials`. Two caveats make a hook a second layer rather than *the* boundary: it fails open when it cannot start ("When the script path doesn't exist or isn't executable ... the action proceeds") and when it times out ("A timed-out command ... hook doesn't block the tool call"). Experiment B reproduced the first: with the guard script not executable, all seven writes went through and no denial was recorded anywhere. Also, `${CLAUDE_PLUGIN_ROOT}` is for plugin `hooks.json`; a `--settings` hook should carry an absolute path.

**Q3. Can `Bash` be allowed in a restricted form without opening the write surface?**
Only partially, and not for `git log`. Prefix allow rules such as `Bash(ls *)` work and `dontAsk` denies everything else; compound commands are split and each part must match; redirection targets (`> file`, `tee`) and recognized file commands (`cat`, `head`, `tail`, `sed`, `tee`) are checked against the `Edit` allow/deny rules, so `echo x > src/f` was denied while `echo x > llake/wiki/concepts/f` was allowed (experiment C). But the check knows nothing about output-file *flags*: with `Bash(sort *)` allowed, `sort -o llake/wiki/discussions/sorted3.md` wrote into the `Edit`-denied `discussions/` directory and `sort -o src/sorted1.md` wrote into `src/`; only the write outside the working directory was stopped (experiment D). The docs' own matching table lists `git log --output=<file> main` as matched by `Bash(git log * main)`, and Read/Edit deny rules explicitly "don't apply to ... arbitrary subprocesses that read or write files indirectly". So a "read-only git" allow list (`Bash(git log *)`, `Bash(git diff *)`, `Bash(git show *)` — `--output` exists on all three) is a hole inside the working directory. Recommendation: leave `Bash` out of `--tools` for a writer and give it `Read`/`Glob`/`Grep`; if shell is unavoidable, allow only exact commands or prefixes with no output-file option, and back it with the `PreToolUse` hook (or the OS sandbox, which is Bash-only and off by default).

**Q4. How are the rules passed per invocation so the plugin ships them without per-project copies?**
Entirely on the command line: `--tools` (built-in tool set), `--permission-mode dontAsk`, `--allowedTools`/`--disallowedTools` (space- or comma-separated rules, absolute `//` form built from `$PROJECT_ROOT`), `--strict-mcp-config`, and `--settings '<inline JSON>'` for the hook. `--settings` "can set any key your user settings file can set" and sits above user/project/local settings, below managed. Two ambient hazards: without further flags a `-p` run loads the user's `~/.claude/settings.json` and the project's `.claude/settings.json` (experiment B2 shows the user's SessionStart hooks firing), and allow lists *merge* across scopes, so a broad user-level `Edit` or `Bash` allow rule would widen the surface. `--setting-sources ""` and `--restricted` (v2.1.248+) both shed those sources; experiments F1 and F2 show the surface holding with no ambient hooks loaded, and `--restricted` additionally confines the file tools to the working directory, removes command-running tools unless named in `--tools`, and refuses `bypassPermissions`. `--bare` also sheds them but requires `ANTHROPIC_API_KEY` (no OAuth), so it is not usable for subscription users.

**Verdict.** The permission system is reliable enough to be *the* boundary for the Edit/Write tools: `dontAsk` + absolute `Edit(...)` allow + `Edit(...)` deny + `--setting-sources ""` (or `--restricted`). It is not reliable for Bash, so a writer agent should not get Bash. A `--settings` PreToolUse hook is a good second layer but fails open, so it must not be the only one.

## Recommended configuration

Spawn the writer from the project root (or pass `--add-dir`), with `PROJECT_ROOT` absolute. Rules use the `//` absolute form so they do not depend on the cwd.

```bash
WIKI="//${PROJECT_ROOT#/}/llake/wiki"

cd "$PROJECT_ROOT" && \
IS_LLAKE_AGENT=true claude -p "$PROMPT" \
  --model "$MODEL" \
  --tools "Read,Glob,Grep,Edit,Write" \
  --permission-mode dontAsk \
  --setting-sources "" \
  --strict-mcp-config \
  --allowedTools "Read" "Edit(${WIKI}/**)" \
  --disallowedTools "Edit(${WIKI}/discussions/**)" \
  --settings "{\"hooks\":{\"PreToolUse\":[{\"matcher\":\"Edit|Write|MultiEdit|NotebookEdit\",\"hooks\":[{\"type\":\"command\",\"command\":\"${PLUGIN_ROOT}/hooks/lib/write-guard.sh\",\"timeout\":30}]}]}}" \
  --max-budget-usd "$BUDGET" \
  --output-format stream-json --verbose
```

Notes on each piece:

- `--tools "Read,Glob,Grep,Edit,Write"`: no Bash. `--tools` is what restricts the built-in set; `--allowedTools` only suppresses prompts (already documented in `llake/wiki/gotchas/claude-p-tools-flag.md`).
- `--permission-mode dontAsk`: the positive boundary. Anything not matched by an allow rule (or the built-in read-only set) is denied instead of prompting. Always pass it explicitly; a `-p` run otherwise starts in `default`, or in whatever `permissions.defaultMode` the user's settings set.
- `--allowedTools "Edit(${WIKI}/**)"`: one rule covers Edit and Write. `Read` is listed so reads outside the working directory (none expected) do not prompt-then-deny.
- `--disallowedTools "Edit(${WIKI}/discussions/**)"`: the deny rule wins over every allow rule at every level and in every mode.
- `--setting-sources ""`: shed user/project/local settings so a broad ambient allow rule cannot widen the surface. `--restricted` is a stricter alternative (also drops command tools and confines file tools to the working directory) if the plugin can require v2.1.248+.
- `--settings` hook: second layer. `write-guard.sh` must be executable and must resolve `tool_input.file_path` against `cwd` with `realpath` before comparing; the plugin should check `-x` before spawning and refuse to spawn (or log loudly) if the guard is missing, because a missing hook fails open silently.
- Read `permission_denials` from the JSON/stream-json result and log them: they are the audit trail that the boundary was exercised.
- If Bash must be added later: exact-match rules only (`Bash(git status)`), never a prefix on a command with an output-file option (`git log`, `git diff`, `git show`, `sort`, `find` with `-fprint`, and so on), and keep the hook.

## Evidence

### From the documentation

Rule syntax and Edit/Write coverage — https://code.claude.com/docs/en/permissions#read-and-edit
- "`Edit` rules apply to all built-in tools that edit files."
- "Claude Code checks file permissions against `Edit(path)` and `Read(path)` rules only. If you write a path rule for `Write`, `NotebookEdit`, `Glob`, or the legacy `MultiEdit` tool instead, Claude Code accepts the rule but never consults it, and warns at startup ... Use `Edit(docs/**)` in place of `Write(docs/**)` ... Requires Claude Code v2.1.210 or later."
- Pattern table: `//path` absolute, `~/path` home, `/path` relative to the settings source, `path`/`./path` relative to the current directory. For rules from "CLI flags or session rules", `/path` resolves to `<primary working directory>/path`.
- "A `!` rule listed first carves nothing out." and "A carve-out can't reopen a file inside a directory that a rule blocks as a whole. With `Read(secrets/**)` and `Read(!secrets/public/**)`, Claude Code still blocks `secrets/public` along with the rest of `secrets`."
- Deny rules apply "to file commands Claude Code recognizes in Bash, such as `cat`, `head`, `tail`, `sed`, and `tee`, and to the targets of Bash redirections such as `> file` and `< file`. They don't apply to a command that reads files without naming them ... or to arbitrary subprocesses that read or write files indirectly, like a Python or Node script that opens files itself. For OS-level enforcement ... enable the sandbox."

Deny/allow precedence — https://code.claude.com/docs/en/permissions#settings-precedence and https://code.claude.com/docs/en/settings-reference#permissions-deny
- "If a tool is denied at any level, no other level can allow it. For example, a managed settings deny can't be overridden by `--allowedTools`, and `--disallowedTools` can add restrictions beyond what managed settings define."
- "deny rules from any scope are evaluated before allow rules."
- `permissions.allow`: "Per-session overrides: `--allowedTools` adds allow rules for one session, and a deny rule from any settings file still blocks a tool it names." `permissions.deny`: "Per-session overrides: `--disallowedTools` adds deny rules for one session alongside this key."
- `permissions.defaultMode`: "Permission rules layer on top of every mode: `deny` rules block in every mode, including `bypassPermissions`."
- Settings lists merge: "When you set the same list key, such as `permissions.allow`, in more than one file, Claude Code combines the lists instead of picking one" — https://code.claude.com/docs/en/settings#lists-merge-instead-of-overriding

CLI flags — https://code.claude.com/docs/en/cli-reference
- `--allowedTools`: "Tools that execute without prompting for permission ... To restrict which tools are available, use `--tools` instead." Local `claude --help`: "Comma or space-separated list of tool names to allow (e.g. "Bash(git *) Edit")".
- `--disallowedTools`: "Deny rules. A bare tool name removes the matching tools from Claude's context ... A scoped rule such as `Bash(rm *)` leaves the tool available and denies only calls that match as written."
- `--tools`: "Restrict which built-in tools Claude can use ... The flag doesn't affect MCP tools; to deny those too, use `--disallowedTools "mcp__*"`."
- `--permission-mode`: "For `-p`, that's `default` when nothing is configured."
- `--settings`: "Path to a settings JSON file or an inline JSON string. Values you set here override the same keys in your `settings.json` files for this session."
- `--setting-sources`: "Comma-separated list of setting sources to load (`user`, `project`, `local`)".
- `--restricted`: "Claude Code removes the built-in tools that run commands or code, and WebFetch, unless you name them individually in `--tools` ... It also confines the built-in file tools to the working directories, loads only managed settings and `--settings`, refuses `bypassPermissions` ... Requires Claude Code v2.1.248 or later."
- `--permission-prompts none` (v2.1.259+): "Pass `none` when nobody can answer, and Claude Code denies them instead."

Permission modes — https://code.claude.com/docs/en/permission-modes
- dontAsk: "auto-denies every tool call that would otherwise prompt you. Claude still runs actions that need no approval in Manual mode, such as file reads inside your working directories and read-only Bash commands, plus actions matching your `permissions.allow` rules and calls approved by a PreToolUse hook."
- acceptEdits: "lets Claude create and edit files in your working directory without prompting ... auto-approval applies only to paths inside your working directory or `additionalDirectories`."
- Protected paths (`.git`, `.claude`, `.mcp.json`, ...) are "Denied" in `dontAsk` regardless of allow rules.

Headless mode — https://code.claude.com/docs/en/headless
- "For `-p`, the built-in starting permission mode is Manual on every plan".
- "`dontAsk`: Claude Code denies every call that would otherwise prompt, which is useful for locked-down CI runs."
- "Without `--bare`, a `-p` session runs the hooks in a project's `.claude/settings.json` and connects the servers in its `.mcp.json`, even in a folder you've never trusted."
- `--bare`: "Anthropic auth is strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings (OAuth and keychain are never read)" (`claude --help`).
- "With `--output-format stream-json`, denials appear as `permission_denied` system messages, and the final result message lists them in `permission_denials`."

Hooks — https://code.claude.com/docs/en/hooks and https://code.claude.com/docs/en/permissions#extend-permissions-with-hooks
- "Hook decisions don't bypass permission rules. Claude Code evaluates deny and ask rules regardless of what a PreToolUse hook returns".
- "A blocking hook also takes precedence over allow rules. A hook that exits with code 2 stops the tool call before permission rules are evaluated".
- `permissionDecisionReason`: "For `"deny"`, shown to Claude." and "A hook that blocks by exiting 2 routes the same way as `"deny"`: Claude sees the stderr message as the denial reason."
- Fail-open: "A hook that can't start lands in the same non-blocking bucket. When the script path doesn't exist or isn't executable, the shell exits with a code like 127 ... For most hook events, the action proceeds. When you set up a policy hook, watch for this notice on its first run: a mistyped path in `settings.json` leaves the gate silently disabled." and "A timed-out `command`, `http`, or `mcp_tool` hook doesn't block the tool call."
- Exit code 1 does not block: "Without valid JSON on stdout, Claude Code treats exit code 1 as a non-blocking error and proceeds with the action".
- `-p` and prompts: for PermissionRequest, "In sessions that can't show a prompt ... if no hook returns a decision, it denies the tool call." So a PreToolUse `"ask"` ends in a denial in `-p`.
- The `if` field on a hook accepts `Edit(docs/**)`-style filters but "the `if` filter is best-effort, use the permission system rather than a hook to enforce a hard allow or deny."
- The "Hook locations" table lists settings files, managed settings, plugin `hooks/hooks.json`, and skill/subagent frontmatter; `--settings` is not in that table but is documented for `disableAllHooks` and by the settings page's statement that it can set any user-settings key. Experiment B2 confirms a `--settings` hook runs.
- Path placeholders: "`${CLAUDE_PLUGIN_ROOT}`: the plugin's installation directory, for scripts bundled with a plugin."

Sandbox — https://code.claude.com/docs/en/sandboxing
- "Built-in file tools: Read, Edit, and Write use the permission system directly rather than running through the sandbox."
- Default write scope for sandboxed commands is "the current working directory and its subdirectories" plus added dirs and the temp dir; `Edit` allow rules and `sandbox.filesystem.allowWrite`/`denyWrite` are merged into the sandbox configuration. Off by default; macOS Seatbelt, Linux bubblewrap.

### From the experiment

Scratch layout (outside the repo), run from `project/` with `--model haiku`, `--max-budget-usd 0.5`, `--strict-mcp-config`, `IS_LLAKE_AGENT=true`; total spend across all runs about $0.45:

```
project/llake/wiki/concepts/existing.md
project/llake/wiki/discussions/existing.md
project/src/readme.md
outside/file.md
```

The write prompt asked for seven actions with Write/Edit only, no retries: (1) write `llake/wiki/concepts/new.md`, (2) edit `llake/wiki/concepts/existing.md`, (3) write `llake/wiki/discussions/new.md`, (4) edit `llake/wiki/discussions/existing.md`, (5) write `src/new.md`, (6) write `../outside/new.md`, (7) write `/tmp/llake-exp-outside-<run>.md`. The filesystem was diffed against a pristine copy after each run.

| Run | Flags (beyond the common set) | Files actually changed | Denied |
| --- | --- | --- | --- |
| A | `--tools Read,Write,Edit --permission-mode dontAsk --allowedTools "Edit(llake/wiki/**)" --disallowedTools "Edit(llake/wiki/discussions/**)"` | 1, 2 | 3, 4 ("File is in a directory that is denied by your permission settings."); 5, 6, 7 ("Permission to use Write has been denied because Claude Code is running in don't ask mode. ...") |
| A2 | same rules, `--permission-mode acceptEdits` | 1, **5** | 3, 4 (deny rule); 6, 7 ("Claude requested permissions to write to ..., but you haven't granted it yet.") — 2 and 4 hit a tool precondition ("File has not been read yet") because the prompt forbade Read |
| A3 | `--permission-mode acceptEdits --disallowedTools "Edit(**)" "Edit(!llake/wiki/**)"` | none | all seven: the `!` carve-out did not reopen `llake/wiki` |
| B | `--permission-mode acceptEdits --allowedTools Edit Write --settings '{"hooks":{"PreToolUse":[...write-guard.sh...]}}'`, guard **not executable** | **all seven** | none; `permission_denials: []`, guard's audit log empty — fail-open |
| B2 | same as B, guard `chmod +x`, `--output-format stream-json` | 1 | 3, 5, 6, 7 via `tool_result` `is_error: true`, text `PreToolUse:Write hook error: llake write-guard: <abs path> is outside the allowed write surface (...)`; all four in `permission_denials`; guard log shows every call with its resolved path |
| C | `--tools Read,Write,Edit,Bash --permission-mode dontAsk --allowedTools "Edit(llake/wiki/**)" "Bash(ls *)" "Bash(cat *)" --disallowedTools "Edit(llake/wiki/discussions/**)"`, ten shell commands | `echo hi > llake/wiki/concepts/bash2.md` | `echo hi > src/bash1.md`, `bash -c "..."`, `python3 -c "open(...)"`, `cp`, `tee src/bash6.md <<< hi`, `ls -la ../outside` (dontAsk); `sed -i "" ... llake/wiki/discussions/existing.md` ("Permission to use Bash with command ... has been denied.", the deny rule) |
| D | `--tools Read,Bash --permission-mode dontAsk --allowedTools "Bash(sort *)" --disallowedTools "Edit(llake/wiki/discussions/**)"` | **`src/sorted1.md`, `src/sorted2.md` (`--output=`), `llake/wiki/discussions/sorted3.md`** | only `sort -o ../outside/sorted4.md` (outside the working directory) |
| E | as A but absolute rules `Edit(//<abs root>/llake/wiki/**)` / `Edit(//<abs root>/llake/wiki/discussions/**)`, plus `--permission-prompts none` | 1, 2 | 3, 4 (deny rule); 5, 6, 7 (dontAsk; the denial text still contains the "You *may* attempt to accomplish this action using other tools" paragraph, unchanged by `--permission-prompts none`) |
| F1 | as E plus `--setting-sources ""` | 1 | 3, 4, 5, 6, 7; stream shows no `hook_started` events (the user's SessionStart hooks that fired in B2 are gone); `system/init` reports `tools: [Edit, Read, Write]`, `permissionMode: dontAsk`, `mcp_servers: []` |
| F2 | as E plus `--restricted` | 1 | 3, 4 (deny rule); 5 (dontAsk); 6, 7 ("<path> is outside <root>; --restricted confines the file tools to the working directory."); no ambient hooks |

Scripts: `expA.sh`, `expA2.sh`, `expA3.sh`, `expB.sh`, `expB2.sh`, `expC.sh`, `expD.sh`, `expE.sh`, `expF.sh`, `write-guard.sh` and `common.sh` in the session scratchpad (`.../scratchpad/exp/`); not committed. The guard hook reads the PreToolUse JSON from stdin, resolves `tool_input.file_path` against `cwd` with `os.path.realpath`, allows paths under `$LLAKE_WRITE_ROOT` except `$LLAKE_WRITE_ROOT/discussions`, and otherwise prints the deny JSON and exits 0.

## Open questions

- **`git log --output=` specifically.** Experiment D used `sort -o` as the analog because the worktree guard in this session refuses git in the scratch directory. The docs' wildcard table shows `Bash(git log * main)` matching `git log --output=<file> main`, and D shows the redirect/file-command check does not see output flags, so the hole is expected to exist for git too, but it was not exercised.
- **Allow rule outside the working directory.** All runs spawned the agent from the project root. Whether `Edit(//abs/…)` allow rules approve writes to a path *outside* the cwd under `dontAsk` (relevant if `session-end.sh` runs with a subdirectory cwd) was not tested. The hooks should `cd "$PROJECT_ROOT"` or pass `--add-dir "$PROJECT_ROOT"`; note `--restricted` would refuse such writes regardless.
- **`--setting-sources ""` is not documented as a value.** It is accepted by 2.1.280 and behaves as "load none" (F1), but the docs only describe the comma-separated `user,project,local` form. `--restricted` is the documented way to load only managed and `--settings`.
- **Which version introduced each behavior.** Path-rule warnings for `Write(...)` need v2.1.210+, Read-deny-blocks-Write needs v2.1.208/2.1.228+, `--restricted` needs v2.1.248+, `--permission-prompts` needs v2.1.259+. The plugin has no minimum-version check today.
- **Symlinks inside the wiki.** Not tested. Docs say allow rules require both the link and its target to match, deny rules match either.
- **`--permission-prompts none` wording.** The docs say it tells Claude "not to retry"; under `dontAsk` the denial text was identical with and without the flag. It may only affect prompt-fallback denials rather than `dontAsk` denials.
- **User-global hooks in background agents.** Independent of this ticket, B2 shows the plugin's current spawn (no `--setting-sources`/`--restricted`) runs the developer's global SessionStart hooks inside every ingest/capture agent. Worth its own ticket.
