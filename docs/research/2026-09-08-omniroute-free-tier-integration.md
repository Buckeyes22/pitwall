# Research: Porting OmniRoute's Free-Tier Machinery into Pitwall

- **Date:** 2026-09-08
- **Status:** Implemented; see the plan at
  `docs/superpowers/plans/2026-09-10-free-tier-gateway-integration.md`. ADR 0007
  (`docs/decisions/0007-free-tier-gateway-tos-posture.md`) records the ToS posture
  and avoid-list contract.
- **Reference checkout analyzed:** `~/git/OmniRoute` (upstream
  `diegosouzapw/OmniRoute`, v3.8.51, MIT).
- **Companion artifact for the personal-serving prong:** `docs/research/2026-08-27-serve-model/`.

---

## 1. Executive summary

Pitwall's destination is a **three-pronged personal AI infrastructure**:

1. **Prong 1 — Subagent routing (exists).** Agent Routing dispatches whole units of agentic
   work across coding-CLI harnesses backed by subscriptions the operator already pays for.
2. **Prong 2 — Free/low-cost model gateway (the gap this document designs).** An
   OmniRoute-style layer that catalogs free-tier and cheap API providers, keeps live quota
   state, and routes requests so coding "never stops" while spending ~$0.
3. **Prong 3 — Self-serve compute (exists).** `pitwall serve` / leases spin up TTL'd,
   price-capped vLLM/llama.cpp/SGLang pods; the operator pays only for compute.

The central finding of this deep dive: **OmniRoute's compounding asset is its catalog, not
its code.** The free-tier catalog (`open-sse/config/freeModelCatalog.data.ts`, 325-line
typed schema + data, biweekly re-audit) is the part that appreciates over time. The
routing/resilience runtime is good but largely replaceable — and Pitwall already owns
better-disciplined equivalents for more of it than expected (see §5, including a
correction to the earlier analysis).

Recommended strategy, in one line: **sync the data, own a thin runtime slice, wrap
everything in Pitwall's existing safety rails.**

Concretely:

- **Port to Python (small, high-leverage):** catalog-extraction seeds, a gateway provider
  adapter, quota-awareness in the planner (stage 2 gates + stage 3 factors), per-model
  lockout, an OmniRoute-derived `strict zero cost` filter, and the tier-cascade fallback
  convention.
- **Adopt as a stripped fork (Phase 3, optional but likely):** the `open-sse` streaming
  engine (protocol translation, provider executors for the OpenAI-compatible subset,
  compression engines, quota fetchers) as `packages/gateway` — an independently packaged
  component mirroring the existing `packages/agent-routing` pattern (own lockfile, own CI,
  own release tags, excluded from broker wheels and images).
- **Delete, never port:** stealth/fingerprint impersonation, MITM/TPROXY, Radar and all
  monetization, affiliate anything, key-pool rotation for free tiers, web-cookie account
  reuse, Electron, the Next.js dashboard, OmniRoute's MCP/A2A/skills/memory agent stack.

The single most important design rule throughout: **the budget gate and kill-switch stay
above all three prongs**, and free routing inherits Pitwall's fail-closed posture rather
than OmniRoute's fail-open one.

---

## 2. Vision: the three-pronged end state

### 2.1 What the operator experience looks like when done

```
coding agents (Claude Code, Codex, Copilot CLI, OpenCode, …)
      │  configured once via agent-routing plugin bundles / route-shim
      ▼
agent-routing shims ── route profiles ──▶ seats
  ┌──────────────────┬───────────────────────────┬────────────────────────┐
  │ seat: sub        │ seat: gateway             │ seat: local            │
  │ (prong 1)        │ (prong 2)                 │ (prong 3)              │
  │ existing paid    │ pitwall /v1 front door    │ pitwall serve pods     │
  │ subscription     │  ├─ keyless free pool     │ (vLLM, llama.cpp,      │
  │ harnesses        │  ├─ budgeted free pools   │  SGLang; TTL +         │
  │                  │  ├─ cheap metered APIs    │  max-usd-per-hour cap) │
  │                  │  └─ packages/gateway      │                        │
  │                  │     (forked open-sse)     │                        │
  └──────────────────┴───────────────────────────┴────────────────────────┘
      ▼ every prong behind:
  planner S1 hard constraints → S2 health+cooldown+QUOTA → S3 score(+quota
  headroom, +reset proximity) → S4 capacity
  → Decimal budget gate ($0 admission for `zero` kind; 402 for paid over cap)
  → kill-switch above everything (network-level, <30 s)
```

The planner's fallback chain *is* the tier cascade. Example capability:

```yaml
capability: coding.chat
fallback_chain:
  - provider: local-serve-ornith      # prong 3: own pod, per_second
  - provider: gw-opencode-free        # prong 2: keyless, zero
  - provider: gw-cloudflare-ai        # prong 2: budgeted free pool, zero
  - provider: gw-deepseek             # prong 2: cheap metered, per_token
```

"Never stop coding" emerges from composition: quota-aware ordering keeps request N+1 off
pools about to exhaust; per-model lockout keeps one bad model from poisoning a connection;
the terminal fallback is the always-on keyless floor; and when even that fails, prong 3
can provision its own compute — gated by budget, not hope.

### 2.2 Design principles (non-negotiable)

1. **Pitwall stays the single front door.** Shims and API clients point at Pitwall; the
   gateway prong is a provider *behind* the planner, never a second control plane in the
   request path.
2. **Fail-closed everywhere** (ADR 0001/0005 posture). Unknown quota = ineligible for
   `zero` claims, not "probably free". Unverifiable = excluded.
3. **No trust-boundary dilation.** The forked gateway runs loopback, token-required,
   fail-closed boot, no embedded secrets, no outbound telemetry. If a feature can't meet
   Pitwall's security lanes, it is deleted, not configured off.
