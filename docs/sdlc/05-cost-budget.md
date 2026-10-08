# Cost / Budget Subsystem

## 1. Purpose & Scope

The cost subsystem sits between request admission and provider calls. It estimates every
workload's USD cost before it is admitted, gates admission against a monthly budget
and a per-request cap, persists workload records to Postgres, exports Prometheus
metrics, and dispatches the 80% budget alert through a log-default notifier seam.
Resend email delivery requires `RESEND_API_KEY`, sender/recipient env, and the
optional `email` extra (`pitwall.cost.notifications`, `pyproject.toml`).

## 2. Components

### `estimator.py` — Tagged Pricing Model + Compatibility Estimator

Responsibility: translate a `Capability` + provider cost profile + request payload into
a `Decimal` USD estimate or pre-spend upper bound.

**Key types / functions:**

- `PricingModel` — Pydantic discriminated union keyed by `kind`.
  - `zero` / `ZeroOrEnergyPricing` — zero-dollar work, optionally with an operator-configured electricity estimate.
  - `gpu_hour` / `GpuHourPricing` — the current RunPod path. Existing provider records still use `per_second_active`; the tagged model multiplies that active-second rate by `execution_timeout_ms / 1000`.
  - `per_request` / `PerRequestPricing` — flat per-invocation compatibility variant.
  - `per_second` / `PerSecondPricing` — compute time × `rate_per_second`, with optional `bid_rate_per_second`; `upper_bound()` uses the larger of the actual rate and bid rate.
  - `per_token` / `PerTokenPricing` — split prompt/completion token pricing with `per_million_input_tokens` and `per_million_output_tokens`.
  - `per_vm_second` / `PerVmSecondPricing` — flat VM-second rate for VM providers.
  - `active_idle` / `ActiveIdlePricing` — distinct active execution and idle standing rates, with duration bounds, a positive billing increment, and explicit scale-to-zero behavior.
  - `per_unit` / `PerUnitPricing` — an explicit provider unit, Decimal rate, `unit_count`, and bounded `max_unit_count`; arbitrary request text is never used to infer units.
- `PricingModelProtocol` — every variant implements `estimate(...)`, `upper_bound(...)`, and `quote_components(...)`.
- `CostComponent` — one named Decimal component with unit, rate, estimated count, ceiling rate/count, estimate, and ceiling.
- `CostQuote` — the one public quote shape: `{model, components, estimate, ceiling, confidence, provenance, currency, assumptions}`. Python dumps retain `Decimal`; JSON-safe dumps use exact decimal strings.
- `parse_pricing_model(provider_cost, cost_mode)` — accepts tagged provider cost maps (`{"kind": ...}` or `{"model": ...}`) and legacy untagged maps. Legacy maps are converted by `cost_mode`:
  - `per_second` → `GpuHourPricing(per_second_active=...)`
  - `per_request` → `PerRequestPricing(per_request=...)`
  - `per_token` → `PerTokenPricing(per_million_input_tokens=..., per_million_output_tokens=...)`
- `quote_cost(capability, provider_cost, payload) -> CostQuote` — public binder for admission.
- `CostEstimator` / `PerSecondEstimator` / `PerRequestEstimator` / `PerTokenEstimator` — compatibility wrappers. They parse provider cost into a tagged variant, then call `estimate()` or `upper_bound()` polymorphically.
- `PerTokenPricing`
  - never lets declared input counts reduce observed accounting; estimates use at least `input_bytes / 4` or the text heuristic, and admission reserves at least the UTF-8 byte count of `system`, `messages`, `prompt`, `input`, and `texts`
  - `estimate()` retains the compatibility default of 256 output tokens when an estimate is requested without a max
  - `upper_bound()` and structured quotes require a positive provider-enforced `max_tokens`/`max_output_tokens`/`max_completion_tokens`/`max_new_tokens`; missing, zero, or lower-than-estimated bounds fail before spend
- `get_estimator(mode: CostMode) -> CostEstimator` — legacy registry lookup; raises `ValueError` for unknown modes
- `_usd(value: Decimal) -> Decimal` — **quantization**: estimates retain `ROUND_HALF_UP`; each conservative component ceiling uses `ROUND_CEILING`, so any positive sub-micro-dollar charge reserves at least one micro-dollar
- `_cost_mapping(provider_cost)` — normalises a flat, nested, or model-like cost dict

Invariant: every non-trivial cost path routes through `_usd`, so all published estimates are quantized to 6 decimal places. New standing and provider-unit config rejects binary floats; rates and counts cross the internal boundary as `Decimal`, decimal strings, or integers.

---

### `simulator.py` — What-If Simulator

Responsibility: replay the production planner (`build_production_plan`) against a frozen or hypothetical
world and project cost/budget impact without touching Postgres, Redis, RunPod, or
the production availability cache.

**Key types / functions:**

- `WhatIfSimulator(context, *, price_overrides, budget_usd, current_spend_usd, max_attempts, mode, weights, lockouts, registry)`
  — pure in-memory simulator. The constructor stores a frozen `PlanningContext`; every
  `simulate()` call replays `build_production_plan(..., context=context)` with the given
  `mode` (`priority` or `weighted`), `RoutingWeights`, and `LockoutSnapshot`, then quotes the
  planned attempts through `quote_cost()`. Observed state (health, cooldown, quota, model
  lockouts, prices, RunPod availability) is an explicit input; nothing is read from
  process-global tables.
- `WhatIfSimulator.from_inputs(now=, providers=, capability=, availability_entries=, quota_snapshot=, lockouts=, ...)`
  — convenience constructor for FinOps callers that have raw hypothetical provider,
  capability, price, and capacity inputs. It builds a `PlanningContext.replay(...)`
  snapshot rather than consulting live state.
- `WhatIfWorkload(request, payload)` — one candidate workload for batch simulation.
- `ProviderCostProjection` — per-attempt quote with `provider_id`, `attempt`,
  `estimate_usd`, `upper_bound_usd`, `pricing_kind`, and `selected`.
- `WhatIfProjection` — single-workload output: the full `ProductionRoutePlan`, all attempt
  quote rows, the selected-provider reservation, `projected_spend_usd`,
  `budget_headroom_usd`, and `would_exceed_budget`.
- `WhatIfBatchProjection` — ordered batch output. Each workload is projected using
  the running spend from the previous workload, so headroom reflects cumulative
  hypothetical demand.

**Price override behavior:**

- `price_overrides` is keyed by provider id. Values may be legacy cost maps
  (`{"per_second_active": "0.001"}`), tagged pricing maps
  (`{"kind": "per_second", "rate_per_second": "0.001"}`), or provider-shaped maps
  with nested `cost` / `config.cost`.
- Overrides are applied to a copied provider snapshot before replay. The original
  `PlanningContext` remains immutable and reusable.
- The override replaces `config["cost"]` on the copied provider, so the production
  planner's quote, and therefore its cost-weighted ranking in `weighted` mode, follows the
  hypothetical price curve.

**Budget behavior:**

- Reservation mirrors `BudgetGate.try_launch`: budget impact is the selected
  provider's `CostQuote.upper_bound()`, not the sum of the fallback chain.
- Fallback attempt quotes are still reported so FinOps users can inspect alternate
  provider costs.
- `budget_headroom_usd = budget_usd - projected_spend_usd` is signed; negative
  headroom means the hypothetical workload or batch would exceed the budget.
- All money values are `Decimal` and quantized to 6 decimal places for parity with
  estimator output and persisted USD columns.

Invariant: the simulator is deterministic and performs no I/O. Identical
`PlanningContext`, request, payload, price overrides, budget, and planner options
produce byte-identical `WhatIfProjection.to_dict()` output.

---

### `budget_gate.py` — Postgres-Backed Admission Gate

