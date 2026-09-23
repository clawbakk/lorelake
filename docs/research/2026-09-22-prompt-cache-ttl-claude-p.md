# Prompt-cache TTL for `claude -p` background agents

Research for [LOR-2](https://linear.app/clawbakk/issue/LOR-2) (child of the map LOR-1). Date: 2026-09-22. Installed CLI: Claude Code 2.1.280.

Context: `docs/reports/2026-09-22-ingest-legacy-vs-v2-baqqetto.md` reports that both benchmark runs (legacy $11.09, v2 $2.01, `claude-opus-5-5`) reconcile to the CLI-reported cost only at the 1-hour cache-write rate ($8/MTok), although no turn gap exceeded 90 s, and claims "CLI 2.1.280 selects the prompt-cache TTL from `CLAUDE_CODE_PROMPT_CACHE_TTL` (or `FORCE_PROMPT_CACHING_5M`), defaulting to `1h` for subscriber models."

## Summary

**Verdict on the report's claims.** The core claim is correct in effect and imprecise in wording. Claude Code does read `CLAUDE_CODE_PROMPT_CACHE_TTL` and `FORCE_PROMPT_CACHING_5M`, and it does default `claude -p` runs to the 1-hour TTL. The default is not "for subscriber models"; it is keyed on the billing path and the request bucket: a Claude subscription within its included usage gets 1h on the *main conversation* bucket (interactive, `-p`, and SDK turns). API keys, cloud providers, and subscriptions that have spilled into usage credits get 5m. The report also omits three other controls that sit in the same precedence chain (`promptCacheTtl` setting, `ENABLE_PROMPT_CACHING_1H`, and the subagent-bucket pair). The CLI-reported dollar figure does reflect real 1h writes: the CLI prices `usage.cache_creation.ephemeral_1h_input_tokens` at the 1h rate and the remainder at the 5m rate, so "$11.09 reconciles only at $8/MTok" is a valid inference that the API actually wrote 1h entries.

**Q1 — Which variables, which values, which versions.** Two per-bucket variables plus two global switches, all accepting exactly `5m` or `1h` (any other value is ignored):

- `CLAUDE_CODE_PROMPT_CACHE_TTL` — main conversation bucket, which explicitly includes `-p` runs. Equivalent setting: `promptCacheTtl` (any settings file).
- `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` — everything else (subagents, workflows, compaction, background/helper requests). Equivalent setting: `subagentPromptCacheTtl`.
- `FORCE_PROMPT_CACHING_5M=1` — forces 5m for both buckets; beats everything.
- `ENABLE_PROMPT_CACHING_1H=1` — requests 1h for both buckets; loses to everything above and only beats the bucket default. (`ENABLE_PROMPT_CACHING_1H_BEDROCK` is deprecated in favour of it.)

Precedence, first match wins: `FORCE_PROMPT_CACHING_5M` → the bucket's env var → the bucket's setting → (subagents only) `experimental.cacheTtl` agent frontmatter → `ENABLE_PROMPT_CACHING_1H` → bucket default. The env vars and settings require Claude Code v2.1.242 or later; the agent-frontmatter slot requires v2.1.248 or later. All three locally installed builds (2.1.277, 2.1.278, 2.1.280) contain the full chain. The CHANGELOG on GitHub has no entry naming these variables or the 1h default, so the exact version at which subscribers started getting 1h by default is **unconfirmed**.

**Q2 — Billing rule.** Per the pricing page: 5-minute cache writes cost 1.25× base input, 1-hour writes cost 2× base input, cache reads cost 0.1× base input (0.05× on Opus 5.5, 0.025× on Fable 5.1 / Mythos 5.1). For `claude-opus-5-5`: $4 input, $5 5m-write, $8 1h-write, $0.20 read, $20 output per MTok — the numbers the report used. The TTL is an inactivity timer: every cache hit refreshes it at no extra cost (the hit is billed at the read rate only). If the gap between two requests exceeds the TTL, the cache entry is gone; the next request finds no matching prefix, processes the whole prompt, and writes the prefix up to the last breakpoint again at the full write rate — the entire prefix, not just the tail. Note that the lifetime is measured from the *start* of the request that wrote or read the entry, so a single response that streams for four minutes leaves roughly one minute of 5m TTL for the follow-up. The 1h write premium is paid on every cache-creation token on every turn, whether or not the longer lifetime is ever used; in a run whose turns never idle past 5 minutes the extra 0.75× base input on all cache writes is pure overhead.

**Q3 — Per-invocation TTL.** Yes. The controls are environment variables read by each `claude` process, so each `claude -p` spawn can carry its own `CLAUDE_CODE_PROMPT_CACHE_TTL`. A long-running stage can run with `1h` while short stages run with `5m`; nothing is shared between processes. Within a single API conversation the API also allows a 1h breakpoint before 5m breakpoints (longer TTL must come first), but Claude Code manages breakpoints itself and exposes only the per-bucket TTL, so per-stage is the practical granularity. Inside one stage that spawns subagents, the subagent bucket has its own variable/setting and its own default (5m even on a subscription).

**Q4 — Subscription vs API billing.** The operator's CLI is signed in through claude.ai on a Max subscription with no API key configured and usage credits off (from `claude auth status` and `~/.claude.json`, no secrets read). On that path the runs are metered against the plan's 5-hour and weekly windows, not invoiced per token; the docs say plainly that the session cost figure "isn't relevant for billing purposes" for Pro/Max subscribers and that Claude Code "computes the dollar figure locally from token counts at list price". So the $11.09 / $2.01 figures are list-price estimates, and the 1h-vs-5m delta ($1.73 / $0.48) is not money the operator paid. It becomes real money in exactly two cases: (a) the plan limit is exhausted and usage credits are turned on — at which point Claude Code itself drops the main conversation to 5m unless the env var pins 1h — or (b) the pipeline is run with an API key / cloud provider, where the default is already 5m. Whether a 1h write consumes more of a subscription's included allowance than a 5m write is **unconfirmed**: the docs describe plan usage in terms of tokens and flag "cache misses" as a usage driver, but do not state how cache-write TTL maps onto plan consumption. Anthropic chose 1h as the subscriber default, which suggests they do not consider it costly for the plan, but that is inference, not a documented rule.

## Recommendation for the v3 default

1. **Set `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` on every `claude -p` the hooks spawn** (post-merge ingest, ingest-v2 planner/fixer, session-capture triage and capture). Export it in the hook next to `IS_LLAKE_AGENT=true` so it is per-invocation and needs no user settings change. Rationale: these agents run turn after turn with sub-90-second gaps, never idle, and exit when done; the 1h premium (2× vs 1.25× base input on every cache write) buys nothing. Do not use `FORCE_PROMPT_CACHING_5M`, which also stomps any operator override.
2. **Make it overridable per stage from `config.json`** (e.g. `ingest.cacheTtl`, `capture.cacheTtl`, defaulting to `5m` in `templates/config.default.json`), and let the hook export the resolved value. A future stage that legitimately pauses for more than five minutes — waiting on a lock, a batching gate, or a single response that streams for minutes — can opt into `1h` without touching the others.
3. **Re-baseline costs at the 5m rate.** Apply it to the two existing runs so every other ticket compares against the same rate: legacy $11.09 → $9.36, v2 $2.01 → $1.52 (cache-write line $4.61 → $2.88 and $1.29 → $0.81 respectively; everything else unchanged). Future benchmark runs should just run with the env var set and quote the CLI figure directly.
4. **Verify once, per the docs' own method:** run one `claude -p "hello" --output-format json` with the env var exported (and `IS_LLAKE_AGENT=true` so the plugin's hooks stay out) and confirm `usage.cache_creation.ephemeral_5m_input_tokens > 0` and `ephemeral_1h_input_tokens == 0`. Not run as part of this research (it would spend plan usage and spawn an agent; the ticket scoped local evidence to `--version` and read-only grepping).
5. **Keep the cost figures honest in the reports:** on this operator's Max plan the dollar figure is a list-price estimate, so label it as such. The 5m default is still right because it is the correct baseline for anyone running the plugin on an API key, and because it removes a 2× multiplier that would otherwise leak into the numbers if the operator's plan ever spills into usage credits.