4. **Free is a pricing fact, not an aspiration.** A provider is `zero`-priced only when
   the catalog's evidence class supports it (`hardStopGuaranteed` semantics, §4.1);
   everything else is metered `per_token` behind the budget gate, or excluded.
5. **The catalog is synced, never hand-maintained.** Upstream's biweekly audit is their
   maintenance burden; our job is extraction + drift detection, not curation.
6. **ToS respect is data-driven.** The avoid-list ships as seed metadata and defaults to
   excluded. We do not build multi-account or rotation tooling for free tiers.

---

## 3. What OmniRoute actually is, mechanically (verified)

Grounding for every porting decision below. File references verified against the
v3.8.51 checkout.

### 3.1 The free-tier catalog — the moat

Location and shape (verified):

- `open-sse/config/freeModelCatalog.ts` (325 lines) — typed schema.
- `open-sse/config/freeModelCatalog.data.ts` — `FREE_MODEL_BUDGETS`, the actual rows
  (444 entries / 34 recurring pool keys per docs).
- `open-sse/config/freeTierCatalog.ts` — `TosVerdict` source.
- `open-sse/services/autoCombo/strictZeroCostFilter.ts` — STRICT_ZERO_COST filter.

`FreeModelBudget` row fields (transcribed from source):

```ts
{
  provider: string;          // e.g. "cloudflare"
  modelId: string;
  displayName: string;
  monthlyTokens: number;     // pool-deduped contribution to the steady headline
  creditTokens: number;      // signup/monthly credit grants
  freeType: "recurring-daily" | "recurring-monthly" | "recurring-credit"
          | "recurring-uncapped" | "one-time-initial" | "keyless" | "discontinued";
  poolKey: string | null;    // dedupe key — shared pools counted once
  tos: TosVerdict;           // "avoid" | "caution" | "ok" (approximate union)
  trainsOnPrompts?: boolean; // privacy cost surfaced next to quota
  hardStopGuaranteed?: boolean; // curated: exceeding allowance = hard refusal,
                                // NOT automatic pay-as-you-go. Undefined/false =
                                // "not guaranteed". Never defaulted to true.
  eligibilityGate?: "regional-identity"; // e.g. mainland-China ID check; counting-only
}
```

The totals object keeps four separately-named sums so marketing can't inflate the
headline: `steadyRecurringTokens` (pool-deduped), `steadyWithRecurringCreditsTokens`,
`firstMonthRealisticTokens` (+ one-time credits), `boostMonthlyTokens` (deposit-unlocked,
reported separately). This evidence-class discipline is the part worth copying wholesale —
including the comments that threaten contributors who default `hardStopGuaranteed: true`.

Provider-level `hasFree`/`freeNote` metadata lives separately in
`src/shared/constants/providers/{noauth,oauth,apikey}/…` (the 356-provider registry,
Zod-validated at load via `src/shared/validation/providerSchema.ts`).

Keyless tier (`providers/noauth.ts`): providers requiring no credential at all (OpenCode
Free, Pollinations, …). These are ordinary OpenAI-compatible HTTP endpoints — trivially
portable to any runtime, and they are the "works the second you install it" floor.

### 3.2 Quota machinery

- ~60 per-provider quota fetchers in `open-sse/services/` (e.g. `grokQuotaFetcher.ts`,
  `alibabaFreeTierQuotaFetcher.ts` + classify/types modules).
- `quotaPreflight.ts` / `quotaMonitor.ts` — pre-dispatch checks and periodic sampling.
- Reset-aware routing strategies (`reset-window`, `reset-aware`, `headroom`) consume
  quota-window state; the 16-factor scorer weighs quota and cost inversely.
- Per-model 429s feed **model lockout** (`open-sse/services/accountFallback.ts`,
  `recordModelLockoutFailure`: reason- or exact-scoped, exponential backoff 120 s →
  30 min cap, `quota_exhausted` locks until midnight) so one exhausted model never
  disables a whole connection.

### 3.3 Auto-combo scoring (verified weights, `open-sse/services/autoCombo/scoring.ts:41–87`)

16 factors, weights summing to 1.0: health 0.1605, quota 0.1429, costInv 0.1429,
latencyInv 0.1143, taskFit 0.0762, quality 0.03, seven factors at 0.0476 each;
`cacheAffinity`, `resetWindowAffinity`, `reliability` computed but non-voting (weight 0).
Mode packs (`modePacks.ts`) replace weight vectors wholesale (e.g. `auto/cheap` → cost
dominant). 5% bandit exploration on `auto`, 10% on `auto/smart`, disabled when >50% of
providers are down. Self-healing: score < 0.2 → 5-minute exclusion, max 30 exclusions.

Pitwall-relevant translation: of the 16 factors, Pitwall's `scoring.py` already computes
close analogues of 5 (health/error, latency/cold-start, cost, warm capacity, priority)
and needs only the quota-adjacent ones (§9.3).

### 3.4 Resilience layers (thresholds from `docs/architecture/RESILIENCE_GUIDE.md`)

| Layer | Scope | Trip | Reset |
|---|---|---|---|
| Circuit breaker | whole provider | OAuth 5/8, API-key 7/12, local 2 failures; only 408/5xx | 60/30/15 s → HALF_OPEN probe |
| Connection cooldown | one key/account | base 5 s (OAuth) / 3 s (API-key), ×2 exponential, honors Retry-After | lazy (read-path refresh) |
| Model lockout | provider+key+model | 429/404/mode denials; 120 s base → 30 min cap | success-decay halves count |

Pitwall already implements provider-level cooldown (3 failures → 5 min; trips ≥2 → 15
min) inside planner stage 2. The genuinely missing piece is the third layer's granularity
(per-model, not per-connection) and quota-window awareness as a *first-class* input.

### 3.5 Compression

`open-sse/services/compression/`: RTK (filters/codeStripper/deduplicator/smartTruncate),
Caveman (`caveman.ts` + rule packs + language packs), LLMLingua-2 (optional native dep),
stacked ladder RTK(pri 10) → Caveman(pri 20) in `adaptiveCompression/ladder.ts`. Measured:
Caveman ~46% in / ~65% out; RTK 60–90%; stacked average 89.2% (78–95%). Applied in
`chatCore.ts` phases (Lite → Standard/Caveman → Aggressive) before dispatch.

