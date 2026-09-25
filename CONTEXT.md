# LoreLake — Domain Glossary

Terms used when discussing LoreLake's writers and how their output is judged. Implementation details do not belong here.

## Writers

- **Ingest**: the writer that updates the wiki after code changes land on the monitored branch. Runs unattended.
- **Ingest run**: one execution of ingest over one commit range.
- **Commit range**: the set of commits between the ingest cursor and the merged head, filtered to the paths the project asked ingest to watch.
- **Ingest cursor**: the last commit ingest has fully accounted for. Advances only when a run produced a trustworthy result.
- **Batching gate**: the rule that decides whether a merge triggers an ingest run now or lets changes pile up.

## Understanding a change

- **Change theme**: the intent behind a set of code changes, stated as one idea ("explicit order intent replaces tradeId inference"). A theme may span many commits; one commit range may hold several themes.
- **Directly affected page**: a wiki page that documents a file the range changed.
- **Thematically affected page**: a wiki page that describes behavior a change theme altered, even though no file it documents changed. Finding these takes judgment, not file mapping.
- **Affected set**: every directly and thematically affected page for a range, including category indexes.

## Judging an ingest run

- **Coverage**: the share of wiki pages that *should* have changed for a commit range and did. Includes category indexes, which are pages.
- **Residual staleness**: text in the wiki that still describes code the range removed or changed. Counted per page after a run.
- **Accuracy floor**: a hard requirement that every statement a run writes is true of the code at the range head. Not traded against cost; one error fails the candidate.
- **Conciseness**: a page says what a contributor needs and no more. More text is more surface for staleness, so length is not a proxy for quality in either direction; there is no length bar, only "to the point".
- **Quality bar**: the coverage, residual-staleness, accuracy and conciseness thresholds a run must clear on a benchmark fixture. Cost is compared only between runs that clear the bar.
- **Benchmark fixture**: a fixed commit range on a fixed base wiki state, used to score every candidate ingest design the same way.
- **Gap transparency**: a run's explicit record of the pages it knew were affected but did not update.