Watch-out: with a 5m TTL a single stage whose one response streams for more than ~5 minutes (a planner emitting a 100k-token plan, for example) will expire its own cache mid-response, and the next turn pays a full re-write. That is a design constraint for LOR-1's v3 plan shape (keep individual responses short, or set that stage to `1h`), not a reason to keep 1h globally.

## Evidence

Each item gives the fact, then the source. Quotes are verbatim.

### Docs: Claude Code prompt-caching page

Source: https://code.claude.com/docs/en/prompt-caching (fetched 2026-09-22).

- Buckets: "Claude Code decides the TTL per request, and every request falls in one of two fixed buckets: **Main conversation**: your interactive turns, non-interactive `-p` runs, and Agent SDK turns, plus the helpers Claude Code runs inline with them. **Everything else**: the requests Claude Code makes outside that conversation, such as subagents, workflows, in-process teammates, forks, compaction, and session titles".
- Default: "Unless you choose a TTL yourself, Claude Code requests the one-hour TTL only on a Claude subscription within your plan's included usage. There it requests the hour for the main conversation, plus a small set of helper requests that Anthropic controls server-side." Table: Main conversation — "One hour" (subscription, within plan usage) vs "Five minutes" (usage credits, API key, or cloud provider); Everything else — "Five minutes, except the server-controlled helper requests, which get one hour" vs "Five minutes".
- Overage: "Once you go over your plan's usage limit and Claude Code draws on usage credits, you are billed for that usage, so Claude Code drops the main conversation to the cheaper five-minute TTL. To keep the one-hour TTL there, choose the TTL yourself."
- Accepted values: "You can set a TTL for either bucket. Each control takes `5m` or `1h`, and Claude Code ignores any other value."
- Controls: "**Main conversation**: the `promptCacheTtl` setting, or the `CLAUDE_CODE_PROMPT_CACHE_TTL` environment variable. **Everything else**: the `subagentPromptCacheTtl` setting, or the `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` environment variable. Both settings and both environment variables require Claude Code v2.1.242 or later."
- Precedence: "When more than one control applies, Claude Code takes the first match in this order: 1. `FORCE_PROMPT_CACHING_5M=1`, which forces five minutes for both buckets 2. The bucket's environment variable 3. The bucket's setting 4. For a subagent's requests, the `cacheTtl` value in the subagent's `experimental` frontmatter field, which requires Claude Code v2.1.248 or later. Claude Code ignores a `1h` there while your Claude subscription is using usage credits 5. `ENABLE_PROMPT_CACHING_1H=1`, which requests one hour for both buckets 6. The default for the request's bucket".
- Verification: "To confirm which TTL your main conversation's cache writes used, run `claude -p "hello" --output-format json` and read `usage.cache_creation` in the result. Claude Code reports one-hour cache writes under `ephemeral_1h_input_tokens` and five-minute cache writes under `ephemeral_5m_input_tokens`."
- Lifetime semantics: "Cached prefixes expire after a period of inactivity. Each request that hits the cache resets the timer, so the cache stays warm as long as you keep working. After a long enough gap, the next request recomputes the full input and re-establishes the cache".
- Cost framing: the one-hour TTL "costs more on short bursts of work that never idle past five minutes, where the higher write rate applies and the longer cache lifetime goes unused."
- Subagents: "Subagents fall outside the main-conversation TTL bucket, so they get five minutes even on a subscription until you choose a longer one."