Strategic value for prong 2: compression multiplies every free quota by 2–10×, which is
the difference between a 30 M-token/month pool being a toy or a workhorse. It lives in
the request path of the TS engine — i.e., it comes along free with the fork and should
NOT be reimplemented in Python.

### 3.6 Translation hub and executors

Hub-and-spoke around OpenAI format (`open-sse/translator/`: `claude-to-openai`,
`openai-to-gemini`, `openai-responses`, `openai-to-cursor`, `openai-to-kiro`, …).
`getExecutor()` (`open-sse/executors/index.ts:217`) lazily resolves ~108 executors,
defaulting to `DefaultExecutor` (plain OpenAI-compatible HTTP). The web-cookie/OAuth
executors (claude-web, chatgpt-web, cursor, codex OAuth flows) are the ToS-hot part —
out of scope for the fork's keep-list (§10.2), except where an operator's own paid
subscription legitimately exposes an API key.

### 3.7 What must never be ported

- **Stealth:** wreq-js TLS fingerprint impersonation, exact CLI header/body fingerprints,
  Claude Code integrity-hash reimplementation, zero-width-joiner insertion to hide
  competitor client names, Antigravity credit evasion.
- **MITM/TPROXY** transparent decrypt.
- **Radar**, affiliate links, referral injection, gamification.
- **Key pools / rotation for free tiers** and any multi-account ergonomics.
- **Web-cookie session reuse** (claude-web/chatgpt-web style providers).
- OmniRoute's MCP server (110 tools), A2A/skills/memory/cloud agents (Pitwall has its own
  agent stack; duplicated agent frameworks inside the trust boundary is pure surface).

---

## 4. Correction and re-baseline vs the earlier analysis

Two corrections from grounding against source (the earlier conversational analysis was
wrong in both directions):

1. **`STRICT_ZERO_COST`, `excludeTosAvoid`, `hardStopGuaranteed` are OmniRoute features to
   port, not existing Pitwall features.** They live in
   `open-sse/services/autoCombo/strictZeroCostFilter.ts` + catalog fields. Pitwall has the
   *posture* (fail-closed, Decimal-exact) but not this mechanism. §9.5 specifies the port.
2. **Pitwall's routing layer is richer than previously credited.**
   `src/pitwall/routing/` already contains: `arbitrage.py`, `canary.py`, `carbon.py`,
   `cascade.py`, `coalescing.py`, `constraints.py`, `cooldown.py`, `failover.py`,
   `fallback.py`, `hedging.py`, `openai.py`, `planner.py`, `prewarm.py`, `production.py`,
   `quality_routing.py`, `saturation.py`, `scoring.py`, `semantic_cache.py`. Hedging,
   canary, cascade, prewarm, coalescing, semantic cache — features OmniRoute advertises as
   differentiators — exist here as modules today. The *actual* routing-layer gaps are
   narrow: quota-window state, reset-awareness in scoring, per-model lockout, and the
   free-tier data to feed them.

Also verified as existing Pitwall assets the plan can lean on:

- `cost/estimator.py`: `ZeroOrEnergyPricing` with `kind: Literal["zero"]` (plus
  `gpu_hour`, `per_request`, `per_second`, `per_token`, …) — the `zero` pricing kind is
  real and already produces $0 components.
- `providers/interface.py`: full protocol family — `InferenceProvider` (sync `infer`),
  `AsyncInferenceProvider`, `AvailabilityProvider` (whose `AvailabilityKind` already
  includes `MODEL`, anticipating model-shaped resources), `ActualCostProvider`,
  `ComputeProvider`; `CredentialReference` env-var pattern (names, never values);
  `resolve_adapter_credentials()` at the adapter boundary.
- `providers/together.py` and `providers/selfhosted/` — prior art for API-shaped and
  own-endpoint adapters; `providers/registry.py` for registration.
- OpenAI proxy at `/v1/openai/{capability}/v1/{path}` with provider fallback chains and
  SSE relay (SDLC 02).
- `serve.py` + `personal/` — the no-database personal path that `docs/research/
  2026-08-27-serve-model/` feeds; agent-routing registration already happens there
  (`routes add … --api-key-env PITWALL_ENDPOINT_KEY --seat local`).

This re-baseline matters for sizing: **prong 2 is a provider adapter + a data pipeline +
two planner factors + one filter, not a routing engine.**

---

## 5. Feature mapping: OmniRoute → Pitwall

| OmniRoute capability | Pitwall today | Disposition |
|---|---|---|
| Free-tier catalog (444 rows, pool dedupe, avoid-list, evidence classes) | nothing | **Sync as data** → seeds + `provider_quotas` (§8, §9.2) |
| Keyless providers (noauth) | nothing | Port — plain OpenAI-compatible endpoints (§9.1) |
| Quota fetchers (~60) / quota preflight / telemetry | nothing | Keep in fork (§10); Python side polls fork's telemetry |
| Reset-aware / headroom / quota scoring | `scoring.py` has cost/latency/health/warm/priority terms | **Port factors** into `explain_score` (§9.3) |
| Per-model lockout | cooldown is provider-scoped | **Port** tuple-scoped lockout (§9.4) |
| STRICT_ZERO_COST filter | fail-closed posture only | **Port semantics** (§9.5) |
| Tier cascade / emergency-free fallback | `fallback.py` chains, `failover.py`, `cascade.py` | Convention: chains over `zero` tier + terminal keyless floor (§9.6) |
| 16-factor auto scorer | 5-term explainable scorer + mode-less | Selective factor port; do NOT clone the engine |
| Compression (RTK/Caveman/stacked) | nothing | Keep in fork only (§10.2) |
| Protocol translation (Claude/Gemini/Responses) | OpenAI-shaped proxy | Keep in fork; Python surface stays OpenAI-compatible |
| Circuit breakers / cooldowns | provider cooldown in S2 | Already covered; add lockout granularity |
| Web UI free-tier dashboards | Textual TUI + Prometheus | Re-surface as TUI views + exporter metrics (§12) |
| 253-provider executor zoo | 4 adapters (runpod/vast/together/lambda) | Fork keeps zoo; Python registry stays curated |
| Stealth / MITM / Radar / affiliates / Electron / MCP / A2A / gamification | — | **Delete / never port** (§3.7) |