Responsibility: atomic cost admission with advisory locking and idempotency support.

**Key types / functions:**

- `BudgetGate(pool, monthly_budget_usd, per_request_max_usd, workload_id_factory)` (`pitwall.cost.budget_gate`)
  - `monthly_budget_usd` — loaded from env `PITWALL_MONTHLY_BUDGET_USD` if not provided
  - `per_request_max_usd` — loaded from env `PITWALL_PER_REQUEST_MAX_USD` if not provided
  - defaults require the env vars to be set
- `BudgetSnapshot` (`pitwall.cost.budget_gate`) — frozen dataclass with `model_dump(mode="json")` and `to_serializable_dict` for HTTP bodies
- `BudgetRejected(RuntimeError)` (`pitwall.cost.budget_gate`) — `error_code = "budget_rejected"`, `status_code = 402`; `to_response_body()` returns `{"error", "reason", "snapshot"}`
- `PITWALL_BUDGET_LOCK_KEY = int.from_bytes(b"PITWBUDG", "big")` = `546840836487` (`pitwall.cost.budget_gate`) — Postgres advisory lock key
- `BudgetGate.try_launch(capability_id, provider_id, estimate_usd, workload_type, submitted_at, idempotency_key) -> str` — see §4. `estimate_usd` can be a raw `Decimal` or quote-like object with `upper_bound() -> Decimal`; quote objects are converted to the upper bound before per-request/monthly checks and before insert.
- `BudgetGate.current_mtd_spend() -> Decimal` (`pitwall.cost.budget_gate`) — read-only month-to-date sum, no lock taken

Invariant: budget limits are strictly positive. Admission estimates are non-negative, so a genuine zero-cost quote remains admissible; negative or non-finite values fail closed.

---

### `sync_gate.py` — Synchronous Inference Wiring

Responsibility: the pre-RunPod pipeline — estimate → budget gate → record `queued` → call RunPod → update ledger state.

**Key functions:**

- `gate_sync_inference(capability, provider_id, provider_cost, payload, budget_gate, runpod_caller, idempotency_key, submitted_at, input_bytes, fallback_chain) -> SyncInferenceResult` (`pitwall.cost.sync_gate`)
  - with an `idempotency_key` it first reserves the key (placeholder workload `wkl_pending_<key[:16]>` plus the request `body_hash`) in its own transaction. When admission then raises before any workload row exists (`BudgetRejected`, `SubBudgetRejected`, anything else), the reservation this call created is deleted and the error re-raised, so a refused request does not bind the key; a rejected replay (`is_new=False`) leaves the other request's reservation alone
- `estimate_cost(capability, provider_cost, payload) -> Decimal` (`pitwall.cost.sync_gate:gate_sync_inference`) — public wrapper
- `SyncInferenceRejected(RuntimeError)` (`pitwall.cost.sync_gate`) — wraps a `BudgetRejected` for the sync path
- `update_workload_fallback_chain(pool, workload_id, fallback_chain)` (`pitwall.cost.sync_gate`)
- `_MARK_WORKLOAD_RUNNING_SQL` / `_MARK_WORKLOAD_TERMINAL_SQL` / `_MARK_WORKLOAD_ACTIVE_AFTER_CALL_SQL` / `_MARK_WORKLOAD_FAILED_SQL` (`pitwall.cost.sync_gate`) — SQL fragments that update workload state with timing, bytes, result, and error fields

Invariant: every RunPod call is wrapped so that `failed` / `completed` / `queued` / `running` state is always written back to `pitwall.workloads` even if the caller throws.

---

### `usage.py` — Token Usage Parser

Responsibility: extract actual `prompt_tokens`, `completion_tokens`, `total_tokens` from OpenAI-compatible responses.

**Key functions:**

- `TokenUsage(prompt_tokens, completion_tokens, total_tokens)` (`usage.py`) — frozen dataclass
- `parse_usage_json(body: dict) -> TokenUsage | None` (`usage.py`) — reads `body["usage"]`
- `parse_usage_sse(raw: bytes | str) -> TokenUsage | None` (`usage.py`) — SSE `data:` frame scanner; last frame with usage wins

Invariant: `total_tokens` is derived as `prompt + completion` when not explicitly present (`usage.py`).

---

### `alerts.py` — 80 % Budget Alert with Redis Dedup

Responsibility: check whether cumulative month-to-date spend has crossed 80 % of the monthly budget and dispatch a single notification per month through the notifier seam, deduped via Redis.

**Key constants:**

- `_BUDGET_ALERT_KEY_PREFIX = "pitwall:budget-alert"` (`pitwall.cost.alerts`)
- `_BUDGET_ALERT_TTL_SECONDS = 45 * 24 * 60 * 60` = 3 888 000 s (`pitwall.cost.alerts`)
- `_BUDGET_ALERT_PENDING_TTL_SECONDS = 15 * 60` (`pitwall.cost.alerts`)

**Key functions:**

- `check_and_send_budget_alert(pool, redis_client, *, now, http_client, notifier) -> BudgetAlertResult` (`pitwall.cost.alerts:check_and_send_budget_alert`)
  - `budget_usd` is the runtime limit from `pitwall.budget_limits` (set by `pitwall budget set`), else env `PITWALL_MONTHLY_BUDGET_USD`, the same budget the gate enforces
  - computes `budget_pct = mtd_spend / budget_usd * 100` (`pitwall.cost.alerts:check_and_send_budget_alert`)
  - only dispatches when `budget_pct >= 80` (`pitwall.cost.alerts`)
  - Redis key: `pitwall:budget-alert:YYYY-MM:80`. A check claims it with `SET NX` and a 15-minute pending TTL (owner token `pending:<hex>`) before sending, so concurrent checks send once and the loser returns `skipped_duplicate=True`. A successful send replaces only its own claim with the sent marker and the 45-day TTL; a failed send releases only its own claim so a later check retries. The calls are awaited on the async client the reconciler passes. The claim helpers live in the private `pitwall.cost._redis_claim` module, shared with the forecast alert (`finops/burn_rate_alerts.py`)
- `_compute_mtd_spend(pool, current_time) -> Decimal` — the gate's `MONTH_SPEND_AT_SQL` for `current_time`'s month
- `_send_budget_notification(mtd_spend, budget_usd, budget_pct, notifier)` routes through the
  common non-reflecting notification boundary.

---

### `notifications.py` — Alert Notification Seam

Responsibility: format alert payloads and route them through `Notifier`. The default transport logs; Resend is selected only by configuration and imported lazily.

**Key types / functions:**

- `NotificationResult(threshold_pct=None, email_id=None, error=None, ok=True)` (`pitwall.cost.notifications`)
- `Notifier` (Protocol, `pitwall.cost.notifications`) — `send(*, subject, body) -> NotificationResult`
- `LogNotifier` (`pitwall.cost.notifications`) — default transport; writes a structured alert log line and returns `ok=True` (`pitwall.cost.notifications:LogNotifier`)
- `ResendNotifier` (`pitwall.cost.notifications`) — optional transport; imports `resend` inside `send` (`pitwall.cost.notifications:send`)
- `get_notifier() -> Notifier` (`pitwall.cost.notifications:send`) — returns `ResendNotifier` when `RESEND_API_KEY` is set; otherwise returns `LogNotifier` (`pitwall.cost.notifications`, `pitwall.cost.notifications:get_notifier`)
- `send_notification_safely(...)` invokes the selected transport while reducing SDK/notifier
  failures to `notification_delivery_failed`; exception text, email identifiers from failed
  results, and credential-bearing provider detail never cross the boundary. Successful delivery
  identifiers are retained only when they match the 128-character safe identifier allowlist and
  do not match the central credential detector; logs record only whether an identifier was present.