### Docs: environment-variable reference

Source: https://code.claude.com/docs/en/env-vars (markdown mirror at `/docs/en/env-vars.md`, fetched 2026-09-22).

- `CLAUDE_CODE_PROMPT_CACHE_TTL`: "Set `5m` or `1h`, the only values Claude Code accepts, to choose the prompt cache TTL for the main conversation: your interactive, `-p`, and SDK turns, plus the helpers that run inline with them. Takes precedence over the `promptCacheTtl` setting and over `ENABLE_PROMPT_CACHING_1H`, and `FORCE_PROMPT_CACHING_5M` overrides it. The API bills 1-hour cache writes at a higher rate. Requires Claude Code v2.1.242 or later".
- `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`: same text for "requests outside the main conversation, such as subagents, workflows, and background work" and the `subagentPromptCacheTtl` setting; also v2.1.242+.
- `ENABLE_PROMPT_CACHING_1H`: "Set to `1` to request a 1-hour prompt cache TTL instead of the default 5 minutes. Intended for API key, Amazon Bedrock, Google Cloud's Agent Platform, Microsoft Foundry, and Claude Platform on AWS users. Subscription users within included usage receive the 1-hour TTL automatically on the main conversation. Subscription users drawing on usage credits can set it to keep the 1-hour TTL."
- `ENABLE_PROMPT_CACHING_1H_BEDROCK`: "Deprecated. Use `ENABLE_PROMPT_CACHING_1H` instead".
- `FORCE_PROMPT_CACHING_5M`: "Set to `1` to force the 5-minute prompt cache TTL even when 1-hour TTL would otherwise apply. Overrides `CLAUDE_CODE_PROMPT_CACHE_TTL`, `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`, `ENABLE_PROMPT_CACHING_1H`, and the `promptCacheTtl` and `subagentPromptCacheTtl` settings".