---

## 6. Integration options, evaluated

### Option A — stock OmniRoute as unmodified sidecar (Phase 0 only)

Run upstream as a compose service on loopback; register one pitwall provider row pointing
at its `/v1`; point one agent-routing route at it.

- ✅ Zero code. Validates free-tier reality (latency, uptime, actual caps) in days using
  their own benchmark methodology (`tests/integration/free-models-tpm-stress.test.ts`).
- ❌ Trust surface: default `CHANGEME` dashboard password, opt-in-only guardrails,
  embedded public creds, Radar/affiliate/stealth compiled in. Node ≥22 runtime. Catalog
  freshness tied to their npm release cadence. Runs as a second control plane with its own
  auth, DB, and dashboard — directly violates design principle #1 long-term.
- **Verdict: throwaway validation harness.** Never the end state.

### Option B — fork as `packages/gateway` (recommended end state)

Slimmed OmniRoute → independent Node component in-repo, mirroring `packages/agent-routing`
pattern: own `package.json`/lockfile, own `gateway-ci.yml`, `gateway/v*` tags, excluded
from broker wheels and all five service images (exactly as SDLC 23 documents for Agent
Routing). Keep: `open-sse/` (self-contained, no Next deps) + a thin OpenAI-compatible
route shim. Strip: §10.2 list.

- ✅ Own the compounding runtime pieces (translation, compression, quota fetchers) under
  Pitwall's security posture and release control; catalog still synced from upstream so
  the fork stays shallow (config + shim changes only).
- ❌ Real fork maintenance; Node toolchain in the monorepo; npm supply-chain review
  burden (SBOM, lockfile lint — reuse upstream's own notices as a starting inventory).
- Sizing: keep-list is roughly 15–20% of the monorepo by surface (open-sse is ~1,400
  files, but most of the weight we care about is translators/executors/compression).

### Option C — pure-Python selective port

Port only: seeds, adapter, planner factors, lockout, zero-cost filter. No Node anywhere.

- ✅ Cleanest trust boundary; single runtime; smallest long-term surface; perfectly
  aligned with the "stdlib-lean" brand.
- ❌ Caps prong 2 at a curated ~15–30 provider pool. No compression multiplier, no
  translation beyond OpenAI shape, no quota fetchers — every provider quirk becomes
  hand-written Python that upstream already maintains.
- **Verdict: acceptable floor, wrong ceiling.** Ships as Phase 1–2 by design; the fork in
  Phase 3 is what buys breadth.

### Decision matrix

| Criterion | A sidecar | B fork | C python-only |
|---|---|---|---|
| Time to first value | hours | weeks | 1–2 weeks |
| Trust-boundary discipline | poor | good (after strip) | best |
| Provider breadth | max | max | curated subset |
| Compression multiplier | yes (borrowed) | yes (owned) | no |
| Maintenance burden | their releases | shallow fork | grows linearly w/ providers |
| Alignment w/ ADR 0001/0005 | violates | aligns | aligns |
| Reversibility | trivial | medium | trivial |

**Recommendation:** A now (throwaway), C's cheap parts next (they're prerequisites for B
anyway), B when prong 2 proves itself. At no point is A in the request path of anything
the operator relies on.

---

## 7. Target architecture in depth

### 7.1 Component view

```
┌─────────────────────────── pitwall broker (Python 3.14) ───────────────────────────┐
│ surfaces: REST /v1 (incl. /v1/openai/* model-id proxy) · CLI · TUI · MCP (stdio)   │
│ planner: S1 constraints → S2 health+cooldown+lockout+quota → S3 score → S4 capacity│
│ cost: TaggedPricingModel (zero | per_token | …) → BudgetGate (advisory lock)       │
│ new: gateway adapter (providers/gateway.py) · catalog sync (scripts/) · quotas     │
│ reconciler: + quota poll loop · exporter: + free-pool metrics                      │
└───────────────┬────────────────────────────────────────────────────────────────────┘
                │ OpenAI-compatible HTTP, loopback, bearer token
┌───────────────▼──────────── packages/gateway (Node, forked) ───────────────────────┐
│ route shim (thin /v1/*) · translators (hub: OpenAI) · executors (OpenAI-compatible │
│ subset + keyless) · compression (RTK/Caveman stacked) · quota fetchers · telemetry  │
│ hardened: token-required, loopback-only, fail-closed boot, no dashboard/no Radar    │
└───────────────┬────────────────────────────────────────────────────────────────────┘
                │ HTTPS to providers
        [ keyless free · budgeted free pools · cheap metered APIs ]
```

### 7.2 Request flows

**Free-pool inference (registry mode):**
`POST /v1/inference {capability: coding.chat}` → resolver → planner S1 eliminates
non-gateway providers for this capability only if constraints say so → S2 checks provider
health, cooldown, per-model lockout, and quota eligibility (pool budget remaining,
reset proximity) → S3 scores (existing terms + `quota_headroom`, `reset_proximity`) →
budget gate: `zero` pricing → $0 admission, no cap friction → gateway adapter `infer()`
→ fork translates if needed, applies compression per policy, dispatches → response
recorded as workload with actual cost $0 → usage accounting still counts *tokens* (quota
burn-down) even when dollars are zero.

**Model-id proxy flow (for shims that want OpenAI shape):**
`POST /v1/openai/{capability}/v1/chat/completions {model: "gw/glm-5.2-flash"}` →
model-id → (capability, provider) mapping table → same pipeline → SSE relay (existing
proxy machinery).

