# Pitwall — Feature & Capability Roadmap

> **What this is:** a forward-looking roadmap for Pitwall's GPU control-plane, multi-cloud, and
> differentiated FinOps direction. The retained 2026-09-01 implementation program delivered its
> provider-neutral runtime, current four-provider contracts, RunPod control/file/onboarding,
> production routing, cost, burn-rate, guardrail, and Textual surface foundations. Aspirational
> rows below remain roadmap ideas, not an executable queue or a support claim.
> **Basis:** public provider interfaces, a source-grounded Pitwall coverage
> review, alternative GPU-provider patterns, and published broker/FinOps designs.
> **Status basis:** current support is defined by [the capability matrix](capability-matrix.md) and
> [support matrix](support-matrix.md). Provider-live countersigns remain separate from hermetic
> code completion.

## Contents
- [The thesis](#the-thesis)
- [Three tracks at a glance](#three-tracks-at-a-glance)
- [Part A — RunPod parity (close the CRUD gaps)](#part-a--runpod-parity)
- [Part B — Multi-cloud (the provider plugin)](#part-b--multi-cloud)
- [Part C — Novel features (the differentiation)](#part-c--novel-features)
- [Part D — The TUI (the unifying surface)](#part-d--the-tui)
- [Part E — Phased roadmap & decisions](#part-e--phased-roadmap)
- [Appendix — grounded references](#appendix)

---

## The thesis

Every tool in the landscape owns **one plane**: LLM gateways (LiteLLM/OpenRouter/Portkey) own *routing*; FinOps tools (Vantage/Kubecost) own *budget*; cluster orchestrators own *compute*. **Pitwall already owns all three** behind one I/O-free decision core — deterministic **given an explicit `now` + immutable capacity/provider snapshots** (today the planner defaults `now` to wall-clock, so the deterministic-replay substrate is a prerequisite for the simulator/time-machine features below) — fronted by a Postgres-advisory-lock budget gate that admits/denies *before* any spend (`routing/planner.py`, `cost/budget_gate.py`).

That fusion is the moat. The roadmap has three tracks, and they reinforce each other:
1. **Parity** — match `runpodctl`/RunPod MCP so Pitwall is a complete control plane, not just a consumer.
2. **Multi-cloud** — a provider plugin so the same routing/cost/audit machinery spans RunPod + Vast + Together + Lambda + …
3. **Novel** — features no competitor can copy without rebuilding around a pure, dry-runnable, budget-gated router (a what-if cost simulator, budget circuit breakers, cross-provider arbitrage, an autonomous autopilot).

The **TUI** is the surface that makes all three usable: a k9s-style operator console where every parity CRUD op, every provider, and every novel panel lives behind one keyboard-driven, read-only-by-default dashboard.

---

## Three tracks at a glance

| Track | What | Why now | Effort | Headline items |
|-------|------|---------|--------|----------------|
| **A · Parity** | RunPod resource control, discovery/billing, bounded files/logs, and onboarding | **Delivered hermetically**; real-account countersign pending | — | static service contracts and four operator surfaces |
| **B · Multi-cloud** | Static capability-aware adapters plus Decimal pricing for RunPod/Vast/Together/Lambda | **Delivered hermetically**; per-provider countersigns pending | — | narrow compute/inference contracts; no dynamic plugin loading |
| **C · Novel** | Budget/routing/compute fusions + moonshots | The actual moat; mostly *new signals into machinery that already exists* (many S/M) | **M–L** | what-if simulator, budget circuit breaker, arbitrage scoring, semantic cache, autopilot |
| **TUI** | Textual operator console unifying retained supported capabilities | **Built** with ten existing views and injectable sources | incremental | Overview, Providers, Leases, Models, Serve, Pods, Routes, Cost, Resources, Operations |

---

## Part A — RunPod parity

**Delivered boundary:** Pitwall exposes the retained RunPod resource operations through CLI, REST,
MCP, and TUI using one control-plane service. It does not claim parity for excluded provider
features such as savings-plan purchase, account SSH-key management, remote exec, or Hub publish.

### A.1 Coverage gap matrix
Current coverage from the audit (`src/pitwall/runpod_client/*`); target = full CRUD reachable on all surfaces.

| RunPod resource | Pitwall today | Target ops to add | Backend needed | Effort | TUI view |
|-----------------|---------------|-------------------|----------------|--------|----------|
| **Pods** | Full retained CRUD/actions on four surfaces | None in retained scope | REST v2 + bounded v1 | Delivered | Resources/Leases |
| **Serverless endpoints** | Full retained CRUD with nested workers/scaling/GPU pools | None in retained scope | REST v2 | Delivered | Resources |
| **Templates** | Account CRUD; Hub list/get/search is read-only | Hub deploy/publish excluded | REST v2 + GraphQL Hub | Delivered boundary | Resources |
| **Network volumes** | CRUD/grow plus bounded S3 object operations | No shrink; no unbounded transfer | REST v2 + S3 API | Delivered | Resources/Operations |
| **Container registry auth** | Create/list/get/delete and explicit replace | Provider has no update | REST v2 | Delivered | Resources |
| **GPU types / availability** | Cached GPU, price, minimum-bid, and availability snapshot | Live countersign pending | GraphQL + REST snapshot | Delivered hermetically | Resources |
| **Datacenters** | Cached datacenter catalogue and availability | Live countersign pending | GraphQL | Delivered hermetically | Resources |
| **Billing / credits** | Credit balance and contract-supported actual categories | Unattributable categories report unavailable | REST + GraphQL | Delivered hermetically | Cost/Resources |
| **Spot / interruptible bids** | Read-only minimum bid in market snapshot | Bid mutation/failover remains future | GraphQL | Read boundary only | Resources |
| **Savings plans** | Unavailable | Read/purchase excluded | — | Deferred | — |
| **SSH keys / secrets** | Credential references only | Account key management excluded | — | Deferred | — |
| **Pod logs / exec** | Bounded redacted log reads | Remote exec excluded | Provider-supported log path | Delivered boundary | Operations |
| **File transfer** | Bounded list/upload/download/delete with checksum/path controls | No `croc` or arbitrary stream | S3 API | Delivered hermetically | Operations |

### A.2 Integration implications (don't miss these)
- **Spot/bid pricing and live GPU prices are GraphQL-only** — REST `interruptible:true` creates spot but can't *set or read a bid*. Any cost-arbitrage feature (Part C) needs a GraphQL client for `gpuTypes`/`podRentInterruptable`. Pitwall already uses GraphQL for one read (`templates.py:108`) — extend it.
- **Running serverless work is the per-endpoint Job API** (`api.runpod.ai/v2/{id}`), while endpoint management uses the strict v2 control-plane client. Both now feed shared product services; they remain distinct provider contracts.
- **Network-volume data access is the S3 API** (separate S3 keys, datacenter-specific endpoints) — the only way to read/write volume files without booting a pod. This replaces the current "boot a pod and run a script" warm-volume hack (`cli.py:564`).
- **Container-registry-auth has no update anywhere** — model it as delete+recreate.

### A.3 Parity also fixes onboarding
The shared `runpod-onboard` workflow now plans, applies, reports status, resumes, and returns
rollback guidance across all four surfaces. Planning is the default; apply/resume require the exact
current plan id. It reuses discovered resources when compatible and records enough broker/audit
state for safe recovery without persisting credential values.

---

## Part B — Multi-cloud

**Delivered boundary:** the same routing/cost/audit/lease machinery uses static, capability-aware
RunPod, Vast.ai, Together, and Lambda Cloud adapters. Compute-only and inference-only providers do
not implement fake operations. Dynamic provider discovery remains deliberately out of scope.

### B.1 The central finding — two cost-model worlds
Candidate providers split into:
- **Per-second** (RunPod-like — estimator reuses existing math): **Vast.ai**, **Lambda**, **TensorDock**, **CoreWeave**, **Crusoe**, **Fly**, **DataCrunch**, **Hyperstack**, **Modal**, **Baseten-dedicated**.
- **Per-token / per-run** (NO RunPod analog — *breaks* the per-second estimator + budget gate): **Together** (per-M-tokens, in/out split), **Replicate** (per-output or per-second), **Fal** (per-MP / per-sec-output).

Pitwall now uses a strict **tagged pricing model** and a structured Decimal quote for both worlds.
The budget gate requires bounded seconds, tokens, units, or requests before provider invocation.

### B.2 Current static provider contracts
```python
class ProviderAdapter(Protocol):
    id: str
    credential_schema: type[BaseModel]
    capabilities: frozenset[ProviderCapability]

    def pricing_model(self, capability, provider_record): ...


class ComputeProvider(ProviderAdapter, Protocol): ...


class InferenceProvider(ProviderAdapter, Protocol): ...


class AsyncInferenceProvider(ProviderAdapter, Protocol): ...


class AvailabilityProvider(ProviderAdapter, Protocol): ...


class ActualCostProvider(ProviderAdapter, Protocol): ...
```
The static registry verifies each declared capability against its narrow runtime-checkable contract.
Credential references are resolved only at the adapter call boundary. Unsupported capability lookup
fails explicitly without relying on an adapter stub.

### B.3 Pricing-model taxonomy the estimator/gate must support
The active tags are `zero`, `gpu_hour`, `per_request`, `per_second` (with optional bid ceiling),
`per_token`, `per_vm_second`, `active_idle`, and `per_unit`. Quotes expose model, named
components, estimate, ceiling, confidence, provenance, currency, and assumptions. Reserved or
subscription pricing remains parked until a current provider contract requires it.

### B.4 Prioritized providers
| Wave | Providers | Why | Effort |
|------|-----------|-----|--------|
| **1 (delivered hermetically)** | **Vast.ai** + **Together** + **Lambda** | Compute/availability for Vast and Lambda; sync inference/availability for Together; capability-safe production reachability | Live countersigns pending |
| **2** | Replicate, Fal, TensorDock, DataCrunch, Hyperstack, Baseten | More inference + more per-second arbitrage sources; each ~2 days once the adapter pattern exists | M |
| **Aspirational** | Modal (SDK-first), CoreWeave (K8s), Crusoe/Fly (niche), AWS/GCP/Azure (heavyweight IAM) | Don't fit the clean REST mold; defer | L |
| **Avoid near-term** | Paperspace/DO | Gradient API deprecated 2024-07-15, replacement in flux | — |

Wave 1 established only contracts used by current providers. No Wave 2 adapter or speculative
preemption/failover interface was added.

---

## Part C — Novel features

The differentiation track. The recurring pattern: most ideas are **new signals/score-terms plugged into machinery that already exists** (the scorer already reads `cost_per_second_active`, `cold_start_p50_ms`, `warm_workers`, `recent_error_rate`, `priority_multiplier`; the gate already admits/denies under an advisory lock) — which is why so many are S/M, not L.

### C.1 Budget / FinOps
| # | Feature | One-liner | TUI panel | Effort |
|---|---------|-----------|-----------|--------|
| 1.1 | **Burn-Rate Forecaster** | Project spend to month-end; expose `budget.eta_to_cap` + forecast-overshoot — a *forecasted breach date* tied to a hard gate (nobody surveyed does this) | "Budget Runway" fuel-gauge w/ projected-empty timestamp | S |
| 1.2 | **Budget Circuit Breaker** | At 80/95% of cap, don't 500 — *auto-downgrade* the capability to a cheaper tier; a financial circuit breaker (cascades escalate on *quality*; nobody escalates *down* on *budget*) | "Degradation Ladder" w/ next-step $ saved | M |
| 1.3 | **Blast-Radius Sub-Budgets** | Hierarchical org→team→key pre-spend admission w/ isolation + chargeback (beyond LiteLLM flat caps); one runaway agent can't drain the org | spend treemap by org/team/key + chargeback export | M |
| 1.4 | **What-If Cost Simulator** ⭐ | Replay traces through the I/O-free planner with an explicit `now` + a captured capacity/provider snapshot (prerequisite: the deterministic-replay substrate; today `now` defaults to wall-clock) and a modified config → exact counterfactual monthly bill, zero paid calls. **The cost estimator is Decimal-exact; once replay determinism lands this is a structural moat.** | "Simulator" tab: config-diff → projected bill delta | M |
| 1.5 | **Cost SLO + Spend-Velocity Governor** | A token bucket that refills in *dollars* not requests; throttle (not deny) when spend-rate exceeds a cost SLO — marries FinOps to SRE | "Spend Velocity" speedometer + error-budget bar | S–M |
| 1.6 | **Reservation/Warm-Pool Recommender** | "Reserve 2 warm A100s → save $412/mo, break-even at 61% (you're at 78%)" from lease histograms + Decimal cost model | "Commitment Advisor" card | M |

### C.2 Compute / orchestration
| # | Feature | One-liner | TUI panel | Effort |
|---|---------|-----------|-----------|--------|
| 2.1 | **Price×Latency Arbitrage Scoring** ⭐ | A tunable arbitrage weight λ so each request picks its point on the cost/latency Pareto frontier — per-request, deterministic given fixed inputs (unlike provision-time, job-granular schedulers) | cost-vs-latency scatter w/ Pareto frontier + λ slider | S |
| 2.2 | **Hedged Multi-Provider Racing** | Fire latency-critical requests at top-2 providers, take first valid, cancel + refund loser; budget reserves worst-case up front | live "race" strip per request | M |
| 2.3 | **Demand-Forecast Prewarm** | Forecast per-capability demand (seasonality) and pre-scale `warm_workers` ahead of the curve via the reconciler — closed loop with the score signal | 24h demand-vs-warm-pool overlay | M |
| 2.4 | **Carbon-Aware Scheduling** | `carbon_intensity` per region (Electricity Maps, cached) as a soft score term for deferrable work → a cost/latency/**carbon** tri-objective router | "Grid" tiles colored by live carbon + kg-CO₂-saved | M |
| 2.5 | **Spot↔On-Demand Failover** | Checkpoint a lease on preemption, relaunch on another pool; budget-aware spot/on-demand arbitrage for broker leases | lease timeline stitching spot/on-demand segments | L |
| 2.6 | **Request Coalescing** | A short broker-side window collapses identical concurrent requests into one upstream call, charged once (extends idempotency keys) | "calls saved today / $X" counter | S–M |

### C.3 Routing / inference intelligence
| # | Feature | One-liner | TUI panel | Effort |
|---|---------|-----------|-----------|--------|
| 3.1 | **Budget-Aware Semantic Cache** ⭐ | Embedding-similarity cache *in front of* the budget gate; a hit short-circuits before spend and protects the cap; org-shared (GPTCache/Portkey are per-app) | "Cache" hit-rate dial + "$ protected from cap" | M |
| 3.2 | **Complexity-Based Cascade Routing** | Cheap model first; escalate to a stronger capability on low confidence — cascade routing (45–85% cost cut) as a *first-class broker capability* sharing one gate + fallback engine | "Cascade" sankey w/ % escalated per tier | M–L |
| 3.3 | **Quality-Aware Routing** | Offline LLM-judge over sampled traces → per-(capability,provider) win-rate as a `quality_score` term; continuous measured quality as a *routing* input | per-capability scorecard w/ win-rate CIs | M |
| 3.4 | **Shadow / Canary + Auto-Promote** | Mark a provider `shadow`/`canary X%`; auto-promote on win-rate+error thresholds, auto-rollback otherwise | "Rollout" board (Argo-Rollouts-style) | M |
| 3.5 | **Org-Wide Prompt/Embedding Dedup** | Content-hash exact-match dedup before the gate, single-charge (tenant-scoped) | "dedup saved N calls / $X" tile | S |

### C.4 Reliability / governance
| # | Feature | One-liner | TUI panel | Effort |
|---|---------|-----------|-----------|--------|
| 4.1 | **Policy-as-Code** (spend+routing+safety) ⭐ | One OPA/Rego-style engine evaluated at the pre-spend audit gate — unifies spend policy + routing policy + safety policy that competitors split across 3 tools | "Policies" list + deny-counts + simulator | L |
| 4.2 | **GitOps for Capabilities/Providers** | Capabilities/providers/budgets/policies as versioned YAML reconciled into the DB; PR = review, revert = rollback | "Config Drift" declared-vs-live diff | M |
| 4.3 | **Automated Chaos / Kill-Drills** | Scheduled fallback + kill-switch drills (slice-isolated, dry-run-first); asserts fallback fires within SLA | "Resilience" panel + MTTR-to-fallback | M |
| 4.4 | **Pre-Spend Guardrails** | PII/secret scanning on prompts as audit checks 17+ — provider-agnostic, enforced *before* spend across all downstream providers | "Guardrails" blocked/redacted feed | M |

### C.5 Observability / intelligence
| # | Feature | One-liner | TUI panel | Effort |
|---|---------|-----------|-----------|--------|
| 5.1 | **Recommendations Engine** | Replays traces through the simulator trying config perturbations, ranks by $ saved per quality/latency lost → "switch X to Y, save $312/mo, +40ms" with one-click GitOps apply | "Recommendations" inbox | M |
| 5.2 | **Cost×Latency×Quality Scorecards** | One pane fusing the three planes per (capability, provider); radar chart; "this provider is Pareto-dominated — retire it?" | sortable scorecard grid + radar | S–M |
| 5.3 | **Provider Drift Detection** | Change-point detection on each provider's latency/error/win-rate → auto-raise cooldown or re-canary | "Drift" timelines w/ change-point markers | M |
| 5.4 | **Broker Copilot** | An MCP agent that reads scorecards/recs/drift and *proposes* config changes as GitOps PRs (never auto-applies) | "Copilot" chat dock + proposed-changes tray | M |

### C.6 Moonshots
| # | Feature | One-liner | Effort |
|---|---------|-----------|--------|
| 6.1 | **Autonomous Autopilot** ⭐ | A closed loop (bandit/Bayesian) that continuously co-optimizes routing weights + warm-pool sizes + budget allocations against declared SLOs, *within* policy rails, every action dry-run-validated and kill-switchable. Feasible **only** because every Pitwall action is already dry-runnable + budget-gated + kill-switchable (and deterministically replayable once the deterministic-replay substrate is in place) — that safety substrate is why Pitwall can attempt this when generic tools can't. | L |
| 6.2 | **Real-Time Cross-Provider Spot Market** | Per-lease live re-bidding across providers on a price×latency×carbon frontier, with checkpoint-migrate (2.5) when a cheaper bid appears + hysteresis to avoid thrash | L |
| 6.3 | **Counterfactual "Time-Machine"** | Productize 1.4: versioned trace archive + pure planner = exact, auditable "what would Q1 have cost under contract B?" for audits/negotiations | M–L |

### C.7 Top 7 bets & the killer synergy
Ranked by (value × differentiation ÷ effort): **1.2** Budget Circuit Breaker · **1.4** What-If Simulator · **2.1** Arbitrage Scoring · **3.1** Budget-Aware Semantic Cache · **1.3** Blast-Radius Sub-Budgets · **4.1** Policy-as-Code · **6.1** Autopilot.

**Build 1.4 (the simulator) first — it's the load-bearing primitive under half the roadmap:** it makes recommendations (5.1) trustworthy, lets policies (4.1) be tested before they brick prod, and gives autopilot (6.1) a dry-run sandbox. The second cluster is the **tri-objective router** (2.1 arbitrage + 2.4 carbon + 3.3 quality), turning the scorer from cost/latency into a cost×latency×quality×carbon optimizer no surveyed competitor can claim.

---

## Part D — The TUI

The Textual TUI is the human surface for retained parity and multi-cloud operations. It uses
k9s-style navigation, is read-only by default, requires typed confirmation for destructive/spend
operations, and shares feature-local services with REST, MCP, and CLI.

### D.1 Information architecture (views)
A k9s-style multi-view console; `?` opens binding-derived help, `:` opens a keyboard command palette for views/actions, and `/` transiently filters the active supported list. `Escape` restores focus (and clears an active filter); `q` quits and `r` refreshes.

No-spend validation is documented in the [serve-model quickstart](operator/serve-quickstart.md).

Self-hosted OpenAI-compatible endpoints are an alpha provider profile on `public_endpoint`:
health, cold starts, revocable residency, slots, warm-up, metadata, zero-or-energy cost, and
local-inventory fit are in scope; host orchestration is not.

| View | Surfaces (parity + novel) | Read-only / actionable |
|------|---------------------------|------------------------|
| **Overview** | fleet health: active leases, Budget Runway (1.1), provider health, recent kill-log, audit status, spend velocity (1.5) | read-only |
| **Providers** | list/register/enable/disable/hibernate; health; **cost×latency×quality scorecards (5.2)**; drift markers (5.3); register-from-RunPod **and** other clouds (Part B) | actionable (type-to-confirm) |
| **Capabilities** | list/create/update; **cascade config (3.2)**; routing weights; quality scorecards | actionable |
| **Pods / Leases** | live leases; launch/renew/stop/teardown; **start/stop/reset (A.1)**; **spot↔on-demand timeline (2.5)**; logs/exec | actionable, danger-styled teardown |
| **Endpoints** | **serverless CRUD + scaling config (A.1)**; hibernate; scaler type/idle/flashboot | actionable |
| **Templates** | **CRUD + Hub browse/deploy (A.1)** | actionable |
| **Volumes** | **network-volume CRUD + S3 file browser (A.1)** | actionable, danger-styled delete |
| **Registry** | **container-registry-auth CRUD (A.1)** | actionable |
| **Catalog** | **live GPU types + price + bid + availability (GraphQL), datacenters (A.1)** | read-only |
| **Cost** | Budget Runway (1.1), **sub-budget treemap + chargeback (1.3)**, **what-if Simulator (1.4)**, **Recommendations inbox (5.1)**, billing actuals, reservation advisor (1.6) | read-only + "apply via PR" |
| **Routing** | **arbitrage Pareto frontier + λ (2.1)**, **cascade sankey (3.2)**, **cache hit-rate (3.1)**, hedge races (2.2), carbon grid (2.4) | read-only + tuning |
| **Policies** | **policy-as-code list + simulate (4.1)**, config drift (4.2), guardrails feed (4.4) | actionable via PR |
| **Jobs** | job status/stream/cancel (existing Job API) | actionable |
| **Resilience** | chaos/kill-drills (4.3), fallback-coverage matrix | actionable (dry-run-first) |
| **Autopilot** | SLO dials, actions log, big-red disengage (6.1) | guarded |

### D.2 Build approach
- **Delivered foundation:** stable human/JSON CLI output and explicit confirmations for retained mutations.
- **Delivered shell:** `pitwall dashboard` with ten views over injectable service sources and headless Pilot coverage.
- **Incremental rule:** future panels land only with an authorized backing product feature; the TUI does not create a second implementation or pre-build future roadmap ideas.

---

## Part E — Phased roadmap

Sequencing across all three tracks. Effort: S ≤ 0.5d · M 0.5–2d · L > 2d (per feature; phases are multi-week).

### Phase 1 — Parity core + onboarding *(unblocks the operability plan too)*
- A · network-volume CRUD + S3 access · registry-auth CRUD · endpoint CRUD + scaling config · template get/update/delete · pod start/stop/reset · GPU/DC discovery (GraphQL) · billing read.
- A · **`pitwall init` wizard + seed** (discovers GPUs/DCs → creates template + registry-auth → registers first provider → seeds a capability → reaches a working inference). *Directly closes operability blockers A-B1…A-B3.*
- TUI · Rich output + `click` contract hardening (D.2 Phase 1).
- **Why first:** it's table-stakes "broker" completeness *and* the onboarding fix; everything else assumes a user can stand up providers.

### Phase 2 — The TUI shell
- Textual `pitwall dashboard`: Overview + Providers + Leases + Cost views, read-only, over the existing service layer (D.2 Phase 2). Headless + snapshot tests.
- **Why second:** gives the parity CRUD a human surface and a foundation every later feature plugs a panel into.

### Phase 3 — The cost-model refactor + first multi-cloud
- B · refactor estimator/budget gate to the **tagged pricing model** (B.3) — the prerequisite for both per-token providers *and* several novel features.
- B · the `Provider` plugin interface (B.2) + **Vast + Together + Lambda** (Wave 1).
- C · **1.4 What-If Simulator** (the load-bearing primitive) — feasible now that traces + pure planner exist.
- **Why here:** the pricing refactor is a fork in the road; doing it before piling on novel budget features avoids rework.

### Phase 4 — Novel quick wins (high value ÷ effort)
- C · 2.1 arbitrage scoring (S) · 1.1 burn-rate forecaster (S) · 1.2 budget circuit breaker (M) · 3.1 budget-aware semantic cache (M) · 1.3 blast-radius sub-budgets (M) · 5.2 unified scorecards (S–M) · 2.6 coalescing (S–M).
- TUI · Cost + Routing views light up as these land.
- **Why here:** each is mostly a new signal into existing machinery; together they deliver the "owns all three planes" story.

### Phase 5 — Platform & moonshots
- C · 4.1 policy-as-code · 4.2 GitOps · 5.1 recommendations engine · 3.2 cascade · 3.3 quality routing · 3.4 shadow/canary · 2.3 prewarm · 2.4 carbon · 2.5 spot-failover.
- C · **6.1 Autopilot** (consumes 1.1/1.2/1.3/2.1/2.3/3.3/4.1/5.1) · 6.2 spot market · 6.3 time-machine.
- B · provider Wave 2 (Replicate/Fal/TensorDock/…).
- **Why last:** these compound on the simulator, the tagged cost model, and the sub-budgets built earlier.

### Decisions for the maintainer
| # | Decision | Note |
|---|----------|------|
| F1 | **How far to chase parity?** Full RunPod control plane vs "consumption + the management ops the TUI needs." | Recommend: full CRUD on the 6 resources (it's bounded and unblocks onboarding); defer SSH-keys/savings-plans/Hub-publishing. |
| F2 | **Multi-cloud now or later?** | Recommend: do the **tagged cost-model refactor** early (Phase 3) even if you only ship RunPod — it's the fork that's expensive to retrofit; add Vast/Together once the abstraction exists. |
| F3 | **Which novel bets define the product?** | Recommend the simulator (1.4) + tri-objective router (2.1+2.4+3.3) + budget circuit breaker (1.2) as the identity; autopilot (6.1) as the north star. |
| F4 | **GraphQL dependency.** Spot bids, savings plans, and live GPU pricing are GraphQL-only — commit to a GraphQL client or forgo spot-arbitrage features. | Affects 2.1/2.5/6.2. |
| F5 | **TUI scope for v1.** Read-only monitor first, or actionable from day one? | Recommend read-only Overview/Cost first (safe, high value), add actionable views behind type-to-confirm as parity lands. |

---

## Appendix

**RunPod surface (parity targets):** REST `api.runpod.io/v2` (Pods/Serverless/Templates/NetworkVolumes/Registries/Billing, strict CRUD + unified pod actions); bounded legacy REST v1 only for reset and pod-create options v2 cannot express; GraphQL `api.runpod.io/graphql` (spot bids `podRentInterruptable`/`podBidResume`, savings plans, live pricing); per-endpoint Job API `api.runpod.ai/v2/{id}` (run/runsync/status/stream/cancel/retry/health/purge-queue); S3 API `s3api-{dc}.runpod.io` (volume files, separate keys); `runpodctl` (croc send/receive, ssh keys, doctor, Hub).

**Pitwall current coverage (audit):** `runpod_client/` owns strict REST v2 pod, template, serverless-endpoint, network-volume, and registry control-plane operations; complete queue Job API and LB/serverless invocation; GraphQL market/discovery/billing seams; and S3 volume file access. The bounded v1 pod path is documented in ADR 0006. Value-add layer to preserve: routing (`routing/*`, `resolver/service.py`), cost+budget gate (`cost/estimator.py`, `cost/budget_gate.py`), 19-check audit (`audit/checks.py`), leases+TTL (`leases/state.py`, `api/leases/*`), webhooks, reconciler, kill-switch, rate limits, idempotency.

**Provider research:** per-second cohort (Vast/Lambda/TensorDock/CoreWeave/Crusoe/Fly/DataCrunch/Hyperstack/Modal/Baseten) vs per-token/run cohort (Together/Replicate/Fal); Wave-1 = Vast + Together + Lambda. Pricing taxonomy: PER_SECOND_ACTIVE · …_WITH_IDLE · PER_MILLION_TOKENS_SPLIT · PER_RUN_UNIT · PER_REQUEST_FLAT · SUBSCRIPTION_RESERVED (+ bid/spot overlay).

**State-of-the-art surveyed (novel track):** LLM gateways (LiteLLM/OpenRouter/Portkey/Cloudflare AI Gateway), semantic caching (GPTCache), cascade routing (RouteLLM, 45–85% cost cut), managed spot + checkpointing schedulers, FinOps anomaly detection (FinOps Foundation), carbon-aware scheduling (Electricity Maps), speculative decoding (vLLM), OPA/Rego policy-as-code, OWASP LLM Top 10. Pitwall's differentiation: it already owns routing **+** budget **+** compute behind one I/O-free, dry-runnable, advisory-lock-gated core (deterministic once the deterministic-replay substrate is in place) — the novel features are *fusions across planes* competitors structurally can't copy.

---

*Drafted 2026-05-31 from public provider surfaces and the repository state at
that date. Re-verify every parity claim against current source and current
provider documentation before implementation.*