### Docs: settings reference

Source: https://code.claude.com/docs/en/settings-reference#promptcachettl (markdown mirror fetched 2026-09-22).

- `promptCacheTtl`: "This key applies to your interactive, `-p`, and Agent SDK turns, together with the helpers Claude Code runs inline with them. ... Requires Claude Code v2.1.242 or later." Scope "Any file"; type string `"5m"` or `"1h"`; "Default: unset, so each main-conversation request gets its default lifetime". Per-session overrides: "`FORCE_PROMPT_CACHING_5M` takes precedence over everything else, then `CLAUDE_CODE_PROMPT_CACHE_TTL`, then this key, and last `ENABLE_PROMPT_CACHING_1H`".
- `subagentPromptCacheTtl`: same shape for subagents, workflows, "and Claude Code's own background and helper requests, such as compaction and session titles".

### Docs: API prompt caching and pricing

Sources: https://platform.claude.com/docs/en/build-with-claude/prompt-caching and https://platform.claude.com/docs/en/about-claude/pricing (fetched 2026-09-22).

- Multipliers: "5-minute cache write tokens are 1.25 times the base input tokens price; 1-hour cache write tokens are 2 times the base input tokens price; Cache read tokens are 0.1 times the base input tokens price (see the table footnote for per-model exceptions)". Footnotes: "Cache hits and refreshes on Claude Opus 5.5 are priced at 0.05x the base input price"; "on Claude Fable 5.1 and Claude Mythos 5.1 ... 0.025x".
- Opus 5.5 row of the model pricing table: "$4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok | $20 / MTok" (base input, 5m write, 1h write, cache hit, output). Sonnet 5: $2 / $2.50 / $4 / $0.20 / $10. Haiku 4.5: $1 / $1.25 / $2 / $0.10 / $5. Fable 5.1: $10 / $12.50 / $20 / $0.25 / $50.
- Break-even: "caching pays off after one cache read for the 5-minute duration (1.25x write), or after two cache reads for the 1-hour duration (2x write)".
- Refresh: "The cache is refreshed for no additional cost each time the cached content is used." And: "The lifetime is measured from the start of the request that writes or reads the cache entry, not from the end of its response. Time spent generating a response counts against the lifetime: if a response takes 4 minutes to stream, a follow-up request that reuses the same cached prefix must start within about 1 minute of that response completing."
- Miss behaviour: "The system checks if a prompt prefix, up to a specified cache breakpoint, is already cached from a recent query. If found, it uses the cached version ... Otherwise, it processes the full prompt and caches the prefix once the response begins." Usage fields: "`cache_creation_input_tokens`: Number of tokens written to the cache when creating a new entry" (i.e. the whole newly-written prefix); "`cache_creation` object ... `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`" and "`cache_creation_input_tokens` field equals the sum of the values in the `cache_creation` object".
- Mixing TTLs: "Cache entries with longer TTL must appear before shorter TTLs (that is, a 1-hour cache entry must appear before any 5-minute cache entries)." Billing split: "Cache read tokens for `A`. 1-hour cache write tokens for `(B - A)`. 5-minute cache write tokens for `(C - B)`." Per-request `ttl` via `"cache_control": {"type": "ephemeral", "ttl": "1h"}`.
- When to use 1h: "When you have prompts that are likely used less frequently than 5 minutes, but more frequently than every hour. ... When you want to improve your rate limit utilization, because cache hits are not deducted against your rate limit." (API rate limits, not subscription plan limits.)

### Docs: Claude Code costs page

Source: https://code.claude.com/docs/en/costs (fetched 2026-09-22).