**Personal (no-DB) mode:** `pitwall serve` gains an optional `--gateway` flag that
supervises the fork as a child process (start/stop/health, local state file pattern from
`personal/`), so prongs 2+3 work without Postgres. Quota state degrades to the fork's
in-process view; planner quota gates become best-effort (documented).

### 7.3 Failure-mode walkthrough (the "never stop" claim, stress-tested)

| Failure | Mechanism | User-visible |
|---|---|---|
| Free pool exhausts mid-request | executor 429 → adapter maps to quota signal → model lockout on that tuple → fallback chain advances | retry lands on next pool; sub-second |
| Pool exhausted for the window | S2 quota gate marks ineligible until `reset_at` | traffic pre-emptively shifts; no 429s at all |
| Keyless floor is down | provider cooldown (existing 3-fail → 5 min) + chain descends to cheap tier | budget gate admits small per_token spend under caps |
| Cheap tier over cap | BudgetGate 402 (existing) | terminal, explicit — by design, spend stops |
| Gateway fork process dies | adapter health probe fails → provider marked unhealthy → chain descends to prong 1/3 | degraded, not stopped |
| Everything external down | prong 3: `serve` provisions own pod (budget-gated) | operator pays compute only — the final fallback |

Note the deliberate asymmetry with OmniRoute: their terminal fallback is *someone else's
free tier*; ours is *your own budget-gated compute*. That's the prong-3 payoff.

---

## 8. Catalog sync pipeline (data design)

### 8.1 Extraction

Script `scripts/ops/sync_gateway_catalog.py` (placeholder path; follow repo script
placement rules):

1. Input: an upstream OmniRoute **npm release** (not git) — install to an isolated prefix,
   import `open-sse/config/freeModelCatalog.data.ts` + provider registry output. (The TS
   is importable via a small node one-liner emitting JSON; the script shells out once,
   hermetically, pinned by version + integrity hash recorded in the seed header.)
2. Transform to two artifacts:
   - `seed/gateway-capabilities.yaml` — one capability per free/cheap model family.
   - `seed/gateway-providers.yaml` — provider rows in the existing seed shape (below).
   - `config/gateway-catalog.json` — full-fidelity copy (tos verdicts, trainsOnPrompts,
     poolKey, freeType, eligibilityGate) consumed by the planner gates and the fork.
3. Dedupe rules inherited verbatim: shared `poolKey` counts once; `recurring-uncapped`
   never summed; `eligibilityGate` rows flagged, never admitted by the zero-cost filter.

Target provider row shape (extends the verified seed schema):

```yaml
providers:
  - name: gw-opencode-free
    capability: coding.chat
    endpoint_id: opencode-free          # validated by existing SSRF regex
    provider_type: openai_gateway
    region: GLOBAL
    priority: 50
    cost:
      mode: zero                        # ZeroOrEnergyPricing — exists today
    gateway:
      base_url: https://…               # loopback fork in front of upstream
      model_id: oc/…
      catalog:
        free_type: keyless
        tos: caution
        trains_on_prompts: true
        hard_stop_guaranteed: true
        pool_key: null
  - name: gw-deepseek
    capability: coding.chat
    provider_type: openai_gateway
    cost:
      mode: per_token                   # PerTokenPricing — exists today
      per_million_input: "0.27"
      per_million_output: "1.10"
    gateway:
      base_url: http://127.0.0.1:20130  # the fork
      model_id: deepseek-chat
      catalog:
        free_type: recurring-monthly
        monthly_tokens: 5000000
        pool_key: deepseek-free
        tos: ok
```

### 8.2 Drift gate

`pitwall-checks` style CI job (mirroring the existing catalog-drift pattern): recompute
headline totals from our extracted seeds; compare against upstream's published
`FREE_TIERS.md` numbers and our last-synced snapshot; fail on divergence beyond
tolerance (any avoid-list change = hard fail; count changes = warn + refresh PR).

### 8.3 Provenance

Every sync commit records: upstream version, tarball integrity, extraction script hash,
and diff summary. This feeds `docs/provenance/` and the fork's SBOM update. First sync
produces a `docs/legal/` review entry covering the MIT attribution chain actually shipped
(fork subset only — dropping wreq-js/stealth also drops its BoringSSL/ICU4X/Mozilla-CA
notice burden).

---

## 9. Pitwall-side engineering plan (Python)

### 9.1 `providers/gateway.py` — the adapter

Implements `InferenceProvider` + `AvailabilityProvider` (+ `AsyncInferenceProvider` only
if a provider needs it; most free tiers are sync):

- `id: "openai_gateway"`; `credential_schema`: `GatewayCredentials` (api_key optional —
  keyless providers validate an empty-string-tolerant model; keyed providers go through
  `CredentialReference` env names exactly like existing adapters).
- `pricing_model()` returns `ZeroOrEnergyPricing` or `PerTokenPricing` from the provider
  row — no new pricing kinds needed (verified: both exist in `cost/estimator.py`).
- `infer()` = single OpenAI-compatible POST through the existing SSRF-validated URL
  resolution path; maps upstream 429 → typed `QuotaExhausted` signal consumed by the
  lockout layer; 5xx → existing cooldown path.
- `availability()` returns `AvailabilityItem(kind=MODEL)` rows from the catalog (the
  protocol already anticipates `AvailabilityKind.MODEL` — verified).
- Registration in `providers/registry.py`; provider types `openai_gateway` (+ optional
  `_free` marker for seed tooling only — behavior keys off pricing kind, not the name).

### 9.2 Migrations (next in sequence; sketch)

