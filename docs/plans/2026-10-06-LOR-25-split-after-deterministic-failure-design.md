# LOR-25 — v3 range split after a deterministic failure: design

**Ticket:** LOR-25 — https://linear.app/clawbakk/issue/LOR-25/v3-split-after-a-deterministic-failure-processes-an-empty-slice
**Spec touched:** `docs/specs/ingest-v3.md` §2, §3, §12 · `docs/adr/0001-ingest-v3-pipeline.md` (Cursor bullet) · `schema/operations.md` (v3 Cursor bullet) · `CONTEXT.md` (Range split)

## 1. Understanding

**Outcome.** When an ingest v3 run fails and holds the cursor, the first trigger after the cause is gone ingests
everything from the held cursor to HEAD. Range split and skip stay as the answer to a range that is too big or
too hard for analysis, and only to that.

**What happened (baqqetto, 0.1.8 → 0.1.9).** Three runs failed with `invalid brief` (a validator bug). Each
counted as a *work* failure, so the counter reached 3 and each retry halved the range. After the fix shipped,
the planner was still in split mode: first-parent chain `[6fc81a9, 0a8995d]`, midpoint `0a8995d`, a
package-bump-only commit. The run analysed nothing, moved the cursor to `0a8995d`, cleared the counter and
stopped. About 19K lines of PR content stayed un-ingested until the next trigger.

**The five causes (from the ticket), as they read in the code:**

| # | Cause | Code |
|---|---|---|
| 1 | An invalid brief is counted as a work failure, but it fails the same way on any range | `run.py:321` `record_failure(..., "work")` |
| 2 | The counter outlives its cause (plugin upgrade, class change) | `plan.py:59` keys the counter on `base` only |
| 3 | A failed split records the midpoint as `lastHead`, so retries keep halving | `run.py:160,321` pass `rp.head` |
| 4 | The midpoint is by first-parent commit count, not churn | `plan.py:69` `chain[len(chain) // 2]` |
| 5 | A finished split stops; the rest waits for the next merge | `run.py:341-351` returns 0; `post-merge.sh` runs once |

**Acceptance (ticket, verbatim intent):**

- A brief/schema validation failure holds the cursor without counting toward split/skip; the log names it a pipeline error.
- After a plugin upgrade, the next trigger plans `range held-cursor..HEAD`, whatever counter state is left.
- `skip` is reachable only through repeated *work* failures on a one-commit range, never through deterministic failures.
- A successful split re-plans the remainder (`midpoint..HEAD`) in the same invocation, or queues it right away.
- The split point is weighted by watched-path churn, or at least never lands on a slice with no watched changes.
- Regression test: three held runs with a brief validation error, then a fixed brief → a single range run over the full held range.

**Assumptions (mine, not the ticket's):** recorded in the plan's Decisions taken; the main ones are §2.1
(what "pipeline" covers), §2.3 (what "one-commit" means once churn weighting exists) and §2.5 (where the
continuation runs).

## 2. Design

### 2.1 A third failure class: `pipeline`

`plan.record_failure(llake, base, head, cls)` gains `cls == "pipeline"`:

| Class | Raised by | Cursor | Counter |
|---|---|---|---|
| `work` | analysis agent failed (budget, timeout, max turns, no result) | holds | +1 (same base and plugin), else restarts at 1; `lastHead` = the head that failed |
| `infra` | rate limit, overload, auth, network, no first turn | holds | unchanged |
| `pipeline` | `InvalidBrief` at brief assembly | holds | **cleared** |

Why a pipeline failure clears the counter instead of leaving it: the ticket says the counter must not outlive a
class change. A pipeline failure says nothing about range size, and an earlier work-failure episode is no longer
the reason the run stops. Clearing it means the next run, once the cause is fixed, plans the full range.

Only `InvalidBrief` becomes `pipeline`. Analysis agent failures stay `work`, including "no result"; internal
exceptions keep their current path (hold, no counter change, cursor-table row 10).

The hold message becomes `pipeline error: invalid brief: <first 3 errors> (not counted toward split)`, in both
`agent.log` (`HOLD: …`) and the `hooks.log` `agent-done` line (`held: agent <id> v3 (pipeline error: …) — cursor held`).

**Accepted cost.** A persistent pipeline error re-runs a full analysis on every trigger until a plugin fix
ships (no backoff). That is the ticket's explicit trade: halving cannot help, and skipping would lose content.
Logged as a follow-up candidate (a backoff for repeated pipeline errors under one plugin version).

### 2.2 The counter is bound to the plugin version

`.state/ingest-failures.json` becomes `{base, count, lastHead, plugin}`. `plugin` is the `version` field of the
plugin's own `.claude-plugin/plugin.json`, read by a new `common.plugin_version()` (path resolved from the
module file; `"unknown"` when the file or field is missing).