Invariant: the base broker has no `resend` dependency. `resend` lives in the optional `email` extra (`pyproject.toml`).

---

### `hibernate_alerts.py` — L14 LB Hibernate Sweep Alerts

Responsibility: alert when an L14 Load Balancer endpoint has `workersMin > 0` but is not hibernated, indicating wasted idle cost.

**Key constants:**

- `L14_DAILY_BURN_PER_WORKER_USD = 100.0` (`pitwall.cost.hibernate_alerts`)

**Key types / functions:**

- `HibernateSweepAlert(provider_id, provider_name, endpoint_id, workers_min, duration_hours, burn_estimate_usd)` (`pitwall.cost.hibernate_alerts:HibernateSweepAlert`)
- `HibernateAlertResult(provider_id, endpoint_id, email_id, error)` (`pitwall.cost.hibernate_alerts:HibernateAlertResult`)
- `send_hibernate_sweep_alert(alert, http_client, notifier) -> HibernateAlertResult` (`pitwall.cost.hibernate_alerts:send_hibernate_sweep_alert`)
  - calls `(notifier or get_notifier()).send(...)`; `http_client` is deprecated compatibility (`pitwall.cost.hibernate_alerts:send_hibernate_sweep_alert`)

---

### `exporter.py` — Prometheus Cost Exporter

Responsibility: HTTP `/metrics` endpoint (port 9109) exposing Prometheus gauges for cloud spend, budget %, active workers, kill-log triggers, and unhealthy providers.

**Gauges:**

| Name | Description |
|------|-------------|
| `pitwall_cloud_spend_month_usd` | Cumulative monthly spend |
| `pitwall_cloud_budget_pct` | Spend as % of budget |
| `pitwall_cloud_budget_usd` | Monthly budget |
| `pitwall_active_workers{provider}` | Active lease count per provider |
| `pitwall_kill_log_triggers_7d` | Kill-switch activations in last 7 days |
| `pitwall_providers_unhealthy` | Count of providers with `health_status = 'unhealthy'` |

Poll interval: 60 s. Runs as `python -m pitwall.cost` or via `main()`.

---

### `billing_read.py` — RunPod Billing Read + Budget Reconciliation

Responsibility: read-only typed access to RunPod account credit balance and spend metadata via the GraphQL client, plus reconciliation helpers that compare provider-reported numbers against Pitwall's internal budget gate state.

**Key types / functions:**

- `BillingSnapshot(user_id, client_balance_usd, current_spend_per_hr_usd, spend_limit_usd, min_balance_usd, under_balance)` (`pitwall.cost.billing_read`) — frozen dataclass built from `RunpodCreditsBalance`; all money fields are `Decimal`
- `BillingSnapshot.from_runpod(balance: RunpodCreditsBalance)` (`pitwall.cost.billing_read:BillingSnapshot`) — factory from the GraphQL model
- `BillingSnapshot.to_serializable_dict()` (`pitwall.cost.billing_read:BillingSnapshot`) — stdlib-JSON-safe dict with `Decimal` values as strings
- `BudgetReconciliation(...)` (`pitwall.cost.billing_read:BudgetReconciliation`) — frozen dataclass comparing RunPod and Pitwall budget numbers; includes `variance_usd` = `runpod_balance - pitwall_budget_remaining`
- `BudgetReconciliation.to_serializable_dict()` (`pitwall.cost.billing_read:to_serializable_dict`) — stdlib-JSON-safe dict
- `BudgetGateLike` (Protocol, `pitwall.cost.billing_read:read_billing_snapshot`) — minimal protocol requiring `monthly_budget_usd: Decimal` and `async current_mtd_spend() -> Decimal`; `BudgetGate` satisfies this protocol
- `read_billing_snapshot(client: RunpodGraphQLClient) -> BillingSnapshot` (`pitwall.cost.billing_read:read_billing_snapshot`) — thin async wrapper around `client.credits_balance()`
- `reconcile_with_budget(client, budget_gate) -> BudgetReconciliation` (`pitwall.cost.billing_read:reconcile_with_budget`) — fetches RunPod billing state, reads Pitwall MTD spend, and computes variance

Invariant: all money fields use `Decimal`; floats are never accepted or produced. GraphQL response JSON is decoded with `parse_float=Decimal` (handled by the underlying `RunpodGraphQLClient`).

---

### `reconcile_cost.py` — Provider Billing Truth-Up

Responsibility: retain provider-reported actuals on exact workloads and atomically refresh derived `pitwall.cost_daily` windows, while preserving the pure window comparison API for compatibility and audit output.

**Key types / functions:**

- `CostReconcileWindow(day, capability_class, provider_type)` — one `cost_daily` key.
- `RecordedCostWindow(window, recorded_usd, workload_count)` — broker-recorded cost from `pitwall.cost_daily`.
- `ProviderActualCostWindow(window, actual_usd, source)` — provider-billing actual for the same window, e.g. RunPod billing or a provider adapter.
- `ProviderActualWorkloadCost(workload_id, actual_usd, source)` — an authoritative Decimal actual mapped to one exact Pitwall workload.
- `ProviderActualCostResult(provider_id, availability, source, observed_at, workloads, unavailable_reason)` — the common provider result. `available` requires unique sourced workload actuals; `unavailable` requires an honest reason and carries none.
- `CostReconcileAdjustment(window, recorded_usd, provider_actual_usd, adjustment_usd, sources, workload_id)` — signed correction where `adjustment_usd = provider_actual_usd - recorded_usd`; truth-up adjustments include the exact workload id.
- `CostReconcilePlan(adjustments, window_count)` — deterministic result with `total_adjustment_usd`, `adjustment_count`, and `to_serializable_dict()`.
- `CostTruthUpResult(status, provider_actual, plan, applied_count, start_day, end_day)` — auditable `reconciled`, idempotent no-op `in_sync`, or non-mutating `actual_unavailable` outcome with the requested interval.
- `reconcile_cost(recorded, provider_actuals, tolerance_usd=Decimal("0.000000")) -> CostReconcilePlan` — pure comparison; groups duplicate windows, compares the supplied recorded/provider union, drops differences within tolerance, and sorts output by window.
- `AsyncpgCostTruthUpRepository(pool)` — thin adapter that acquires one transaction-scoped advisory lock, locks exact workload ids, validates provider/state/date ownership, applies absolute sourced actuals, and recomputes only affected derived daily windows in the same transaction.
- `reconcile_provider_actual_cost(...)` — returns unavailable results without repository I/O and reports whether an available read changed durable workload truth or was an idempotent replay.

Invariant: all money inputs must be `Decimal`, finite, non-negative before comparison, and quantized to 6 decimal places with `ROUND_HALF_UP`. The pure function is deterministic for identical inputs. The asyncpg adapter is transactional and idempotent because it serializes compare-and-set and stores provider workload actuals rather than adding deltas. Derived rollups cannot overwrite that source authority. Aggregate-only bills, estimates, balances, and empty/lagging reports remain unavailable. RunPod advertises provider-actual-cost support only for exact persisted workload-to-Pod identities and bounded authoritative Pod billing windows. Vast.ai, Together AI, and Lambda do not advertise this capability; RunPod endpoint and network-volume billing also remains unavailable as workload actual cost.

---

### `read_models.py` / `core.cost_reporting` — Estimate-versus-Actual Reads

Responsibility: keep persisted cost reads Decimal-authoritative while preserving established JSON-number fields at their public compatibility boundary.