```sql
CREATE TABLE provider_quotas (
  provider_id  TEXT NOT NULL,
  pool_key     TEXT,
  free_type    TEXT NOT NULL,           -- recurring-monthly | keyless | …
  window_start TIMESTAMPTZ,
  reset_at     TIMESTAMPTZ,
  budget_units TEXT,                    -- tokens or requests; Decimal-safe text
  used_units   TEXT NOT NULL DEFAULT '0',
  tos_verdict  TEXT NOT NULL,           -- avoid | caution | ok
  evidence     JSONB NOT NULL DEFAULT '{}',
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (provider_id, pool_key)
);
CREATE TABLE provider_quota_samples (   -- rollup source for TUI/exporter
  provider_id TEXT NOT NULL,
  sampled_at  TIMESTAMPTZ NOT NULL,
  used_units  TEXT NOT NULL,
  reset_at    TIMESTAMPTZ,
  PRIMARY KEY (provider_id, sampled_at)
);
CREATE TABLE model_id_map (             -- proxy model-id routing
  model_id    TEXT PRIMARY KEY,
  capability  TEXT NOT NULL,
  provider    TEXT NOT NULL
);
```

Idempotency, workloads, cost rollups: unchanged. Token usage on `zero` workloads is
recorded in the existing usage surfaces (dollars $0, tokens real) — quota burn-down reads
the same rows.

### 9.3 Planner: quota factors in S2/S3

Stage 2 gains two gates (fail-closed defaults):

```python
def _quota_eligible(provider, quotas, now) -> tuple[bool, str | None]:
    q = quotas.get((provider.id, provider.pool_key))
    if provider.pricing_kind != "zero":
        return True, None  # metered: budget gate owns it
    if q is None:
        return False, "zero-priced without catalog evidence"  # fail closed
    if q.tos_verdict == "avoid":
        return False, "tos-avoid"
    if q.budget_units is not None and q.used_units >= q.budget_units:
        return False, "quota-exhausted"
    return True, None
```

Stage 3 `explain_score()` gains two terms (keeping the existing deterministic,
explainable shape — every term inspectable in the plan):

```python
quota_headroom = remaining / budget  # 1.0 fresh → 0.0 at exhaustion
reset_proximity = max(0, 1 - minutes_to_reset / window_minutes)
# score += w_quota * quota_headroom + w_reset * reset_proximity
```

Weights start conservative (`w_quota = 10.0`, `w_reset = 2.5` on the 100-point base) and
are tunable per capability like `priority_multiplier`. Both terms are pure functions of
`PlanningContext` — byte-replayable plan identity is preserved (plan-ID hash includes the
quota snapshot, so a plan is replayable only against the snapshot that produced it; the
existing `PlanningContext.replay()` contract dictates snapshot immutability).

### 9.4 Per-model lockout (tuple-scoped)

Mirror `accountFallback.ts` semantics at Pitwall scale, in `routing/cooldown.py` or a new
`routing/lockout.py`:

- Key: `(provider_id, model_id)` (+ optional reason scope).
- Backoff: `min(120s * 2^(failures-1), 30min)`; `quota_exhausted` → lock until
  `reset_at`; success halves the failure count (decay).
- In-memory authoritative (like OmniRoute: lost on restart, settings persist) but with a
  periodic flush to `provider_quotas.evidence` for observability only — reconciler never
  resurrects lockouts.
- The 429-classifier ports OmniRoute's duration semantics: daily quota → 24 h, permanent
  ban marker → operator-visible terminal state (never auto-cleared; no ban-evasion
  tooling, by principle #6).

### 9.5 Strict zero-cost filter (the OmniRoute port, renamed)

`routing/zero_cost.py`: a provider may serve a `zero`-priced plan **only if** catalog
evidence shows `hard_stop_guaranteed` OR `free_type == "keyless"`; `tos == "avoid"`
excluded; `eligibility_gate` rows excluded from zero routing (counting-only upstream
becomes routing-excluded here — stricter, deliberately); `trains_on_prompts` surfaced in
plan explanation and TUI so the privacy cost of "free" is visible at selection time, not
in a footnote. Anything unverifiable is demoted to `per_token`-with-unknown-pricing →
which the estimator then fails closed on (missing cost = ineligible, existing behavior).

### 9.6 Tier cascade as convention + emergency descent

- Seed generator emits fallback chains in ladder order by default: own-serve → keyless →
  budgeted free → cheap metered (mirroring OmniRoute's SUBSCRIPTION_LADDER rungs,
  re-ordered for our prongs).
- Emergency-free descent: when a chain's paid/metered rungs are budget-blocked or
  cooldown-locked, planner may descend into the keyless floor — the inverse of OmniRoute's
  emergency fallback, landing on *our* verified-free tier rather than unknown providers.
- Prong-3 escape hatch: chain-terminating rule may propose (never auto-execute — Autopilot
  shadow discipline) a `serve` provision when free+cheap are exhausted: "all free pools
  exhausted until HH:MM; own-pod at $X/hr would cover the gap — `pitwall serve …`".

### 9.7 Surface changes

- API: `/v1/models`-style catalogue read (model_id_map), quota read endpoints
  (`GET /v1/quotas`, admin-scoped refresh), plan explanations extended with quota terms.
- CLI: `pitwall gateway sync|status|doctor`; `pitwall quotas`; agent-routing
  `routes add --seat gateway --api-key-env PITWALL_API_TOKEN`.
- TUI: Providers view gains quota columns (headroom bar, reset countdown, tos badge);
  Cost view gains "free-tier burn-down" panel.
- MCP: `pitwall_gateway_catalog_read`, `pitwall_quota_list` (read-only; spend stays
  behind the same scopes as everything else).

### 9.8 Testing (extends the lanes in SDLC 17)

- Unit: quota gate truth table (every branch above), lockout backoff table, zero-cost
  filter evidence matrix, extraction transform (fixture: synthetic mini-catalog).
- Property: planner replay identity with quota snapshots; score monotonicity in headroom;
  lockout decay invariants.
- Integration (testinfra): gateway adapter against a fake OpenAI-shaped server (429/5xx
  injection); budget-gate $0 admission for zero kind; proxy model-id routing.
- Soak/benchmark: port OmniRoute's TPM-stress methodology — N dispatch/hour across a
  simulated free pool, assert zero paid leakage (cost rollups stay $0) and bounded 429
  rate (lockout working).
- Security lane: adapter credential handling (keyless must NOT require env presence);
  SSRF regex on `base_url`; no catalog URL ever dereferenced at boot.

---

## 10. `packages/gateway` fork plan

### 10.1 Packaging

Mirrors Agent Routing's independence exactly (per SDLC 23's precedent): own
`package.json` + lockfile, `gateway/v*` lightweight tags, `gateway-ci.yml` (lock, lint,
typecheck, unit, build, SBOM), excluded from broker wheels and all five service images,
never imported by the broker (HTTP/loopback only), MIT with upstream attribution.