- "The Session block in `/usage` shows API token usage and is intended for API users. Claude Max and Pro subscribers have usage included in their subscription, so the session cost figure isn't relevant for billing purposes."
- "Claude Code computes the dollar figure locally from token counts at list price, unless a `modelPricing` table is in effect. ... The figure is an estimate, so for authoritative billing see the Usage page in the Claude Console."
- "Cache misses: your first message after a break longer than the cache lifetime misses the cache and reprocesses your full context. The lifetime is an hour on a subscription and drops to five minutes once you're drawing on usage credits; on an API key or cloud provider, it's five minutes by default."
- `/usage` on a plan flags "behaviors such as long context or cache misses, flagged when one accounts for 10% or more of recent usage."

### Changelog

Source: https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md (fetched 2026-09-22; newest header `## 2.1.280`). No entry mentions `CLAUDE_CODE_PROMPT_CACHE_TTL`, `FORCE_PROMPT_CACHING_5M`, `ENABLE_PROMPT_CACHING_1H`, `promptCacheTtl`, `cacheTtl`, or a 1-hour cache default. The v2.1.242 / v2.1.248 minimums above come from the docs pages, not the changelog.

### Local evidence: installed CLI

- `claude --version` → `2.1.280 (Claude Code)`; binary at `/Users/a-andrew/.local/share/claude/versions/2.1.280` (Mach-O arm64, bun standalone). Sibling builds present: 2.1.277, 2.1.278.
- `grep -a -c` on the 2.1.280 binary: `CLAUDE_CODE_PROMPT_CACHE_TTL` 6 hits, `FORCE_PROMPT_CACHING_5M` 5, `DISABLE_PROMPT_CACHING` 29, `CLAUDE_CODE_DISABLE_1H_CACHE` 0 (that name does not exist). Same identifiers are present in 2.1.277 and 2.1.278.
- The embedded settings schema (verbatim from the binary): `promptCacheTtl` — "Prompt cache TTL for the main conversation (interactive, -p and SDK turns, plus the helpers that run inline with it): "5m" or "1h". Unset = automatic: 1 hour on a Claude subscription within its usage limits, 5 minutes on an API key, Bedrock, Vertex or Foundry. 1-hour cache writes are billed at a higher rate; the cache stays warm across longer breaks. The CLAUDE_CODE_PROMPT_CACHE_TTL environment variable takes precedence." `subagentPromptCacheTtl` — "... Unset = automatic (5 minutes unless ENABLE_PROMPT_CACHING_1H=1). The CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL environment variable takes precedence." Both validated against the enum `["5m","1h"]` with `.catch(void 0)`, i.e. an invalid value is treated as unset.
- The resolver, minified but readable (identifiers renamed here for clarity):

  ```js
  MAIN_BUCKET = ["repl_main_thread*", "sdk", "auto_mode", "memdir_relevance"];
  function explicitTtl(querySource, agentFrontmatterTtl, inOverage) {
    if (env.FORCE_PROMPT_CACHING_5M) return {ttl:"5m", reason:"force_5m_env"};
    const isMain = matches(querySource, MAIN_BUCKET);
    const e = isMain ? env.CLAUDE_CODE_PROMPT_CACHE_TTL : env.CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL;
    if (e !== undefined) return {ttl:e, reason:"env"};
    const s = isMain ? settings.promptCacheTtl : settings.subagentPromptCacheTtl;
    if (s !== undefined) return {ttl:s, reason:"setting"};
    if (agentFrontmatterTtl !== undefined && !(agentFrontmatterTtl === "1h" && inOverage))
      return {ttl:agentFrontmatterTtl, reason:"agent_frontmatter"};
    if (env.ENABLE_PROMPT_CACHING_1H || (provider() === "bedrock" && env.ENABLE_PROMPT_CACHING_1H_BEDROCK))
      return {ttl:"1h", reason:"enable_1h_env"};
  }
  function resolveTtl(querySource, {agentCacheTtlOverride, ignoreOverage = false} = {}) {
    const subscriber = isClaudeAiSubscriber();
    const inOverage = subscriber && !ignoreOverage && rateLimitState().isUsingOverage === true;
    const h = explicitTtl(querySource, agentCacheTtlOverride, inOverage);
    if (h !== undefined) return h;
    if (!subscriber || inOverage) return {ttl:"5m", reason:"default"};
    const allowlist = cachedRemoteAllowlist() ?? remoteConfig("tengu_prompt_cache_1h_config", {allowlist:[...MAIN_BUCKET]}).allowlist ?? [];
    return matches(querySource, allowlist) ? {ttl:"1h", reason:"subscriber"} : {ttl:"5m", reason:"default"};
  }
  ```

  `claude -p` runs use `querySource:"sdk"` (two call sites in the bundle), which is in `MAIN_BUCKET`, so a subscriber's `-p` run resolves to `{ttl:"1h", reason:"subscriber"}` unless something earlier in the chain fires. The 1h allowlist is remote-configurable (`tengu_prompt_cache_1h_config`), which matches the docs' "small set of helper requests that Anthropic controls server-side". Each request's `prompt_cache_ttl` / `prompt_cache_ttl_reason` is emitted in the CLI's `api_request` telemetry event.
