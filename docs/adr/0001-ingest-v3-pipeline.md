# Ingest v3: fan-out writers with direct edits under a per-page write surface, and a cursor that advances over recorded gaps

- **Status:** accepted, 2026-10-02.
- **Spec:** [Ingest v3 pipeline](../specs/ingest-v3.md).
- **Quality definition:** [Ingest quality bar](../benchmark/quality-bar.md).
- **Map:** [Ingest v3 wayfinder map (LOR-1)](https://linear.app/clawbakk/issue/LOR-1); decided in [LOR-14](https://linear.app/clawbakk/issue/LOR-14).

Legacy ingest is one agent that grows its context over the whole commit range; it is accurate on what it writes but leaves many stale statements behind and costs a lot on large backlogs. v2 (plan-apply) is cheap but scores far lower. For v3 we chose the design at the knee of the cost–quality curve on both benchmark fixtures: a read-only analysis writes a brief, code bundles the affected pages, and parallel writers edit their own pages directly, behind a write surface enforced at the permission level and checked again afterwards. Pages a run could not bring current go into a committed gap record, and the cursor advances over them instead of holding.

## Relation to the plan-apply decision

This ADR relates to the plan-apply decision for v2 (`llake/wiki/decisions/adr-plan-apply-split.md`, 2026-09-20). v2 keeps plan-apply; nothing here changes v2.

- **Refines.** v3 keeps that decision's core: analysis is read-only, code owns all bookkeeping (`updated:` dates, indexes, log, cursor), and the write surface is enforced, not requested.
- **Reverses, for v3, the writing mechanism.** Writers edit pages directly; a plan JSON that code applies is rejected.
- **Reverses, for v3, the rejection of post-hoc validation.** Snapshot, diff and revert are now a second layer behind a permission-level boundary, not the only guard.

Why the plan-apply objection no longer holds: revert was unreliable when one agent interleaved edits across many pages. In v3 each page has exactly one writer, each bundle is snapshotted before its writer starts, and the per-page `Edit(//path)` allow list prevents out-of-surface writes rather than detecting them.

Costs of plan-apply that v3 removes:

- The plan JSON is a single point of failure: one syntax error loses a large plan.
- The op vocabulary collapsed in practice to whole-body replace, so it bought no safety over a direct edit.
- The whole plan has to fit one response's output cap.

## Decision

- **Topology.** One Opus analysis agent writes the brief; one Opus recall pass looks for pages it missed. Code groups the affected pages into bundles and spawns N Sonnet-medium writers in parallel, one per bundle. No coordinator agent.
- **Writing.** Writers edit their own pages directly. Each writer's allow list holds one `Edit(//path)` entry per owned page. Code snapshots each bundle before its writer starts, diffs afterwards, and reverts anything outside the surface or failing structural checks.
- **Verification.** $0 code checks on what was written, then one fix round for flagged pages. The LLM verifier ships off by default (a knob).
- **Bookkeeping.** Code alone writes `updated:` dates, indexes, the gap record, the log entry and the cursor.
- **Cursor.** The cursor advances over pages a run could not bring current; they are recorded in the committed `llake/ingest-gaps.json` and taken on first by the next run. A gap is marked stuck after 3 attempts and is left for a human. Writer failures never hold the cursor. Analysis failures do hold it, and after 2 work failures on the same base the next run ingests half the range (a one-commit range is skipped and recorded).

## Deliberate exception: Sonnet writers by default

The map's standing rule is "Opus for judgment; cheaper models only where the benchmark shows no loss". Sonnet-medium writers do show a loss, and are the default anyway:

- **The loss.** On the big fixture, Q 94.25 (mean of 6) against 97.5 for Opus-medium writers, and 0.52 writer-introduced errors per 100 added lines against 0.04. The default fails legacy parity (legacy: 0.05 per 100 lines).
- **Why anyway.** It leaves about 60–74 false statements on the pages after a run, against about 307 for legacy, and costs about half of Opus writers ($16.6 against $34.5 end to end on the big fixture). It is the knee on Q per dollar; under legacy parity (introduced errors only) the knee would be Opus-medium writers.
- **Who chose.** On 2026-10-02 the operator delegated config defaults ("use the defaults you think would work best"); the orchestrator chose Sonnet medium.
- **Reversal.** Opus-medium writers are one knob away (`ingest.v3.writerModel`).

This is a deliberate trade of introduced accuracy for coverage and price, stated so that nobody "fixes" it without the evidence.

## Considered options

Rejected:

- **Plan JSON per writer.** Smaller plans, but the same failure modes: one syntax error loses the bundle, the ops collapse to whole-body replace, the output cap still binds.
- **Hybrid: writers propose edits, code applies them.** Brings back the plan's failure surface for no gain; the permission layer already gives the guarantee code application was meant to give.
- **PreToolUse hook as the second layer.** Agents run with no settings sources, so no hooks load; the per-page allow list already denies out-of-surface writes, and the post-write diff catches anything else.
- **Single growing agent (legacy).** Accurate on what it writes and cheaper on the big backlog ($9.36), but Q 67.0 against 94.25: it leaves about five times as many false statements on the pages.
- **Coordinator agent.** The brief already settles who writes what; a coordinator adds an Opus context and a single point of failure without a measured gain.
- **Hold the cursor on writer failure or open major gaps.** One bad page would block every later merge (the cursor-held retry spiral already known from v2); a committed gap record keeps the debt visible and retried first.
- **Revert pages the verifier flags.** A flagged page is usually mostly improved; reverting throws away correct edits to remove one suspected error. Verifier findings become gaps.
- **Verifier on by default.** Sonnet raised Q by 0.35–0.65 for $2.8–4.1 more; an Opus verifier lowered Q; one pass found 25–63% of known errors. Kept as a knob.
- **Opus writers by default.** Best Q and parity-level accuracy, at about twice the price; see the exception above.
- **Deterministic-only staleness sweep.** Names-based checks cover 46% of stale claims and 23% of major ones on the big fixture, none on the small one. Judgment is needed; the sweep stays as leads and a check.

## Evidence

Aggregates from the two benchmark fixtures; full tables in spec §14. "K1" is the default; "O-med" is Opus-medium writers with the same brief and fix round. $ is end to end.

| | Big Q | Big $ | Small Q | Small $ | Introduced errors / 100 lines (big) | False statements left (big) |
|---|---|---|---|---|---|---|
| Legacy | 67.0 | 9.36 | 76.1 | 6.51 | 0.05 | ≈307 |
| v2 | 34.3 | 1.52 | 62.5 | 1.27 | — | — |
| **K1 (default)** | **94.25** | ≈16.6 | 100 | 2.61 | 0.52 | ≈60–74 |
| O-med | 97.5 | 34.52 | — | — | 0.04 | ≈28 |

Marginal gain: 3.8 Q per dollar from legacy to K1, 0.18 from K1 to O-med. Q noise between single runs is about 2 (standard deviation 0.85 over 6 runs).

## Consequences

- **Cost.** The default costs about 1.8× legacy on a large backlog and about 0.4× on a short range. No eligible setting reached "a fraction of legacy cost" on the big backlog.
- **The cursor means "accounted for", not "current".** Pages behind the cursor may still be stale; the gap record says which, and the next run takes them first.
- **A second committed state file.** `llake/ingest-gaps.json` sits next to `llake/last-ingest-sha`. It has no union merge rule, so two branches that both changed it can conflict on merge.
- **Partial writes are handled by snapshot.** A writer that dies mid-bundle leaves no half-written page: the bundle is reverted and retried page by page.
- **Unvalidated end to end.** Downstream stages were measured on frozen briefs. The design is unproven until the first real-install shakedown (spec §15) passes.