### 10.2 Keep / strip (directory-level)

**Keep (from `open-sse/`):** `translator/` (hub + OpenAI/Claude/Gemini/Responses
subset), `executors/` (base, default, and the plain-API executors), `services/compression/`
(whole tree), quota fetchers, `services/` core (rate limiting, cache, session), `utils/`
(sanitized subset), plus a new thin route shim exposing only `/v1/chat/completions`,
`/v1/models`, `/v1/embeddings`, and a `/internal/telemetry` endpoint (token-gated).

**Strip:** entire root Next.js app (`src/`), `electron/`, `packages/browser-pool`,
`open-sse/mcp-server/`, `open-sse/vendor/` web-session reversing, all stealth utils
(fingerprinting, CCH, ZWJ), MITM/TPROXY (`src/mitm/`), Radar + affiliate + gamification
modules, cloud-agent/A2A/skills/memory, `wreq-js` (falls back to standard fetch — this
also removes the native-binding/BoringSSL notice chain), dashboard i18n, update-notifier.

### 10.3 Hardening checklist (merge precondition)

bearer token required (no anonymous mode), loopback bind default, fail-closed boot on
missing config, no embedded default secrets, rate limit on the shim, request body caps,
structured error envelope without stack traces, dependency freeze + lockfile lint +
`npm audit` gate, SBOM per release (CycloneDX like upstream), and upstream's own
error-sanitization helper retained (`buildErrorBody`) since it is genuinely good.

### 10.4 Rebranding

Name: `@pitwall/gateway` (component name "Pitwall Gateway"); UI-less so the surface is
small; docs landing page mirroring `docs/agent-routing/README.md` with the non-affiliation
disclaimer pattern (already proven for RunPod) extended to cover "not affiliated with
OmniRoute; adapted from OmniRoute (MIT), © diegosouzapw" in NOTICE + package README.

---

## 11. Legal and ToS

- **License:** MIT fork is unambiguous (verified: root LICENSE © 2026 diegosouzapw, no
  CLA). Obligations: retain copyright/permission notice (NOTICE + per-file headers in the
  kept subtree), carry `THIRD_PARTY_NOTICES.md` entries **only for deps we keep**
  (stripping wreq-js removes its chain). First release gets a fresh transitive review of
  the reduced dep tree + SBOM, following `docs/legal/transitive-license-review.md`.
- **Trademark:** no use of the OmniRoute name/mark beyond attribution comments; rebrand
  everywhere user-visible.
- **ToS posture by tier:** keyless endpoints (publicly offered, no account) — low risk;
  budgeted free pools with the operator's own single account — acceptable personal use,
  no pooling/rotation/multi-account tooling ever; avoid-list providers — excluded by
  default with the verdict visible; anything requiring client impersonation or session
  reversal — out of the fork entirely. `trainsOnPrompts` defaults conservative routing:
  prompt-privacy-sensitive capabilities can exclude training providers via a constraint.
- **ADR required** before Phase 3: `0007-gateway-fork-and-free-tier-posture.md` recording
  the strip list, the ToS rules above, and the sync-not-maintain catalog rule.

---

## 12. Observability and FinOps for free tiers

- Exporter: `pitwall_free_pool_headroom_ratio{provider,pool}`, `pitwall_free_reset_seconds`,
  `pitwall_gateway_requests_total{provider,outcome}`, `pitwall_lockout_active{provider,model}`,
  `pitwall_zero_cost_tokens_total` (yes — count free tokens so "savings" is a real number:
  `~tokens × catalog list price` reported as `pitwall_free_tokens_avoided_usd_estimate`,
  clearly labeled estimate).
- Cost rollups: `zero` workloads contribute $0 spend but real token counts; daily rollup
  gains a free-tier section (used/budget per pool, resets today, lockouts).
- TUI Operator console: quota columns + burn-down panel; kill-switch view unchanged (it
  sits above everything, including free — one switch, three prongs).

---

## 13. Risk register

| # | Risk | L×I | Mitigation |
|---|---|---|---|
| 1 | Free pools degrade/rotate faster than sync cadence | H×M | biweekly sync + drift CI + keyless floor + prong-3 escape |
| 2 | ToS enforcement against pooled/automated use | M×H | single account, no rotation tooling, avoid-list default-off, rate caps per pool |
| 3 | Fork drift from upstream makes rebases painful | M×M | shallow fork; changes confined to strip + shim; catalog via data not code edits |
| 4 | npm supply chain in trust boundary | M×H | dep freeze, lockfile lint, audit gate, SBOM, minimal keep-list |
| 5 | Quota-state wrongness routes into 429 storms | M×L | lockout backoff + pre-emptive S2 gate + TPM soak test |
| 6 | Scope creep into "rebuild OmniRoute in Python" | H×M | this document's dispositions are the contract; C-eval before any new engine code |
| 7 | Personal-mode users get silent second control plane | M×M | gateway supervised child only; `doctor` verifies single front door |
| 8 | Privacy: training-on-prompts providers leak sensitive code | M×M | `trainsOnPrompts` as constraint + plan explanation + TUI badge |

---

## 14. Phased roadmap (acceptance-gated)