- Cost computation in the bundle (renamed for clarity): `writeCost = min(usage.cache_creation.ephemeral_1h_input_tokens, cache_creation_input_tokens) × promptCacheWrite1hTokens + remainder × promptCacheWriteTokens`. So the CLI's reported `total_cost_usd` prices 1h and 5m writes separately, driven by what the API reports — the report's "reconciles only at $8/MTok" inference is sound. The catalog entry for `claude-opus-5-5` carries `pricing:"tier_4_20_cache_read_0_20"` ($4 / $20 / $0.20), consistent with the pricing page.
- Agent frontmatter: the subagent schema exposes `experimental.cacheTtl` — "Prompt cache TTL for this agent's requests ("5m" or "1h") when no `subagentPromptCacheTtl` setting or env var is set. "1h" is ignored while a Claude subscription is in overage."

### Local evidence: operator billing path and plugin spawn sites

- `claude auth status` (2.1.280): `authMethod: "claude.ai"`, `apiProvider: "firstParty"`, `subscriptionType: "max"`. `~/.claude.json` (only key names / flags inspected): `oauthAccount.billingType = stripe_subscription`, `hasExtraUsageEnabled = False`, no `primaryApiKey`, and `ANTHROPIC_API_KEY` is not set in the shell. No `PROMPT_CACH*` variables are set in the shell.
- Spawn sites that inherit this: `hooks/post-merge.sh:371` (`claude $MODEL_FLAG $EFFORT_FLAG -p "$INGEST_PROMPT" ... --max-budget-usd ... --output-format stream-json`), `hooks/lib/ingest-v2.sh:128` (planner) and `:259` (fixer), and `hooks/lib/session-capture-worker.sh:270` (triage) and `:363` (capture). None export a cache-TTL variable; they export `IS_LLAKE_AGENT=true` and `LLAKE_AGENT_ID`, which is where a `CLAUDE_CODE_PROMPT_CACHE_TTL` export would go.
- Benchmark report, `docs/reports/2026-09-22-ingest-legacy-vs-v2-baqqetto.md`: cache-write tokens v2 161,570 ($1.29), legacy 575,669 ($4.61); "At the 5-minute cache-write rate the legacy run would have cost $9.36 (−$1.73) and v2 $1.52 (−$0.48). Turn gaps in both runs never exceeded 90 s." Re-checked: 161,570 × $5/MTok = $0.81 and 575,669 × $5/MTok = $2.88, so the deltas are $0.48 and $1.73 as stated.

## Open questions

- **Which CLI version introduced the subscriber 1h default.** The docs put the env vars/settings at v2.1.242+ and the frontmatter slot at v2.1.248+, but the changelog has no line for any of it and nothing states when subscribers began receiving 1h by default. Unconfirmed; it does not matter for the operator (all local builds are ≥ 2.1.277).
- **Plan-usage cost of a 1h write vs a 5m write.** No primary source says whether a 1h cache write draws more of a Pro/Max allowance than a 5m write. The 5m recommendation stands either way; if it turns out to be neutral for plan usage, the only remaining reason for 5m is baseline honesty for API-key installs and the usage-credits case.
- **Remote allowlist drift.** The main-bucket 1h allowlist is fetched from remote config, so Anthropic can change which query sources get 1h without a CLI release. Setting the env var explicitly removes that variability from the benchmark baseline, which is one more argument for pinning it in the hooks.
- **Live confirmation.** The docs' own check (`claude -p "hello" --output-format json` → `usage.cache_creation`) was not executed here. Running it once with and without `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` would close the loop empirically; expected result is `ephemeral_1h_input_tokens > 0` unset and `ephemeral_5m_input_tokens > 0` with the var set.