- `WorkloadCostRead` exposes the shared cost vocabulary: model, components, estimate, ceiling, confidence, provenance, currency, assumptions, actual, variance, effective amount, reconciliation status, actual kind/source, and reconciliation time.
- Migration 0029 retains `cost_ceiling_usd`, the exact structured `cost_quote`, `cost_actual_provenance`, and `cost_reconciled_at`. Legacy rows have their prior admission amount backfilled as the ceiling and remain confidence `unknown`; no missing components are invented.
- Provider billing reads are labelled `actual_kind="provider_reported"`; an unsourced legacy or
  broker-observed actual is `actual_kind="recorded"`. Together token counts returned in the
  successful provider response are priced with the admitted `PerTokenPricing` rates and labelled
  `actual_kind="usage_derived"`, never provider billing. Missing usage, failed attempts, and
  cancellations after provider egress keep actual unavailable rather than claiming zero
  (`pitwall.routing.production`,
  `pitwall.cost.read_models`). None is labelled
  an invoice, so `provider_invoice=false` remains explicit. OpenAI fallback admission initially
  reserves the exact executable paid chain, then terminal truth-up reduces its ceiling and quote to
  the attempts actually made. A successful already-paid pod lease is the narrow zero-actual
  exception when every attempt made was lease-covered: the proxy records `broker:lease_covered`,
  which releases any unused paid-fallback reservation without implying that a failed lease or a
  preceding paid provider attempt cost zero
  (`pitwall.api.routes.openai`).
- `CostSummaryRead` and `RecentWorkloadsRead` remain Decimal internally. Their explicitly named legacy serializers are the only cost-reporting boundary that converts established numeric fields to `float`; the nested workload cost model retains exact decimal strings.
- REST, MCP, CLI, and TUI consumers must delegate to `core.cost_reporting` rather than reimplementing cost semantics.

Invariant: an estimate is never presented as provider actual billing, and provider provenance is never promoted to an invoice claim without an invoice-specific contract.

---

### `circuit_breaker.py` — Budget Circuit Breaker / Auto-Downgrade

Responsibility: stateful circuit breaker that trips when budget headroom or burn-rate runway crosses configurable thresholds, emits `allow` / `downgrade` / `block` decisions, and recovers with hysteresis to avoid flapping.

**Key types / functions:**

- `BudgetCircuitBreaker` (`pitwall.cost.circuit_breaker:BudgetCircuitBreaker`) — stateful breaker with configurable thresholds
  - `headroom_trip_pct` — default `10.0`; trips closed→open when headroom % falls at or below this value
  - `runway_trip_hours` — default `24.0`; trips when burn-rate runway (hours until exhaustion) falls at or below this value
  - `recovery_headroom_pct` — default `20.0`; must be **>** `headroom_trip_pct` (hysteresis)
  - `recovery_runway_hours` — default `72.0`; must be **>** `runway_trip_hours` (hysteresis)
  - `downgrade_headroom_pct` — default `5.0`; below this, the breaker emits `block` instead of `downgrade`
  - `cooldown_seconds` — default `300.0`; time before an `open` breaker transitions to `half-open`
- `CircuitBreakerDecision` (`pitwall.cost.circuit_breaker:CircuitBreakerDecision`) — frozen dataclass with `action`, `reason`, `state`, `headroom_usd`, `headroom_pct`, `runway_hours`
- `BudgetCircuitBreaker.evaluate(budget_usd, mtd_spend_usd, now, burn_rate_usd_per_hour)` (`pitwall.cost.circuit_breaker:evaluate`) — explicit *now* for determinism; requires timezone-aware datetime
- `BudgetCircuitBreaker.state` — read-only current state (`closed` / `open` / `half-open`)
- `BudgetCircuitBreaker.reset()` — resets to `closed`; useful for testing

**State machine:**

| Transition | Condition | Action emitted |
|------------|-----------|----------------|
| `closed` → `open` | `headroom_pct <= headroom_trip_pct` OR `runway_hours <= runway_trip_hours` | `downgrade` or `block` |
| `open` → `half-open` | `now - last_trip_at >= cooldown_seconds` | `downgrade` or `block` |
| `half-open` → `closed` | `headroom_pct >= recovery_headroom_pct` AND `runway_hours >= recovery_runway_hours` | `allow` |
| `half-open` → `open` | Recovery thresholds NOT met | `downgrade` or `block` |

**Downgrade vs block:**

- `block` when `headroom_pct <= downgrade_headroom_pct` (budget effectively exhausted)
- `downgrade` when tripped but headroom is still above the downgrade threshold
- `allow` when `closed`

The gate (e.g. `sync_gate.gate_sync_inference`) can consult the breaker after estimating cost and before calling `BudgetGate.try_launch`.  A `downgrade` decision can be translated into `Hints(cost_sensitive=True)` for the planner; a `block` decision can short-circuit to HTTP 402 without touching Postgres.

Invariant: the breaker is deterministic.  Identical inputs plus identical internal state always yield identical `CircuitBreakerDecision` objects.

---

### `budget_kill_escalation.py` — Budget-Breach → Kill-Switch Escalation (opt-in)

Responsibility: the **most severe** rung of the budget ladder — optionally auto-firing the
operator kill switch (`CloudKillSwitch`, network sever + compute termination) when the budget is
*exhausted*, not merely low. Because auto-terminating running compute on a budget number is
dangerous, it is **disabled by default** and gated three ways.

**Escalation ladder (increasing severity):**

    per-run cap reject → monthly cap reject → circuit-breaker `downgrade` →
    circuit-breaker `block` → **budget-breach kill escalation** (this module)

**Modes** (`PITWALL_BUDGET_BREACH_KILL_MODE`):

- `disabled` (default) — inert; never evaluates or fires.
- `shadow` — evaluates the trigger and **logs what it would do**, but never touches the kill
  switch. Lets operators validate the trigger before arming.
- `armed` — fires `CloudKillSwitch.activate(...)` when the trigger holds.

**Trigger gate** (must *all* hold): mode ≠ `disabled`; circuit-breaker `action == "block"`
(unless `KillEscalationPolicy.require_block` is relaxed); and headroom ≤
`PITWALL_BUDGET_BREACH_KILL_HEADROOM_FLOOR_USD` (default `0` — i.e. fully exhausted/overrun).
Never escalates on `allow`/`downgrade`.

**Key types / functions:**

- `KillEscalationMode` — `"disabled" | "shadow" | "armed"`
- `KillEscalationPolicy` — frozen: `require_block` (default True), `headroom_floor_usd` (default 0)
- `evaluate_kill_escalation(decision, *, mode, policy)` — **pure/deterministic** decision
- `maybe_escalate_to_kill(decision, kill_switch, *, mode, policy)` — async invoker; the *only* thing
  that can fire the switch; returns a `KillEscalationOutcome` audit record (`fired`, `mode`, `reason`,
  `report`) in every mode
- `KillSwitchLike` — minimal Protocol (`async activate(reason)`) keeping this module decoupled from
  `pitwall.api.admin` (no circular import)

**Integration:** the reconciler's `_budget_breach_escalation` cron job runs every minute. In
`disabled` mode it returns before reading anything. Otherwise it reads month-to-date spend
(`BudgetGate.current_mtd_spend`), evaluates a `BudgetCircuitBreaker` kept in the worker context, and
passes the decision to `maybe_escalate_to_kill`. In `armed` mode the switch is `run_kill(...,
actor="system:budget-breach", terminate_compute=True)`, which persists the report to
`pitwall.kill_log`; a row with that actor in the current UTC month stops the job from firing again
that month. Off-by-default means existing deployments are unaffected until an operator opts in
(recommended path: `shadow` first, then `armed`).

### `__init__.py` — Lazy Module-Level Re-Exports

`pitwall.cost` uses a lazy `__getattr__` to import from submodules on first attribute access. Estimator, reconciliation, and shared read-model types are included in the same lazy public surface; importing `pitwall.cost` still performs no provider or database I/O.

---

### `burn_rate.py` — Persisted Burn-Rate Read