**Phase 0 — Validate (days, throwaway).** Stock OmniRoute sidecar, loopback, one
agent-routing route, personal benchmark of 5–10 free pools (their TPM-stress method).
*Exit:* written dossier in `docs/research/` with real latency/uptime/cap numbers and a
keep/kill recommendation per pool.

**Phase 1 — Python floor (1–2 weeks).** Catalog extractor + seeds; `gateway.py` adapter
(keyless only); `zero` pricing through budget gate; model-id proxy mapping; agent-routing
gateway seat. *Exit:* dry-run + live keyless inference through pitwall's front door with
$0 cost rollups; unit+integration lanes green.

**Phase 2 — Quota intelligence (2–4 weeks).** Migrations; quota poll + S2 gates + S3
factors; per-model lockout; strict zero-cost filter; tier-cascade seeding; emergency-free
descent; exporter/TUI quota views. *Exit:* TPM soak test shows zero paid leakage and
bounded 429 rate across a simulated multi-pool day; planner replay identity intact.

**Phase 3 — Own the runtime (when Phase 2 is boring).** `packages/gateway` fork per §10;
ADR 0007; strip/harden/CI/SBOM; Python adapter retargets to the fork; compression on.
*Exit:* fork passes Pitwall security lanes; catalog-sync CI live; one rebase cycle
completed without manual conflict resolution beyond the strip list.

**Phase 4 — Compose the prongs (ongoing).** Cross-prong arbitrage: what-if simulator
compares own-pod $/token vs metered APIs vs free burn-down; Autopilot (shadow) proposes
seat/route switches; cache-affinity pinning for prompt-cache-heavy pools.

---

## 15. Open questions for the operator

1. Does prong 2 need Claude-native (`/v1/messages`) shape at the pitwall surface, or is
   OpenAI-shape + agent-routing translation enough for the shim fleet?
2. Personal-mode gateway supervision: child process vs require compose? (Affects `serve`
   scope.)
3. Is a curated Phase-1 provider whitelist (10–20 pools) preferred over full-catalog
   seeding for the first cut?
4. Should `pitwall free` become a first-class CLI noun (`pitwall free status/bench`) or
   stay under `gateway`/`quotas`?
5. Timing of ADR 0007: with Phase 3, or earlier to lock the ToS posture before Phase 1
   seeds ship avoid-list metadata?

---

## Appendix A — Verified source references

- OmniRoute: `LICENSE` (MIT © 2026 diegosouzapw); `open-sse/config/freeModelCatalog.ts`
  (schema, 325 lines) + `freeModelCatalog.data.ts` + `freeTierCatalog.ts`;
  `open-sse/services/autoCombo/{scoring.ts,strictZeroCostFilter.ts,modePacks.ts}`;
  `open-sse/services/accountFallback.ts`; `open-sse/services/compression/`;
  `open-sse/translator/`, `open-sse/executors/`; `src/shared/constants/providers/`
  (`noauth.ts` keyless tier); `THIRD_PARTY_NOTICES.md` (wreq-js chain).
- Pitwall: `src/pitwall/providers/interface.py` (protocol family, `AvailabilityKind.MODEL`,
  `CredentialReference`); `src/pitwall/cost/estimator.py` (`ZeroOrEnergyPricing` kind
  `"zero"`, `PerTokenPricing`); `src/pitwall/routing/` module inventory (planner stages,
  `scoring.py` terms, `cooldown.py`, `fallback.py`, `failover.py`, `cascade.py`,
  `hedging.py`, `canary.py`, `prewarm.py`, `semantic_cache.py`, `saturation.py`,
  `quality_routing.py`, `arbitrage.py`, `carbon.py`); `seed/providers.yaml` shape;
  `docs/sdlc/02-api-rest.md` (OpenAI proxy); `docs/sdlc/04-routing.md`,
  `05-cost-budget.md`, `10-reconciler-lifecycle.md`, `17-testing-strategy.md`,
  `23-agent-routing.md`; `docs/decisions/0001`–`0006`.

## Appendix B — Glossary

- **Pool / poolKey:** a provider-side shared budget counted once regardless of how many
  models draw from it (OmniRoute dedupe rule).
- **Free type:** evidence classification of a free offer (recurring-daily/monthly/credit,
  recurring-uncapped, one-time-initial, keyless, discontinued).
- **Hard-stop guaranteed:** curated fact that exceeding the free allowance refuses
  requests rather than billing — prerequisite for `zero` pricing under the strict filter.
- **Seat:** agent-routing dispatch target class (`sub`, `gateway`, `local`).
- **Prong:** one of the three capability pillars (subscription routing, free/cheap
  gateway, self-serve compute).

## Decisions (2026-09-10)

Operator answers to §15. Answering these does not select a phase as current work;
§14 phase selection remains a separate, explicit act.

- Q1 API shape -> add a Claude-native `/v1/messages` surface on Pitwall alongside the
  OpenAI-shaped one (Anthropic-shaped clients such as Claude Code hit Pitwall directly
  without shim translation; §5 row "Protocol translation" moves partly into the Python
  surface, and Phase 1 gains a second protocol contract plus tests).
- Q2 personal-mode supervision -> supervised child process (one front door; `doctor`
  verifies it; start/stop/health reuse the `serve` state-file pattern per §7 and risk 7).
- Q3 first-cut seeding -> full 444-row catalog with avoid-list metadata; eligibility is
  gated by the avoid-list and the strict zero-cost filter rather than by a curated
  whitelist (maximum coverage on day one; the Phase 0 benchmark still informs the
  avoid-list rather than the seed set).
- Q4 CLI noun -> stay under `pitwall gateway sync|status|doctor` and `pitwall quotas`;
  no first-class `pitwall free` noun (no new top-level contract to pin through the alpha).
- Q5 ADR 0007 timing -> before Phase 1, because Q3 ships avoid-list metadata in Phase 1
  seeds and the ToS posture that defines it must be a recorded decision first; the fork
  strip-list section is marked pending until Phase 3.
