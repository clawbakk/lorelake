# LoreLake — Domain Glossary

Terms used when discussing LoreLake's writers and how their output is judged. Implementation details do not belong here.

## Writers

- **Ingest**: the writer that updates the wiki after code changes land on the monitored branch. Runs unattended. It works out what the changes mean for the project's current state and brings the wiki in line with it; it does not summarize diffs or commit messages, whose level of detail varies from commit to commit.
- **Ingest run**: one execution of ingest over one commit range.
- **Commit range**: the set of commits between the ingest cursor and the merged head, filtered to the paths the project asked ingest to watch.
- **Ingest cursor**: the last commit ingest has accounted for: every change up to it is reflected in the wiki or owed in the gap record.
- **Write surface**: the set of wiki pages a writer is allowed to change. Enforced outside the writer, not by its instructions; in a run with several writers, each writer's surface is only the pages assigned to it.
- **Batching gate**: the rule that decides whether a merge triggers an ingest run now or lets changes pile up.

## The wiki

- **State page**: a page that describes the project as it is now, so that a question about the project can be answered by reading it. Ingest keeps state pages true at the range head: stale content is corrected, new facts are added, changed facts are updated.
- **Record page**: a page that captures a point in time: a decision taken, a discussion held, a change planned. Its value is the record itself, so it is not rewritten to match the current code; when the code moves past it, it is marked superseded or resolved and points at the state page that is now true. Decisions and discussions are record pages; gotchas and playbooks are state pages, because a gotcha claims a pitfall exists now and a playbook must work against the code as it is.
_Avoid_: treating every fixed category as a record

## Understanding a change

- **Change theme**: the intent behind a set of code changes, stated as one idea ("explicit order intent replaces tradeId inference"). A theme may span many commits; one commit range may hold several themes.
- **Directly affected page**: a wiki page that documents a file the range changed.
- **Thematically affected page**: a wiki page that describes behavior a change theme altered, even though no file it documents changed. Finding these takes judgment, not file mapping.
- **Affected set**: every directly and thematically affected page for a range, including category indexes.
- **Removed name**: an identifier or file name present in the watched source at the range base and absent at the range head.
- **Staleness sweep**: a mechanical search of the wiki for removed names. It finds only the staleness that names removed code; the rest takes judgment.

## Judging an ingest run

- **Coverage**: whether every page that *should* have changed for a commit range did, or was declared a gap where that is allowed. Includes category indexes, which are pages.
- **Residual staleness**: text in the wiki that still describes code the range removed or changed. Measured per page after a run, as stale claims (the page says X; the code at the range head says Y) that remain.
- **Stale claim**: one statement on a page, presented as current, that is false at the range head. The unit residual staleness is measured in.
- **Major staleness**: a stale claim that misleads a reader about core behavior, such as a fixed defect presented as live, a removed type described as present, or a wrong data flow. A page with any major stale claim must be corrected in the run that finds it.
- **Minor staleness**: a stale claim that misleads only in detail, such as a count, an anchor or a passing mention of a removed name. A page whose stale claims are all minor may be declared a gap instead of corrected.
- **Accuracy floor**: a hard requirement that every statement a run writes is true of the code at the range head. Not traded against cost; one error fails the candidate. This is the quality bar's pass rule; choosing the knee uses **legacy parity** instead.
- **Conciseness**: a page says what a contributor needs and no more. More text is more surface for staleness, so length is not a proxy for quality in either direction; there is no length bar, only "to the point".
- **Quality bar**: the coverage, residual-staleness, accuracy and conciseness thresholds that define a fully up-to-date run on a benchmark fixture. It is the reference line on the cost–quality curve, not a gate: runs below it are still placed on the curve by their quality score.
- **Quality score (Q)**: the headline benchmark number, the weighted share of a fixture's verified stale claims that a run resolved (major ×3, minor ×1). Declared gaps earn nothing.
- **Harness screen**: the mechanical quality-bar checks run on every candidate run. Reviewer checks follow for finalists only.
- **Candidate**: an ingest design under benchmark (legacy, v2, or a prototype with its settings), invoked by the harness against an isolated clone of the project.
- **Benchmark fixture**: a fixed commit range on a fixed base wiki state, used to score every candidate ingest design the same way.
- **Gap transparency**: a run's explicit record of the pages it knew were affected but did not update.
- **Gap**: an affected page a run did not bring current, recorded with its cause. A declared gap is one kind; others come from failed or capped work.
- **Declared gap**: a gap the writer chose, naming the stale claims it leaves instead of correcting them. Allowed only for minor staleness; the page stays owed to a later run.
- **Gap record**: the committed file of pages owed, together with the run's log entry.
- **Carried gap**: a gap from an earlier run, taken on first by the next run.
- **Stuck gap**: a gap that failed three dispatched attempts. It is no longer retried automatically and needs a human.
- **Gap-only run**: a run with no new commit range that works only on carried major gaps.
- **Range split**: after repeated analysis failure, ingesting the first half of the range, so that a failing range shrinks instead of widening.
- **Silent miss**: an affected page a run neither corrected nor declared. Always fails the quality bar, whatever the page's staleness.
- **Cost–quality curve**: candidate ingest designs plotted by cost against their quality score on the benchmark fixtures. v3's design is chosen on this curve rather than as the cheapest design that clears a fixed bar.
- **Knee**: the point on the cost–quality curve past which spending more buys no justified gain in quality.
- **Legacy parity**: the accuracy standard a candidate must meet to be the knee. Its runs have no major writer-introduced error, and a writer-introduced error rate (per 100 added lines) no worse than legacy's, both measured with the same instrument. It is applied as a comparison, not an absolute count.
- **Writer-introduced error**: a factual error in text the run itself wrote, as opposed to a wrong statement carried unchanged from the base wiki.
- **Union audit**: the accuracy instrument behind legacy parity. Several independent verification passes over a run's final pages, with their findings pooled, each one confirmed against the code at the range head, and duplicates counted once. One pass alone misses too many errors to be trusted.
- **Rung**: one fully specified candidate configuration on the sweep ladder that maps the cost–quality curve.
- **Frozen brief**: an analysis brief reused unchanged across downstream sweep runs, so that writer and verification settings are compared on identical input.
- **Brief Q**: the quality score of an analysis brief alone: the weighted share of a fixture's stale claims that sit on pages the brief lists. It is the most a run built on that brief can resolve without declaring gaps.
- **Claim Q**: the stricter form of brief Q: a stale claim counts only when the brief quotes it.
- **Verification pass**: a read-only check of what a run wrote, against the code at the range head.
