### Bug fix that IS a gotcha
Commit: `fix: handle race when subsystem A receives duplicate events from external source`
Diff reveals: the external source emits events twice under a specific condition; the fix adds a deduplication step.
**Semantic value**: teaches a non-obvious behavior of an external dependency and a concrete mitigation — domain knowledge that will bite the next person touching this integration.
Action: update the relevant subsystem page; create or update a `gotchas/` entry naming the behavior and mitigation; link both to any related ADR.

### Bug fix that is NOT a gotcha
Commit: `fix: typo in environment variable name CONFIG_VALUE_X`
Diff reveals: a single-char rename across one config file and one import.
**Semantic value**: negligible — no new knowledge, no class of error, nothing a future reader gains from documenting.
Action: correct any wiki references to the old spelling. No new page.

### Multi-category commit
Commit: `feat: add safety circuit breaker to processing pipeline`
Diff reveals: a new threshold config key, a new pre-execution check in the pipeline, and a related test.
**Semantic value**: a new safety control with a specific trigger condition; a new integration point in the pipeline; a new configuration knob.
Action: update the safety/risk-related page (the control), the pipeline page (the integration point), the configuration page (the knob); cross-link with `[[wikilinks]]`. Create an ADR in `decisions/` if tradeoffs are evident.

### Implementing an existing decision
Commit: `feat: implement idempotency keys for operation X`
Diff reveals: implementation matching `decisions/adr-NNN-idempotent-operation-x.md`.
**Semantic value**: the code now reflects a previously-decided design. The decision becomes implemented reality; the implementation needs to point back to the "why."
Action: update the page describing operation X; add `[[adr-NNN-idempotent-operation-x]]` to its `related:`; update the ADR's `related:` to link back to the implementation page. Do NOT create a new ADR.

### Refactor with NO new pages
Commit: `refactor: move ServiceY from src/services/ to src/managers/y/`
Diff reveals: file moves and import updates. No behavior change.
**Semantic value**: no new knowledge. Only code references change.
Action: update any wiki pages with `file:line` references to the old path. Do not create new pages.
