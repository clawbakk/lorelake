# Ingest quality bar

The bar that defines a fully up-to-date ingest run on a benchmark fixture, and the fixture format it is scored against. Candidates are placed on a cost–quality curve by their quality score (the weighted share of verified stale claims resolved, major ×3, minor ×1). This bar is the curve's reference line. The design is chosen at the knee: the point past which more cost buys no justified quality gain. A candidate with a confirmed factual error cannot be the knee.

This is a developer tool for choosing between ingest designs. It is not shipped to projects that install LoreLake. Terms in **bold** are defined in [`CONTEXT.md`](../../CONTEXT.md).

## What the bar protects

The wiki is the project's documentation and knowledge base, and ingest keeps it current. After a run, every **state page** should be true of the code at the range head: stale content corrected, new facts added, changed facts updated. **Record pages** (decisions, discussions) are not rewritten; when the code moves past one, it is marked and linked to the state page that is now true.

Ingest describes what the change means for the project's state. It does not summarize diffs or commit messages, and a candidate must not depend on commit messages being detailed.

## Pass rule and cost

- A candidate runs **twice** on each fixture. **Both runs must clear every check below**; one failed check fails the run, and one failed run fails the candidate on that fixture.
- **Cost** is the mean CLI-reported cost of the two runs, with the prompt-cache TTL at 5 minutes. It is the cost axis of the cost–quality curve.
- Wall clock and plan-usage movement (the stream's `rate_limit_event` utilization before and after) are recorded next to cost but not scored. A run that hits the ingest timeout fails.

## Page tiers

A fixture sorts every page a run is expected to act on into one tier:

| Tier | Which pages | The run must |
|---|---|---|
| **must-correct** | Pages the fixture names as a baseline requirement, plus every state page with at least one **major** stale claim | Correct the page |
| **correct-or-declare** | State pages whose stale claims are all **minor**; record pages the range moved past | Correct the page, or name it as a **declared gap** |
| **history-only** | Pages that mention changed code only as correctly framed history | Nothing; editing them is allowed and scored like any other edit |

Any page not listed is unscored for coverage, but everything a run writes on it is scored for accuracy and conciseness.

A **silent miss** (a listed must-correct or correct-or-declare page the run neither corrected nor declared) fails the run.

A record page is "corrected" by setting `status:` (`stale` or `deprecated`) and adding a dated note that says what superseded or resolved it and links the state page that is now true. Its body is not rewritten. Gotchas and playbooks are state pages: a gotcha whose pitfall the range removed must be marked resolved, and a playbook must work against the code at the range head.

## Checks

Each check says who performs it: the **harness** (mechanical, every run) or the **reviewer** (human, finalists only; see [Scoring procedure](#scoring-procedure)).

### 1. Coverage

- Every must-correct page was changed. *(harness)*
- Every correct-or-declare page was changed or appears in the gap record. *(harness)*
- No silent misses. *(harness)*

### 2. Residual staleness

- On every page the run touched, every **stale claim** the fixture lists for that page is gone or corrected. The harness flags claims whose quoted text is still present verbatim; the reviewer confirms the rest. *(harness, then reviewer)*
- On every state page the run touched, the fixture's deleted-name search returns zero hits. Mentions of removed code belong on record pages and gotchas, not on state pages. *(harness)*

### 3. New facts

Every entry in the fixture's new-facts list appears in the wiki, on its home page or on a page the home page links to. *(reviewer)*

### 4. Indexes

After the run, for every category index:

- every row's summary equals its page's `description:`;
- pages the run created or deleted are added to or removed from their index;
- the page count is right;
- the category blurb is still true of the category. *(harness for the first three, reviewer for the blurb)*

Only problems the run introduced count. Base wikis carry preexisting index drift, and a run is not failed for drift it did not cause.

Every page the run changed has its `updated:` date set to the run date. *(harness)*

### 5. Accuracy floor

Only text the run wrote (lines its diff added) is scored; errors in text it left alone are not the run's.

An **error** is a statement presented as current that is false at the range head: a wrong symbol, behavior, default, count or flow, or a `path:line` anchor whose line (±2) does not hold the named symbol. A historical statement is an error only if the history is false.

- Every anchor in added lines is checked. *(harness flags; the reviewer confirms a flag before it counts as an error, since the ±2-line match is heuristic)*
- At least 5 behavioral claims per must-correct page and 2 per other touched page are checked against source at the range head, chosen from sections that touch a change theme. *(reviewer)*

**One confirmed error fails the run.**

### 6. Conciseness

Words are not counted. There is no length bar in either direction.

- **Fact checklist (floor).** For each page the fixture gives a fact checklist for, every fact is present in some form. *(reviewer)*
- **Padding flags (ceiling).** On every touched page, the reviewer flags:
  - change narration on a state page ("this range removed…", "previously…");
  - notes about removed code on a state page that is not a gotcha;
  - content duplicated from another page instead of linked;
  - prose that restates a code block line by line.

  **More than 2 flags on one page fails the run.** *(reviewer)*

### 7. Gap transparency

The run's gap record names every declared gap with the stale claims it leaves, in a form the next run can act on. A declared gap on a must-correct page is a coverage failure, not a gap. *(harness for presence and tier, reviewer for actionability)*

## Scoring procedure

1. **Screen.** Every candidate run goes through the harness checks. Exploratory variants are compared on these alone.
2. **Finalists.** The two or three candidates still in contention get the reviewer checks on both runs on every fixture.
3. **Verdict.** A candidate clears the bar when both of its runs pass every check on every fixture. Its cost is then compared with the other candidates that cleared it.

An LLM judge may replace reviewer checks later, once enough human verdicts exist to calibrate it.

## Fixture format

A fixture is a directory. It is kept outside the plugin repository when its content comes from a private project, and the harness takes its path as an argument. Nothing in the format depends on a particular language, tracker or project layout.

```
<fixture>/
  README.md             provenance: how the tiers and claims were established, and by whom
  fixture.json          the range and how to reset to it
  affected-set.json     every scored page, its tier and its stale claims
  facts.json            fact checklists
  new-facts.json        facts the range introduced
```

`fixture.json`:

```json
{
  "name": "big",
  "project_repo": "<path to a local clone>",
  "range": { "base": "<sha>", "head": "<sha>" },
  "base_wiki_commit": "<sha whose llake/wiki is the starting wiki>",
  "include": ["src/", "scripts/"],
  "deleted_names": ["<symbol removed by the range>", "..."]
}
```

`affected-set.json`:

```json
{
  "pages": [
    {
      "page": "<category>/<slug>",
      "kind": "state | record",
      "tier": "must-correct | correct-or-declare | history-only",
      "why": "baseline | major | minor | record | history",
      "claims": [
        {
          "id": "<page slug>#<n>",
          "severity": "major | minor",
          "says": "<short quote from the page at the base wiki>",
          "head": "<what the code at the range head shows, with path:line>"
        }
      ]
    }
  ]
}
```

`facts.json`: `[{ "page": "<category>/<slug>", "fact": "<one fact a contributor needs, true at head>" }]`

`new-facts.json`: `[{ "fact": "<a fact the range introduced>", "home": "<category>/<slug>" }]`

Every stale claim and every tier assignment in a fixture must have been checked against source at the range head, not taken from an agent's analysis unverified.