Responsibility: derive one deterministic, read-only monthly burn-rate view from persisted
`pitwall.cost_daily` Decimal rollups. The legacy `BurnRateForecaster` remains a pure helper for
callers that already supply `SpendPoint` values; `BurnRateRead` is the shared operator model for
the REST, MCP, CLI JSON, and Cost TUI paths.

**Key types / functions:**

- `SpendPoint(day, cost_usd)` — one persisted UTC day of spend.
- `BurnRateForecast` / `BurnRateForecaster` — retained pure forecast helper. It calculates daily
  rate, trend, confidence, remaining budget, and exhaustion from supplied points; it has no I/O.
- `BurnRateRead` (`src/pitwall/finops/burn_rate.py`) — frozen, transport-neutral view containing:
  - UTC `now`; inclusive UTC observation-window start/end/days and observed-day count;
  - month-to-date spend, daily rate, month-end forecast, budget, remaining budget, and consumed
    percent; all money is Decimal quantised to six places;
  - projected breach timestamp and Decimal ETA, trend, confidence, data sufficiency, freshness,
    last-rollup day, at/already-breached flags, and an explicit projection-overflow flag.
  - `to_dict()` is the canonical REST/MCP/CLI JSON schema. Decimal fields are strings (rather
    than binary floats); percent is serialized to four decimal places; timestamps are UTC `Z`.
- `read_burn_rate(pool, *, budget_usd, now, window_days=30) -> BurnRateRead` — the persisted
  read service. `now` is required, timezone-aware, and normalized to UTC. `window_days` is
  bounded to 1–366.
- `read_configured_burn_rate(pool, *, now, window_days=30)` — same service using the existing
  `PITWALL_MONTHLY_BUDGET_USD` setting; it introduces no configuration key.
- `forecast_from_cost_daily(...)` — compatibility adapter for existing callers; it now aggregates
  `cost_daily` by UTC day before invoking the pure forecaster.

**Read semantics:**

- The observation window is inclusive: its start is `UTC(now).date() - (window_days - 1)` and its
  end is `UTC(now).date()`. The query groups every capability/provider row with
  `SUM(cost_usd) GROUP BY day`, so the model never chooses a single cost dimension arbitrarily.
- `spend_to_date_usd` is separately summed from the first day of the current **UTC** calendar month
  through the observation end. A cross-month rolling window therefore does not pollute the active
  monthly budget calculation.
- The daily rate is the persisted-window total divided by the inclusive calendar span from the
  first to last observed day. Missing days inside that span count as zero; absent leading/trailing
  rollup days are represented by data sufficiency rather than silently treated as zero.
- The month-end forecast is `spend_to_date + daily_rate × fractional UTC days until the next UTC
  month boundary`. It is `null` for no observed rows. A date or Decimal overflow never raises from
  the read: the affected projection is `null` and `projection_overflow` is `true`.
- `no_data` means zero observed rollup days; `sparse` means 1–6; `sufficient` means at least 7.
  `stale` means the rollup is behind the work: a finished workload's UTC day, up to yesterday,
  has no persisted rollup row. An idle broker whose last work is rolled up is not stale, however
  old that day is.
- A zero budget has `percent_consumed: null`; zero spend is explicitly `at_budget` in that case.
  For a positive budget, consumed percent is not capped, so an already-breached month remains
  observable. At-budget and already-breached reads report breach time `now` and ETA zero.
  A positive future ETA is rounded upward at the public six-decimal-day precision, so a breach
  less than one quantum away remains distinguishable from an already-breached result.

**Forecast notification policy:**

`burn_rate_alerts.check_and_send_forecast_alert` is notification-only and is called from the
existing successful `cost_daily` rollup hook. It does not call, modify, or weaken `BudgetGate` or
the existing actual-spend alert. A fresh, sufficient, non-overflow forecast first crosses when its
month-end total is **greater than** 100% of a positive budget. It uses the existing notifier
transport and Redis deduplication pattern with the month-scoped key
`pitwall:forecast-alert:YYYY-MM:100` and a 45-day TTL. A sent key is removed (re-armed) only after
a fresh, sufficient forecast falls **below** 90%; exactly 100% is not a breach crossing and exactly
90% does not re-arm. Zero budget, no/sparse data, stale rollups, an at-or-already-breached actual
month, no projection, and overflow never send a forecast notification.

The crossing path atomically claims the month key with Redis `SET NX` and a 15-minute pending
delivery lease. Concurrent reconciler runs therefore send once, while a process crash cannot mute
the alert for the rest of the month. Success atomically replaces only the caller's reservation
with the bounded delivery identifier and extends it to 45 days; delivery failure removes only that
reservation and returns the stable `notification_delivery_failed` reason. A later rollup can retry
without exposing transport or credential detail. As with ordinary notification transports, a
provider delivery that outlives the pending lease is an at-least-once edge rather than a false
exactly-once claim and returns `notification_sent_without_dedup`. Recovery re-arms only a completed
`sent:` delivery marker; it cannot delete another reconciler's live `pending:` reservation. The
explicit tags keep provider-supplied delivery identifiers from colliding with ownership state.

**Feature-local operator adapters:**

- `api/routes/burn_rate.py` owns the `GET /v1/cost/burn-rate` handler.
- `mcp/tools/burn_rate.py` owns `pitwall_burn_rate(window_days=30)`.
- `cli/burn_rate.py` owns the `burn-rate` command handler and emits `BurnRateRead.to_dict()` in
  `--json` mode. Persistence or configuration failures return exit code 1 with the stable,
  non-reflecting `{"error": "burn_rate_unavailable"}` JSON envelope (or the same code in human
  output); exception and credential detail is never emitted.
- `tui/cost.py`'s `PostgresCostSource` reads the same model before rendering aggregate spend,
  forecast, projected breach, confidence, and data freshness.

The serialized surface registration mounts the HTTP route in the FastAPI inventory, registers the
MCP tool, and dispatches the top-level `burn-rate` CLI command. All three return the same
`BurnRateRead.to_dict()` schema; the existing Cost TUI renders the same model.

Invariant: the pure helper and persisted read are deterministic for explicit inputs; all four
operator adapters delegate to `BurnRateRead` rather than duplicating forecast math.
The checked-in `tests/fixtures/cost/burn_rate_boundaries.json` golden vectors pin full read-model
output for UTC month rollover, delayed rollups, and projection overflow, plus the complete
forecast-alert repeat/recovery/deduplication sequence.

### `reservations.py` — Reservation recommender

Responsibility: evaluate on-demand cost against reservation / warm-pool candidate plans and return a recommendation-only FinOps decision. The core path is pure: callers provide a `DemandForecast`, a `WhatIfSimulator`, reservation candidates, and optionally a `BurnRateForecast`; the recommender performs no provider mutations, no database writes, and no network calls.

**Key types / functions:**

- `DemandForecast(name, workloads, window_hours)` — named forecast window containing `WhatIfWorkload` entries that are replayed through the simulator.
- `ReservationLine(provider_id, reserved_units, warm_pool_size, unit_capacity, hourly_commitment_usd, upfront_usd)` — one provider sizing line. `capacity_workloads = (reserved_units + warm_pool_size) * unit_capacity`; fixed cost is upfront cost plus hourly commitment over the forecast window.
- `ReservationCandidate(plan_id, reserves, price_overrides)` — one build/warm-pool plan. `price_overrides` must reference providers present in the candidate reservation lines, so discounted marginal pricing cannot be evaluated without declared capacity.
- `recommend_reservations(demand, simulator, candidates, burn_rate_forecast=None) -> ReservationRecommendation` — computes the on-demand baseline with `WhatIfSimulator.simulate_workloads(...)`, then evaluates each candidate with remaining reserved capacity. Workloads covered by reserved capacity use candidate price overrides; overflow workloads fall back to the on-demand baseline projection.
- `ReservationRecommender(simulator).recommend(...)` — reusable wrapper for callers that want to bind the simulator once.
- `PlanEvaluation` — structured evaluation with fixed, marginal, and total Decimal cost, covered workload count, overflow count, unmet count, selected-provider counts, projected savings, and optional budget-after-plan / runway-after-plan metadata.
- `ReservationRecommendation` — final output with `action` of `reserve`, `on_demand`, or `blocked`, the on-demand baseline, candidate evaluations, the selected evaluation, and JSON-safe `to_dict()`.