`plan.active_failures(llake, base)` returns the record only when its `base` and `plugin` both match the current
ones, else `{}`. `plan_run` and `record_failure` use it. So:

- after an upgrade, a leftover record (any count, any `lastHead`) is ignored: the run plans `range base..HEAD`;
- a record written by 0.1.9 or earlier has no `plugin` key, so it never matches: the first 0.1.10 run is a full range;
- the next work failure under the new version restarts the count at 1.

`run.py` logs one line when a record exists but does not apply: `failure counter ignored (plugin <old> → <now>,
base <b7>): planning the full range`. Nothing deletes the stale file; the next finalize or failure replaces it.

### 2.3 Churn-weighted split point

New `plan.commit_churn(repo, sha, include)`: the sum over `git diff --numstat <sha>^1 <sha> -- <include>` of
added + deleted lines, at least 1 per listed file (a pure rename or a binary file counts 1). For a merge commit
this is its first-parent diff, i.e. the PR's content.

New `plan.split_point(repo, base, target, include)`:

1. `commits` = the first-parent chain of `base..target`, oldest first.
2. `watched` = the commits whose own churn is > 0, in order.
3. If fewer than 2 are watched → return `None` (the range cannot shrink).
4. Candidates = every watched commit except the last one, and only those where `base..candidate` has net
   watched changes (`watched_changes`). The last watched commit is excluded so the remainder always holds
   watched content; the net check guarantees the slice is never empty (a revert pair could cancel out).
5. Pick the candidate whose cumulative churn is closest to half the total (`abs(2*cum - total)`, integers);
   ties go to the earlier commit. No candidate → `None`.

`plan_run` with an active counter at ≥ 2:

```
target = lastHead or head            (lastHead unresolvable → target = head, as today)
point = split_point(base, target)
point            → RunPlan("split", point)
None, watched_changes(base, target) → RunPlan("skip", target)
None, no watched changes            → RunPlan("range", head)
```

**"One-commit range", redefined.** With churn weighting, a range whose watched changes all sit in one
first-parent commit cannot be split without landing on an empty slice (the baqqetto case: `0a8995d` empty,
`6fc81a9` all content). So skip triggers when at most one first-parent commit in `base..target` carries watched
changes; the trivial commits around it carry nothing to lose. A plain one-commit range is the special case.
Skip is still reachable only after two counted *work* failures (pipeline failures clear the counter; a version
change voids it).

On the baqqetto trace with the new code: the three invalid-brief runs leave no counter; the 0.1.9 → 0.1.10
run plans `range 2497ce0..HEAD`.

**Dead code removed.** `run.py`'s "split midpoint precedes every watched change" branch (and `NO_CHANGE_THEME`)
becomes unreachable: a split point always has net watched changes. It goes, with its e2e test replaced by one
that pins the new split point.

### 2.4 `lastHead` (cause 3)

Kept as "the head of the run that failed". For counted work failures that is what makes the search converge:
each failed split halves the remaining suspect range. What cause 3 complained about, halving that keeps going
after the trigger moved on for reasons unrelated to range size, is closed by §2.1 (pipeline failures clear) and
§2.2 (a version change voids). No separate change.

### 2.5 Continuation after a finalized split or skip

**Python.** `run.EXIT_CONTINUE = 3`. After a finalized `split` or `skip`, `run()` re-reads `HEAD`; if the
cursor (`rp.head`) is not `HEAD`, it logs `remainder <h7>..<HEAD7> pending: continuing in this invocation` and
returns 3. Every other outcome keeps 0 (done) or 1 (held). A kill after finalize still returns 0.

**Bash.** In the `post-merge.sh` v3 subshell, under the same lock:

