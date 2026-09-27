# LoreLake — Domain Glossary

Terms used when discussing LoreLake's writers and how their output is judged. Implementation details do not belong here.

## Writers

- **Ingest**: the writer that updates the wiki after code changes land on the monitored branch. Runs unattended. It works out what the changes mean for the project's current state and brings the wiki in line with it; it does not summarize diffs or commit messages, whose level of detail varies from commit to commit.
- **Ingest run**: one execution of ingest over one commit range.
- **Commit range**: the set of commits between the ingest cursor and the merged head, filtered to the paths the project asked ingest to watch.
- **Ingest cursor**: the last commit ingest has fully accounted for. Advances only when a run produced a trustworthy result.
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

## Judging an ingest run

- **Coverage**: whether every page that *should* have changed for a commit range did, or was declared a gap where that is allowed. Includes category indexes, which are pages.
- **Residual staleness**: text in the wiki that still describes code the range removed or changed. Measured per page after a run, as stale claims (the page says X; the code at the range head says Y) that remain.
- **Stale claim**: one statement on a page, presented as current, that is false at the range head. The unit residual staleness is measured in.
- **Major staleness**: a stale claim that misleads a reader about core behavior, such as a fixed defect presented as live, a removed type described as present, or a wrong data flow. A page with any major stale claim must be corrected in the run that finds it.
- **Minor staleness**: a stale claim that misleads only in detail, such as a count, an anchor or a passing mention of a removed name. A page whose stale claims are all minor may be declared a gap instead of corrected.
- **Accuracy floor**: a hard requirement that every statement a run writes is true of the code at the range head. Not traded against cost; one error fails the candidate.
- **Conciseness**: a page says what a contributor needs and no more. More text is more surface for staleness, so length is not a proxy for quality in either direction; there is no length bar, only "to the point".
- **Quality bar**: the coverage, residual-staleness, accuracy and conciseness thresholds a run must clear on a benchmark fixture. Cost is compared only between runs that clear the bar.
- **Benchmark fixture**: a fixed commit range on a fixed base wiki state, used to score every candidate ingest design the same way.
- **Gap transparency**: a run's explicit record of the pages it knew were affected but did not update.
- **Declared gap**: an affected page a run names in its gap record, with the stale claims it leaves, instead of correcting it. Allowed only for minor staleness; the page stays owed to a later run.
- **Silent miss**: an affected page a run neither corrected nor declared. Always fails the quality bar, whatever the page's staleness.