Selection rule: only plans with `meets_demand=True` are eligible. The chosen plan is the lowest total cost; exact ties prefer on-demand, then candidate `plan_id` order for deterministic output. If no plan can route the demand, the action is `blocked`.

Invariant: all costs and savings are `Decimal` values quantised to 6 decimal places. Identical demand, simulator context, candidates, and burn-rate forecast produce byte-identical `ReservationRecommendation.to_dict()` output.

### `sub_budgets.py` — Blast-Radius Sub-Budgets + Chargeback

Responsibility: partition the monthly budget into named sub-budgets (per capability/team/tag), gate admission against the relevant sub-budget, and attribute spend via deterministic chargeback reports.

**Key types / functions:**

- `SubBudget(tag, allocation_usd, description)` (`pitwall.cost.sub_budgets`) — one named slice of the monthly budget
- `SubBudgetConfig(total_budget_usd, budgets)` (`pitwall.cost.sub_budgets:SubBudget`) — Pydantic model that validates sub-budget allocations sum ≤ total budget
- `SubBudgetGate(budget_gate, config, tag_mtd_spend)` (`pitwall.cost.sub_budgets`) — wraps `BudgetGate` and adds per-tag admission
  - `tag_mtd_spend` is an optional async callable; when omitted the gate tracks spend in-memory. The callable is evaluated under the global admission lock (inside `BudgetGate.try_launch_admission`'s transaction, which holds `pg_advisory_xact_lock` on a pooled connection), so it must count every admitted workload, including ones admitted a moment earlier
  - the required signature is `async def tag_mtd_spend(tag: str, conn) -> Decimal`. The resolver runs inside the admission transaction, which holds one pooled connection and `pg_advisory_xact_lock`, so it must query on `conn`, the transaction connection that `BudgetGate.try_launch_admission` passes to `before_new_admission(conn)` (as `after_new_admission(conn, workload_id)` already receives it). A resolver that acquires its own connection deadlocks silently on a one-connection pool, and on a larger pool whenever concurrent admissions have checked out every connection while waiting for the advisory lock. `SubBudgetGate` therefore inspects the signature at construction and raises `TypeError` for a resolver that cannot take the second positional argument; a resolver declaring `*args` is accepted
  - `try_launch(tag, capability_id, provider_id, estimate_usd, ...)` passes the sub-budget check to `BudgetGate.try_launch_admission` as its `before_new_admission(conn)` callback, so the tag check and the global check run in one lock; in-memory spend is recorded in the `after_new_admission` callback. An idempotency replay (`is_new=False`) skips both
  - exposes `monthly_budget_usd` and `current_mtd_spend()` for compatibility with `BudgetGateLike`
- `SubBudgetRejected(RuntimeError)` (`pitwall.cost.sub_budgets`) — `error_code = "sub_budget_rejected"`, `status_code = 402`; reasons include `"unknown_tag"` and `"sub_budget"`
- `SubBudgetSnapshot` (`pitwall.cost.sub_budgets`) — frozen dataclass with tag-specific state and `to_serializable_dict()` for HTTP bodies
- `generate_chargeback_report(config, workloads, tag_resolver)` (`pitwall.cost.sub_budgets`) — pure function that attributes spend by tag
  - uses the gate's precedence per workload: `cost_actual_usd`, else `cost_ceiling_usd`, else `cost_estimate_usd`
  - workloads without a matching tag are counted as `unallocated_spend_usd`
  - returns `ChargebackReport(total_spend_usd, line_items, unallocated_spend_usd)` with `to_serializable_dict()`

Invariant: all money values are `Decimal` and validated for finiteness. Sub-budget allocations are non-negative; the total budget is strictly positive. The chargeback report is deterministic: identical `config`, `workloads`, and `tag_resolver` always produce the same report.

---

## 3. Cost Model

### Tagged Pricing Variants

Provider cost now has a tagged shape. The tag lives in `kind` (or `model` for caller convenience), and each variant owns its own math:

| `kind` | Formula | Upper bound behavior | Rate keys |
|--------|---------|----------------------|-----------|
| `zero` | zero, or configured watts × duration × USD/kWh | same as estimate | optional `watts`, `usd_per_kwh` |
| `gpu_hour` | `per_second_active * (execution_timeout_ms / 1000)` | same as estimate | `per_second_active` |
| `per_request` | one admitted invocation × flat fee | exactly one; client count/cap fields are rejected | `per_request` |
| `per_second` | `rate_per_second * (execution_timeout_ms / 1000)` | uses `max(rate_per_second, bid_rate_per_second)` when a bid is present | `rate_per_second`, optional `bid_rate_per_second` |
| `per_token` | `(input rate × input tokens + output rate × output tokens) / 1_000_000` | requires an explicit maximum output-token bound | `per_million_input_tokens`, `per_million_output_tokens` |
| `per_vm_second` | `rate_per_second * (execution_timeout_ms / 1000)` | same as estimate | `rate_per_second` |
| `active_idle` | active rate × rounded active duration + idle rate × rounded standing duration | active maximum is the capability timeout; idle maximum is trusted pricing config and may be omitted only for scale-to-zero/zero idle rate | `active_rate_per_second`, `idle_rate_per_second`, `minimum_billing_increment_seconds`, `scale_to_zero`, optional `max_idle_seconds` |
| `per_unit` | provider-unit rate × explicit unit count | `max_unit_count`, defaulting to the explicit count | `unit`, `rate_per_unit` |

Legacy untagged provider cost maps still work. `parse_pricing_model()` converts legacy capability modes to tagged variants so current providers keep exact numbers while future providers can supply a tagged map directly.

Token fallback hierarchy (`PerTokenEstimator._estimate_tokens` / `PerTokenPricing`):
- Explicit counts: `payload["input_tokens"]` / `payload["output_tokens"]` (or nested `payload["usage"]`), but an input declaration cannot lower observed accounting
- Estimate floor: `input_bytes / 4` or `sum(text_lengths_of("system","messages","prompt","input","texts")) / 4`
- Admission floor: the larger of the estimate and the exact UTF-8 bytes in those request fields
- Output default: 256 tokens when `max_tokens` / `max_output_tokens` / `max_completion_tokens` / `max_new_tokens` are absent

Why `upper_bound()` matters: execution time, output tokens, standing time, and provider-defined units can be open-ended at launch time. The budget gate must decide before dispatch. `CostQuote.upper_bound()` either returns a conservative Decimal reservation or rejects an unbounded request; it never silently substitutes an optimistic estimate.

Detailed tagged configuration and payload examples for every active variant are in [Active cost pricing and admission](../operations/cost-pricing.md).

### Zero pricing burn-down

The `zero` kind estimates `$0` (or configured watts × duration × USD/kWh), so the budget gate
admits free-pool requests without reserving spend. Consumption is instead tracked as a per-pool
token burn-down so "free" capacity stays honest:

- A successful zero-priced execution records `result.total_tokens` via
  `QuotaRepository.add_usage` into `pitwall.provider_quotas.used_units`; the write is
  best-effort and never fails delivered output
  (`src/pitwall/routing/production.py` `_record_quota_usage`).
- The reconciler `_quota_poll` seeds, rolls, and samples quota windows every 5 minutes; it
  never calls upstream. Recurring windows re-zero `used_units` at `reset_at`.
- The routing Stage-2 quota gate eliminates exhausted pools (`quota_ineligible`) and the
  Stage-3 headroom/reset-proximity terms prefer fuller pools, so burn-down directly shapes
  selection (see [Routing](04-routing.md)).
- The cost exporter publishes the free-pool gauges described in
  [Observability](13-observability.md).
- When every free pool is exhausted and no metered candidate remains, the route plan carries a
  `pitwall serve` escape-hatch proposal instead of a paid admission.

### Rounding / Quantization

Published estimates pass through `_usd(value)` with compatibility rounding:

```
estimate_usd = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
```

`_USD_QUANTUM = Decimal("0.000001")`. Every estimate is therefore accurate to **6 decimal places** (micro-dollar).
Each named ceiling product uses `ROUND_CEILING`, then the component ceilings
are summed. Thus a positive exact upper-bound product never rounds down to
zero or below its unquantized value.

## 4. Budget Admission Flow (`BudgetGate.try_launch`)

```
try_launch(capability_id, provider_id, estimate_usd, workload_type,
           submitted_at, idempotency_key)
│
├─ _positive_estimate(estimate_usd)
│   ├─ raw Decimal/string/int/legacy-float → _positive_decimal(...)
│   └─ structured quote → retain estimate + JSON and reserve upper_bound()
│   └─ raise ValueError if estimate <= 0
│
├─ pre-flight: per_request_cap check (outside transaction)
│   └─ if estimate > self.per_request_max_usd
│        → raise BudgetRejected("per_request_cap", snapshot)
│
└─ async with pool.acquire() as conn, conn.transaction():
     ├─ SELECT pg_advisory_xact_lock(PITWALL_BUDGET_LOCK_KEY)   ← exclusive advisory lock
     │
     ├─ if idempotency_key is not None:
     │    SELECT id FROM pitwall.workloads WHERE idempotency_key = $1
     │    → return existing_id if found (idempotent replay)
     │
     ├─ SELECT COALESCE(SUM(COALESCE(cost_actual_usd,
     │                               cost_ceiling_usd,
     │                               cost_estimate_usd)), 0)
     │    FROM pitwall.workloads
     │    WHERE submitted_at >= date_trunc('month', now() AT TIME ZONE 'UTC')
     │   → mtd_spend
     │
     ├─ if mtd_spend + estimate > self.monthly_budget_usd
     │    → raise BudgetRejected("monthly_budget", snapshot)
     │
     ├─ workload_id = _workload_id_factory()  (default: "wkl_" + ULID)
     │
     ├─ INSERT pitwall.workloads (id, capability_id, provider_id, type,
     │   state='queued', cost_estimate_usd, submitted_at[, idempotency_key],
     │   cost_ceiling_usd, cost_quote)
     │   RETURNING id
     │
     └─ return str(admitted_id)
```

Post-insert, callers (e.g. `sync_gate.gate_sync_inference`) immediately transition the row to `running` via `_mark_workload_running`.

## 5. Configuration

| Env Var | Module | Default | Description |
|---------|--------|---------|-------------|
| `PITWALL_MONTHLY_BUDGET_USD` | `src/pitwall/cost/budget_gate.py`, `src/pitwall/cost/alerts.py`, `src/pitwall/cost/exporter.py`, `src/pitwall/finops/burn_rate.py` | **required** (gate/alerts) / `"50.0"` (burn-rate settings default) / `"1000"` (exporter) | Monthly spend cap |
| `PITWALL_PER_REQUEST_MAX_USD` | `pitwall.cost.budget_gate` | **required** | Per-request estimate cap |
| `RESEND_API_KEY` | `pitwall.cost.notifications` | unset → `LogNotifier` | Selects `ResendNotifier` when set; absent falls back to structured logging |
| `PITWALL_ALERT_FROM` | `pitwall.cost.notifications` | required for `ResendNotifier` | From address; falls back to `RESEND_SENDER_EMAIL` |
| `PITWALL_ALERT_TO` | `pitwall.cost.notifications` | required for `ResendNotifier` | Alert recipient; falls back to `RESEND_BUDGET_ALERT_EMAIL` |
| `RESEND_SENDER_EMAIL` | `pitwall.cost.notifications` | fallback only | Legacy from address when `PITWALL_ALERT_FROM` is unset |
| `RESEND_BUDGET_ALERT_EMAIL` | `pitwall.cost.notifications` | fallback only | Legacy recipient when `PITWALL_ALERT_TO` is unset |
| `PITWALL_COST_EXPORTER_PORT` | `pitwall.cost.exporter` | `"9109"` | Metrics HTTP port |
| `DATABASE_URL` | `pitwall.cost.exporter` | **required** (exporter) | Postgres DSN |

## 6. Failure Modes

| Error | Type | Trigger | HTTP Status |
|-------|------|---------|-------------|
| `BudgetRejected(reason="per_request_cap")` | `RuntimeError` | `estimate > per_request_max_usd` before lock | 402 |
| `BudgetRejected(reason="monthly_budget")` | `RuntimeError` | `mtd_spend + estimate > monthly_budget` under lock | 402 |
| `SyncInferenceRejected(reason, budget_error)` | `RuntimeError` | wraps `BudgetRejected` for the sync path | 402 |
| `ValueError` (from `_positive_decimal`) | `ValueError` | `estimate_usd <= 0` or config not positive | 500 |
| `ValueError` (from `get_estimator`) | `ValueError` | unknown `CostMode` | 500 |
| `ValueError` (from `_required_non_negative_decimal`) | `ValueError` | provider cost missing required key | 500 |
| `ValueError` (from `_usd`) | `ValueError` | cost estimate out of representable USD range | 500 |
| `ValueError` (bounded quote) | `ValueError` | required token/standing/unit bound is missing, non-positive where required, below observed/requested use, or supplied from a forbidden client-only duration/request field | fail before provider dispatch |
| `CostTruthUpResult(status="actual_unavailable")` | structured result | adapter has no genuine provider actual or the provider window is not authoritative | no ledger write |

`BudgetRejected.to_response_body()` (`pitwall.cost.budget_gate`) is the canonical HTTP 402 body:
```json
{"error": "budget_rejected", "reason": "monthly_budget", "snapshot": {...}}
```

## 7. Testing

| Test file | What it covers |
|-----------|----------------|
| `tests/cost/test_estimator.py` | Legacy estimator characterization, tagged pricing variants, quote interface, dispatch |
| `tests/cost/test_structured_quote.py` | Strict standing/per-unit config, named quote components, current-adapter fixtures, Decimal round trip, malformed/overflow input |
| `tests/property/test_cost_quote_properties.py` | Non-negativity, monotonicity, precision, and ceiling ≥ estimate for standing/per-unit quotes |
| `tests/cost/test_simulator.py` | What-if planner replay, price overrides, per-attempt cost breakdown, selected upper-bound budget headroom, batch accumulation |
| `tests/unit/cost/test_estimator_rounding.py` | `_usd` quantization to 6 decimal places |
| `tests/unit/cost/test_estimator_boundary.py` | Zero, negative, missing keys, unknown `CostMode` |
| `tests/unit/cost/test_sync_persist_deadline.py` | Sync gate workload state transitions |
| `tests/unit/cost/test_estimator_quote_contract.py` | Hand-computed estimates and ceilings per pricing model: half-up estimate vs round-up ceiling, bid and tier ceiling rates, cached-input billing, default output bound, UTF-8 input-byte ceiling, active/idle scale-to-zero bound, overflow, assumptions, exact error text |
| `tests/unit/cost/test_budget_gate_admission_contract.py` | `BudgetGate` cap boundaries (equal is admitted), advisory lock, limits connection, persisted workload columns and compact quote JSON, malformed-estimate errors, rejection and idempotency log lines |
| `tests/unit/cost/test_sync_gate_ledger_contract.py` | `gate_sync_inference` ledger writes by column: keyed admission, replay log sequence, lost admission race mismatch, failure timing, non-JSON provider results |
| `tests/unit/cost/test_usage_cached_tokens.py` | Cached prompt-token capture from JSON and SSE usage, invalid cached counts as zero, negative float token rejection |
| `tests/unit/rate_limits/test_seconds_until_available_defaults.py` | Token-bucket wait for the default one token and for exactly the full capacity |
| `tests/cost/test_budget_gate.py` | `BudgetGate.try_launch` admit/reject logic, idempotency, quote upper-bound gating |
| `tests/cost/test_sync_gate.py` | Full `gate_sync_inference` pipeline |
| `tests/cost/test_budget_alerts.py` | `check_and_send_budget_alert` with Redis dedup |
| `tests/cost/test_usage.py` | `parse_usage_json`, `parse_usage_sse` |
| `tests/cost/test_hibernate_alerts.py` | `send_hibernate_sweep_alert` |
| `tests/cost/test_cloud_cost_exporter.py` | `/metrics` endpoint |
| `tests/cost/test_billing_read.py` | `read_billing_snapshot`, `reconcile_with_budget`, `BillingSnapshot`, `BudgetReconciliation`; hermetic fake transport; Decimal fidelity; error propagation |
| `tests/cost/test_reconcile_cost.py` | Provider result availability, transactional truth-up, replay, window validation, Decimal fidelity, and asyncpg adapter SQL |
| `tests/integration/test_cost_truth_up_concurrency.py` | Concurrent identical provider reads serialize and apply the absolute actual once |
| `tests/cost/test_cost_read_models.py` | Shared estimate/ceiling/confidence/actual/reconciliation semantics and legacy numeric boundary |
| `tests/api/test_cost_read_routes.py`, `tests/mcp/test_cost_tools.py`, `tests/cost/test_cost_cli.py` | REST/MCP/CLI delegation to the shared cost reporting model |
| `tests/unit/finops/test_burn_rate.py` | BurnRateForecaster basics: empty/single/multi-point windows, trend detection, budget exhaustion, gap handling, naive-now guard |
| `tests/property/test_burn_rate_properties.py` | Property-based invariants: non-negative burn rate, remaining budget accuracy, confidence ∈ [0,1], zero-burn behaviour |
| `tests/unit/finops/test_burn_rate_read.py` | Persisted daily rollup aggregation, exact shared JSON schema, no/sparse data, zero/at/already-breached budgets, UTC/month boundary, stale rollup, and overflow behaviour |
| `tests/property/test_burn_rate_read_properties.py` | Decimal persisted-read invariants for non-negative values, percent semantics, breach ETA, and serialization |
| `tests/finops/test_burn_rate_alerts.py` | Forecast threshold, atomic concurrent Redis dedup, 45-day TTL, below-90% re-arm, threshold boundaries, suppression states, aggregate-only notification content, and non-reflecting delivery failure |
| `tests/integration/test_burn_rate_read.py` | Real Postgres multi-dimension `cost_daily` aggregation with micro-dollar Decimal fidelity |
| `tests/api/test_burn_rate_route.py` | Feature-local REST handler schema and window validation before global registration |
| `tests/mcp/test_burn_rate_tool.py` | Feature-local MCP handler delegation and exact schema before registry integration |
| `tests/cli/test_burn_rate.py` | Feature-local CLI JSON/human rendering and argument bounds before dispatcher integration |
| `tests/tui/test_cost_screen.py` | Shared model source delegation plus Cost-screen Pilot rendering of forecast, breach, and data state |
| `tests/unit/finops/test_reservations.py` | Reservation recommender: on-demand fallback, warm-pool savings, overflow handling, blocked demand, burn-rate metadata, deterministic serialization, stable tie-break, candidate validation |
| `tests/property/test_reservations_properties.py` | Property-based invariant: selected eligible plan has the minimum total cost |
| `tests/cost/test_circuit_breaker.py` | State transitions (closed/open/half-open), hysteresis, downgrade vs block, runway edge cases, determinism, reset |
| `tests/property/test_circuit_breaker_properties.py` | Hypothesis: valid action/state invariants, block-only-when-headroom-low, runway monotonicity with burn rate |
| `tests/cost/test_sub_budgets.py` | Sub-budget config validation, sub-budget gate admit/reject, chargeback attribution, snapshot serialization |
| `tests/property/test_sub_budget_properties.py` | Property-based invariants: allocation sum ≤ total, chargeback total = parts, remaining non-negative |
| `tests/property/test_reconcile_cost_properties.py` | Property-based invariants: total adjustment equals provider-recorded delta and output is order-invariant |
| `tests/integration/test_budget_gate.py` | Concurrent launch, overspend under load |
| `tests/integration/test_budget_overspend_concurrency.py` | Budget race-condition hardening |
| `tests/integration/test_idempotency_key_concurrency.py` | Idempotency key dedup under concurrency |
| `tests/chaos/test_db_outage_fail_closed.py` | Budget gate behaviour when DB is unavailable |
| `tests/test_full_cost_path.py` | End-to-end cost estimation → threshold alert |
| `tests/property/test_simulator_properties.py` | What-if budget headroom monotonicity as selected-provider rate increases |

## 8. Dependencies

**Intra-pitwall imports:**

| Module | Imports from |
|--------|-------------|
| `budget_gate.py` | `pitwall.core.ids.ulid_new` |
| `simulator.py` | `pitwall.routing.{PlanningContext,build_production_plan}`, `pitwall.cost.estimator.quote_cost`, `pitwall.core.models.Capability` |
| `reservations.py` | `pitwall.cost.simulator.{WhatIfSimulator,WhatIfWorkload}`, `pitwall.finops.burn_rate.BurnRateForecast` |
| `sync_gate.py` | `pitwall.core.idempotency.reserve_idempotency_key`, `pitwall.core.models.Capability`, `pitwall.cost.{budget_gate,estimator}` |
| `alerts.py` | `pitwall.cost.notifications.{NotificationResult,Notifier,send_notification_safely}` |
| `hibernate_alerts.py` | `pitwall.cost.notifications.{Notifier,send_notification_safely}` |
| `exporter.py` | `pitwall.config.require_runtime_env` |
| `billing_read.py` | `pitwall.runpod_client.graphql.{RunpodCreditsBalance,RunpodGraphQLClient}`, `pitwall.cost.budget_gate.BudgetGate` (via `BudgetGateLike` protocol) |
| `reconcile_cost.py` | `asyncpg` for the optional `cost_daily` adapter; pure reconciliation uses only stdlib `datetime`, `dataclasses`, and `decimal` |
| `read_models.py` | stdlib `datetime`, `dataclasses`, and `decimal`; no transport dependency |
| `core/cost_reporting.py` | `asyncpg` plus `pitwall.cost.read_models` |

**External libraries:**

| Library | Used by |
|---------|---------|
| `asyncpg` | `budget_gate`, `sync_gate`, `alerts`, `exporter`, `reconcile_cost` (Postgres connections/pools) |
| `httpx` | `alerts`, `hibernate_alerts` compatibility-only `http_client` type hints (`pitwall.cost.alerts`, `pitwall.cost.hibernate_alerts`) |
| `resend` | optional `email` extra; lazily imported by `ResendNotifier.send` (`pyproject.toml`, `pitwall.cost.notifications:send`) |
| `prometheus_client` | `exporter` (Gauge, generate_latest) |
| `fastapi` / `starlette` | `exporter` (HTTP app) |
| `uvicorn` | `exporter.main()` |
| `decimal.Decimal` | all modules handling money (stdlib) |