```
run_ingest_v3_watched || V3_RC=$?
if [ "$V3_RC" -eq "$V3_EXIT_CONTINUE" ]; then
  next_v3_agent        # fresh id, dir, log, pid file; repoints AGENT_LOG, CURRENT_PID_FILE, LLAKE_AGENT_ID, V3_AGENT_DIR
  log "continue | v3 agent <id> takes the rest of the range"
  run_ingest_v3_watched || true
fi
```

Two helpers go into `hooks/lib/ingest-v3.sh`:

- `run_ingest_v3_watched`: arms the watchdog (`V3_WATCHDOG` seconds, USR1 to `$MY_PID`), runs `run_ingest_v3`
  with the current agent globals and `V3_TIMEOUT`, tears the watchdog down (`kill_tree` + `wait`), removes the
  pid file, returns the run's exit code. The existing inline watchdog in `post-merge.sh` moves into it.
- `next_v3_agent`: new `V3_AGENT_ID` from `generate_agent_id`, dir under `$AGENTS_DIR`, `agent.log`,
  `orchestrator.pid` holding `$MY_PID`; sets `AGENT_LOG`, `CURRENT_PID_FILE`, `LLAKE_AGENT_ID` so the kill trap
  and `_agent_cleanup` act on the continuation run.

Why in bash and not a loop in Python: the hard watchdog and the deadline belong to one run. A Python loop would
give the remainder whatever was left of the first run's deadline; an analysis cut short by it is a *work*
failure and would start counting again. A fresh run gets a fresh deadline, agent dir, journal and kill-revert
target. The gate is not re-run: it already passed the full range, and the split's finalize reset the clock, so
re-gating would defer the remainder (the "nothing happened" symptom).

**Bound.** One continuation per invocation (an `if`, not a loop). The continuation itself cannot be a split or
skip: the finalize before it cleared the counter. If it fails it holds the cursor at the midpoint like any range
run.

### 2.6 Docs and version

- `docs/specs/ingest-v3.md`: §2 stage table (assemble brief: "pipeline failure, counter cleared"), run-kinds
  table (Split and Skip rows), §3 Failure counter and Range split paragraphs (plugin binding, churn weighting,
  continuation, one-watched-commit skip), §12 failure classes paragraph (adds pipeline) and cursor table (row 4
  "continues with the remainder"; row 5 split into 5a analysis work failure and 5b invalid brief).
- `docs/adr/0001-ingest-v3-pipeline.md` Cursor bullet; `schema/operations.md` v3 Cursor bullet;
  `CONTEXT.md` Range split; `CHANGELOG.md` `[Unreleased]` → Fixed.
- Version 0.1.9 → 0.1.10 in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
- `llake/` is not touched (ingest updates the wiki pages about run planning on its own).

## 3. Testing

| Behaviour | Test |
|---|---|
| pipeline clears, work counts with plugin, infra unchanged | `tests/lib/test_v3_plan.py` |
| record from another plugin version / without `plugin` is ignored | `test_v3_plan.py` |
| churn: merge commit counts its PR content; rename counts ≥ 1 | `test_v3_plan.py` |
| split point lands on the churn half; never on an empty slice; baqqetto shape → skip only after 2 work failures | `test_v3_plan.py` |
| existing halving-until-skip and two-commit tests still pass (equal churn) | `test_v3_plan.py` |
| invalid brief → hold, "pipeline error" in hooks.log, no counter | `tests/lib/test_v3_run_e2e.py` |
| **regression:** 3 held invalid-brief runs, then a valid brief → one `range` run over base..HEAD | `test_v3_run_e2e.py` |
| stale counter after an upgrade → full range, logged | `test_v3_run_e2e.py` |
| finalized split with HEAD ahead → exit 3; at HEAD → 0 | `test_v3_run_e2e.py` |
| post-merge: split then continuation in one invocation, cursor at HEAD, two `completed` lines | `tests/hooks/test_post_merge_v3.sh` |
| bash 3.2 portability of the new shell code | `tests/hooks/test_bash32_portability.sh` (existing) |
| manifests agree on 0.1.10 | `tests/lib/test_marketplace_manifest.py` (existing) |

## 4. Out of scope

- Backoff for repeated pipeline errors (follow-up candidate, §2.1).
- How a human clears a skipped range: LOR-22 (Backlog) owns it.
- Splitting inside a single merge commit (by its second-parent commits): YAGNI; a one-watched-commit range is skipped as above.
