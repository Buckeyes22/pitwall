# Capability Routing & Provider Resolution

## 1. Purpose & Scope

Maps an inference request to an ordered provider chain. Core layers:

- **Production planner** (`src/pitwall/routing/production.py`): `build_production_plan` is the pure,
  stateless planner that returns a `ProductionRoutePlan`; `ProductionRoutingService` wraps it with
  persistence, budget admission, and provider calls.
- **Replay substrate** (`src/pitwall/routing/context.py`): `PlanningContext` carries the time,
  provider snapshots, availability, and quota data a plan is built from.
- **Runtime resolver** (`src/pitwall/resolver/`): async Stage 1+2 resolution and provider URL construction.
- **OpenAI chain and fallback** (`src/pitwall/routing/openai.py`, `routing/fallback.py`): the ordered
  provider chain for the OpenAI-compatible proxy and its async fallback executor.
- **Free-pool gates** (`quota.py`, `lockout.py`, `zero_cost.py`, `cascade_seed.py`, `affinity.py`).

Entry points: `build_production_plan` (pure), `stage12_elimination` (pure), and `resolve_capability`
(async, repo-backed). The what-if simulator (`pitwall.cost.simulator:WhatIfSimulator`) replays plans
through `build_production_plan`.

---

## 2. Components

### `routing/types.py` — Routing DTOs

Frozen/slotted dataclasses shared by the constraint, scoring, and planner stages. The plan, candidate, elimination, and escape-hatch types live in `routing/production.py` (below).

| Type | Fields |
|---|---|
| `RoutingRequest` | `capability_name`, `payload_bytes`, `required_gpu_class`, `required_region`, `required_volume_id`, `hints: Hints`, `capability_id`, `required_cuda_min`, `required_cuda_version`, `stream: bool`; property `payload_mb` |
| `Hints` | `latency_sensitive`, `cost_sensitive`, `region_preference`, `cache_key` |
| `ObservedMetrics` | `recent_error_rate`, `last_cache_key` |
| `ConstraintResult` | `provider_id`, `passed`, `reason`, `reasons` |
| `ScoreExplanation` | `provider_id`, `base_score`, `latency_penalty`, `warm_worker_bonus`, `cost_penalty`, `region_bonus`, `recent_error_penalty`, `quota_headroom_bonus`, `reset_proximity_bonus`, `affinity_bonus`, `quota_reason`, `trains_on_prompts`, `priority_multiplier`, `score_before_multiplier`, `final_score`; `to_dict()` |

`EliminationReason` is the Stage 1 enum: `CAPABILITY_MISMATCH`, `REGION_MISMATCH`, `CUDA_MISMATCH`, `GPU_CLASS_MISMATCH`, `PAYLOAD_TOO_LARGE`. The planner's later reasons (`disabled`, `health_unavailable`, `health_cooldown`, `quota_ineligible`, `model_locked_out`, `capacity_unavailable`, and the rest) are `RouteElimination.reason` strings in `routing/production.py`. `CapacityReason` is the literal set `missing_capacity_key`, `available`, `capacity_unknown`, `capacity_unavailable`, `resident`, `slot_free`, `would_evict`, `strict_slots_blocked`, `concurrency_limited`.

**Invariants**: `ConstraintResult` normalizes `reason` and `reasons` together (a passing result has neither). `ScoreExplanation.to_dict()` is JSON-serializable.

---

### `routing/constraints.py` — Stage 1 hard constraints (pure)

```python
DEFAULT_LB_MAX_PAYLOAD_MB = Decimal("30")  # serverless_lb default

def evaluate_hard_constraints(request, provider, *, capability=None, capability_id=None) -> ConstraintResult
```

**Five constraints in order:**
1. **Capability mismatch** — `capability_name`/`capability_id` must match provider's config (`capability_name`, `capability`, `capability_id`).
2. **Region mismatch** — `required_region` vs provider `region`; `required_volume_id` vs `volume_id`/`networkVolumeId`/`required_volume`.
3. **CUDA mismatch** — if `required_cuda_min`/`required_cuda_version` set, provider must declare compatible version in `allowed_cuda_versions`/`allowedCudaVersions`/`cuda_min`/`cuda_version`/`cuda`. Version compared as numeric tuple `(12, 4)`; string equality fallback.
4. **GPU class mismatch** — if `required_gpu_class` set, provider declares candidates via `gpu_class`/`gpu_type`/`gpu_type_id` fields or `gpu_classes`/`gpu_types`/`gpuTypeIds`/`gpu_type_priority` config lists. Token normalization strips `NVIDIA GEFORCE GENERATION`; suffix match (≥4 chars) accepted.
5. **Payload too large** — if `payload_bytes` known and provider has `max_payload_mb` or is `serverless_lb` (default 30 MB), payload must not exceed limit.

**Invariant**: all pure functions; `evaluate_hard_constraints` returns `passed=True` only when zero constraints fire.

---

### `routing/cooldown.py` — Provider health cooldown state machine (pure)

```python
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_INITIAL_COOLDOWN = timedelta(minutes=5)
DEFAULT_ESCALATED_COOLDOWN = timedelta(minutes=15)
DEFAULT_COOLDOWN_POLICY = CooldownPolicy()

@dataclass(frozen=True)
class ProviderCooldownState:
    consecutive_failures: int = 0
    cooldown_trips: int = 0
    cooldown_until: datetime | None = None
    health_status: str = "unknown"

class CooldownStateMachine:
    def record_success(state, *, now=None) -> ProviderCooldownState
    def record_failure(state, *, now=None) -> ProviderCooldownState
    def apply_probe_result(state, *, passed, now=None) -> ProviderCooldownState

def state_from_provider(provider) -> ProviderCooldownState
def is_in_cooldown(state, *, now=None) -> bool
def cooldown_duration_for_trip(cooldown_trip, *, ...) -> timedelta
def to_provider_patch(state) -> dict[str, object]
# Aliases: record_success, record_failure, next_cooldown_state, is_provider_in_cooldown
```

**Transitions**: On success → `consecutive_failures=0, cooldown_trips=0, cooldown_until=None, health_status="healthy"`. On failure → increment `consecutive_failures`; every `failure_threshold` (3) trips triggers a new cooldown. Trip 1 → 5 min; trips ≥ 2 → 15 min. If already in cooldown window, state returned unchanged.

**Invariants**: `cooldown_trip >= 1`; `initial_cooldown > 0`; `escalated_cooldown >= initial_cooldown`; all datetimes timezone-aware UTC.

---

### `routing/scoring.py` — Stage 3 scoring

```python
def score_provider(provider, hints: Hints | None = None, observed: ObservedMetrics | None = None) -> float
def explain_score(provider, hints=None, observed=None, *, quota_snapshot=None, now=None,
                  w_quota=10.0, w_reset=2.5) -> ScoreExplanation
```

**Formula** (`explain_score`):
```
base_score            = 100.0
latency_penalty       = cold_start_p50_ms / 100          (latency_sensitive only)
warm_worker_bonus     = 20.0                             (latency_sensitive AND warm_workers >= 1)
cost_penalty          = cost_per_second_active * 10_000  (cost_sensitive only)
region_bonus          = 15.0                             (region_preference == provider.region)
recent_error_penalty  = recent_error_rate * 50
quota_headroom_bonus  = w_quota * headroom               (only with quota_snapshot and now)
reset_proximity_bonus = w_reset * reset_proximity        (only with quota_snapshot and now)
affinity_bonus        = affinity_bonus(provider, hints, observed)

score_before_multiplier = base - latency + warm - cost + region - error + quota_headroom
                          + reset_proximity + affinity
final_score             = score_before_multiplier * priority_multiplier
```
Fields come from provider attributes or `config["…"]`; `priority_multiplier` defaults to `1.0`. `recent_error_rate` is at most 1 and all numerics are finite and non-negative. `score_provider` returns `explain_score(...).final_score`; the explanation also records `trains_on_prompts` from the zero-cost verdict.

---

### `routing/production.py` — Production planner and service

`pitwall.routing.production:build_production_plan` is the pure planner: it consumes immutable
capability and provider snapshots and returns a deterministic `ProductionRoutePlan` without I/O.
`pitwall.routing.production:ProductionRoutingService` adds repository reads, pre-spend inspection,
budget admission, persistence, and the narrow provider adapter calls. A route preview performs no
provider egress.

```python
def build_production_plan(*, capability, providers, payload, operation, registry, now, mode,
                          weights, max_attempts, provider_id=None, budget_limit_usd=None,
                          openai_lease_proxy=False, openai_attempt_order=None, context=None,
                          own_pod_usd_per_hour=None, gpu_class=None,
                          lockouts=None) -> ProductionRoutePlan

def stage12_elimination(request, provider, *, capability, now) -> RouteElimination | None
```

**Elimination order** (each eliminated provider gets one `RouteElimination` with a stage and reason):
1. adapter lookup: `capability_unsupported`; a non-`pod_lease` RunPod provider asked to lease compute: `provider_type_unsupported`.
2. `stage12_elimination`: hard constraints (`pitwall.routing.constraints:evaluate_hard_constraints`: capability, region, CUDA, GPU class, payload), then `disabled`, `health_unavailable` (status not `healthy` or `warming`), and `health_cooldown`. The capability resolver (`pitwall.resolver.service`) shares this gate.
3. `quota_ineligible` from the `QuotaSnapshot` on the `PlanningContext`; `model_locked_out` from the `lockouts` snapshot.
4. Payload incompatibility, `capacity_unavailable`, and `transport_unsupported`.

**Ranking**: in `priority` mode a candidate's objective is the provider `priority`; in `weighted` mode it is `weights.cost x ceiling cost + weights.latency x latency_ms`, all Decimal. Each `ProductionRouteCandidate` carries its `objective` and `score_components`. The chain is the ranked primary plus fallbacks, capped at `max_attempts` (1 to 10); attempt backoff is `attempt_backoff_s`. When every free pool is exhausted, `EscapeHatch` carries the `pitwall serve` proposal.

Eliminations are `RouteElimination(provider_id, adapter_id, reason, stage)` values, sorted for a stable plan. Model lockouts arrive as the `lockouts` snapshot (the caller reads the process-global table), so two calls with the same arguments always produce the same plan.

**Validation** (`ValueError`): `mode` not `priority` or `weighted`; `max_attempts` outside 1 to 10 or boolean; `openai_attempt_order` given as a string.

---

### `routing/context.py` — Replay substrate / `PlanningContext`

`PlanningContext` is the deterministic replay seam for planner, resolver, and simulator calls. It carries:

- `now: datetime` — always timezone-aware UTC.
- `providers` — immutable provider snapshots, detached from later source-object mutation.
- `capability` — copied capability metadata used by Stage 1 constraints when not supplied directly.
- `availability_snapshot` / `capacity_snapshot` — immutable Stage 4 RunPod availability values.

**Live constructor**:

```python
context = PlanningContext.live()
plan = build_production_plan(
    capability=capability,
    providers=providers,
    payload=payload,
    operation=operation,
    registry=registry,
    now=context.now,
    mode=mode,
    weights=weights,
    max_attempts=max_attempts,
    context=context,
)
```

`PlanningContext.live()` captures wall-clock UTC `now` and snapshots the current process-global RunPod availability cache. When callers omit `context`, `resolve_capability` constructs the same live context internally, so existing production behavior is preserved.

**Replay constructor**:

```python
context = PlanningContext.replay(
    now=historical_utc_datetime,
    providers=historical_providers,
    capability=historical_capability,
    availability_entries=[
        ("US-KS-2", "NVIDIA L4", "SECURE", 1, False),
    ],
)
plan = build_production_plan(
    capability=historical_capability,
    providers=historical_providers,
    payload=payload,
    operation=operation,
    registry=registry,
    now=context.now,
    mode=mode,
    weights=weights,
    max_attempts=max_attempts,
    context=context,
)
```

Replay callers may pass `availability_snapshot=AvailabilitySnapshot(...)` instead of tuple entries. The same `PlanningContext` must produce byte-identical `RoutePlan.to_dict()` output across repeated calls, independent of wall-clock time, later provider mutations, or later global availability-cache changes.

**Discovery integration:** `pitwall.runpod_client.discovery.GpuDiscoveryService` calls `gpu_types()` and `datacenters()` through the GraphQL client, normalizes the response into `GpuDiscoverySnapshot`, and can flatten it into `AvailabilitySnapshot` via `snapshot.to_availability_snapshot()`.  This lets a background refresh task keep the global availability cache warm, while replay callers freeze a historical snapshot and pass it to `PlanningContext.replay(...)` without touching live GraphQL.

**Downstream use**: `pitwall.cost.simulator:WhatIfSimulator(context, mode=, weights=, lockouts=)` replays `build_production_plan` against a `PlanningContext.replay(...)` with explicit historical or hypothetical provider, quota, and availability data, so nothing is read from process-global tables.

---

### `routing/coalescing.py` — In-flight request coalescing

```python
class AsyncRequestCoalescer[T]:
    async def run(self, key: str, execute: Callable[[], Awaitable[T]]) -> T

def build_inference_coalescing_key(*, idempotency_key, capability_id,
                                   provider_id, capability_params,
                                   capability_class) -> str
```

`AsyncRequestCoalescer` collapses concurrent duplicate work in one API process.
The first caller for a key owns the execution; concurrent callers await the same
future and receive the same result. If the owner raises, the same exception is
observed by all waiters. The key is evicted on success, failure, or cancellation,
so only in-flight requests coalesce.

Requests with an idempotency key coalesce by key and content. Anonymous requests coalesce only
when the answer cannot differ between callers: embedding and rerank capabilities by content;
LLM, vision, and transcription capabilities only with an explicit `temperature: 0` and at most one
choice (`n`). Every other anonymous request (sampling on, custom or GPU-lease capabilities) gets a
key of its own, so no caller receives another caller's sample. The route reads the capability's
class through `ProductionRoutingService.capability_class`.

`POST /v1/inference` uses this around the shared production-service execution after request-model
validation. The service performs the single pre-spend decision, idempotency lookup, provider
planning, budget admission, adapter call, persistence, and optional trace emit. A burst of
identical requests therefore produces one admitted upstream execution and one trace attempt.
Planning drops a provider whose cost ceiling exceeds the remaining budget
(`budget_unavailable`). When that leaves no route, the service raises `BudgetRejected` for the
cheapest dropped ceiling, not `no_providers_available`, so an exhausted budget answers with the
budget contract (HTTP 402 on `/v1/inference`) before any provider call.

**Keying contract**:

- Key content includes the resolved `capability_id`, selected `provider_id`, and
  canonical redacted `capability_params`.
- Requests with an `Idempotency-Key` use a scope of
  `hash(idempotency_key) + hash(content)`. Different idempotency keys never
  share work, even when payloads match.
- Anonymous requests use `hash(content)`.
- Raw idempotency keys are not stored in coalescing keys.

This is intentionally process-local. Multi-process or multi-host request
coalescing remains the responsibility of durable idempotency and database
constraints.

---

### `routing/openai.py` — OpenAI-compatible chain

```python
def openai_provider_types() -> frozenset[str]   # union of every registered adapter's openai_proxy_types
DEFAULT_OPENAI_MAX_ATTEMPTS = 3
MAX_OPENAI_ATTEMPTS = 3

def resolve_openai_provider_chain(providers, *, primary_provider_id=None,
                                  max_attempts=3, now=None) -> OpenAIProviderChain
def openai_base_url_for_provider(provider) -> str | None
def build_openai_url(openai_base_url, path) -> str
# Aliases: resolve_openai_chain, resolve_provider_chain, resolve_openai_provider_ids
```

**URL derivation from `runpod_endpoint_id`**: `serverless_lb` → `https://{id}.api.runpod.ai/openai/v1`; `serverless_queue`/`public_endpoint`/`None` → `https://api.runpod.ai/v2/{id}/openai/v1`; `pod_lease` → derived from provider config written by the lease readiness hook: `https://{active_pod_id}-{openai_proxy_port}.proxy.runpod.net/v1` (`routing/openai.py::pod_lease_base_url`); `None` while no lease is armed. `config["openai_base_url"]` overrides for the other types only. `build_openai_url` strips leading `/` and `v1/` from path to prevent `/v1/v1/` duplication.

Providers whose derived base URL is `None` are excluded from the chain (`ordered_openai_providers`).

**Ordering**: filters to `openai_provider_types()`, enabled, healthy, not in cooldown; sorts `(priority, name, id)`; primary first; explicit `fallback_chain` appended; remaining candidates fill slots up to `max_attempts`.

**Validation** (`ValueError`): `max_attempts < 1` or boolean; `primary_provider_id` not in available candidates.

---

### `routing/fallback.py` — Async OpenAI fallback executor

```python
DEFAULT_OPENAI_FALLBACK_BUDGET_S = 5.0

@dataclass(frozen=True)
class OpenAIProxyRequest:
    method, path, headers: Mapping, body: bytes, client: httpx.AsyncClient
    fallback_budget_s: float = 5.0, max_attempts: int = 3

@dataclass(frozen=True)
class OpenAIProxyResult:
    response: httpx.Response, provider: Provider
    attempted_provider_ids: tuple[str, ...], elapsed_s: float

class OpenAIProxyExecutionError(RuntimeError):
    attempted_provider_ids, cause, attempted_errors

async def execute_openai_with_fallback(request_ctx, providers: list[Provider],
                                        *, on_attempt=None) -> OpenAIProxyResult
```

Retries only on 5xx or transport errors before response headers; 4xx returned immediately. Budget enforced per-attempt. `on_attempt` callback called after each attempt with current `attempted_provider_ids`. Raises `OpenAIProxyExecutionError` on exhaustion (includes `attempted_provider_ids`, `attempted_errors`, `cause`). Validation: `max_attempts >= 1`, `fallback_budget_s > 0`.

---

### `resolver/service.py` — Async Stage 1+2 resolver

```python
class CapabilityRepositoryLike(Protocol):
    async def get(capability_id) -> Capability | None
    async def get_by_name(name) -> Capability | None

class ProviderRepositoryLike(Protocol):
    async def get(provider_id) -> Provider | None
    async def list(*, capability_id=None, enabled_only=False, provider_type=None,
                   limit=100, offset=0) -> list[Provider]

@dataclass(frozen=True)
class Stage12Resolution:
    capability: Capability, provider: Provider
    eligible_providers: tuple[Provider, ...]
    eliminated: tuple[RouteElimination, ...] = field(default_factory=tuple)

async def resolve_capability(capability_name, *, capability_repo, provider_repo,
                             provider_id=None, request=None, context=None, now=None,
                             provider_limit=100) -> Stage12Resolution
def select_stage12_provider(request, providers, *, capability,
                            context=None, now=None) -> Stage12Resolution
```

`resolve_capability`: look up by name then id → `CapabilityNotFoundError`. If `enabled=False` → `CapabilityDisabledError`. Fetch providers (single by `provider_id` or list from repo). Delegate to `select_stage12_provider`: Stage 1 → Stage 2 → lowest `priority` survivor. Raises `NoHealthyProviderError` if none survive.

Serialized Stage 1+2 decisions include `selected_adapter_id` and an ordered
`eligible_provider_adapters` list alongside the retained provider-id fields. `ResolvedProvider`
likewise emits `adapter_id`, so transports can report which static adapter owns execution without
re-deriving it from `ProviderType` or RunPod-specific columns.

---

### `resolver/provider_urls.py` — Provider URL builder with SSRF protection

```python
_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")

def openai_base_url(provider) -> str
def queue_url(provider, path: str = "") -> str
def lb_url(provider, path: str = "/") -> str
def public_endpoint_url(provider) -> str
def provider_url(provider) -> str
```

| ProviderType | Function | URL pattern |
|---|---|---|
| `serverless_queue` | `queue_url` | `https://api.runpod.ai/v2/{endpoint_id}` |
| `serverless_lb` | `lb_url` | `https://{endpoint_id}.api.runpod.ai{path}` |
| `public_endpoint` | `public_endpoint_url` | `https://api.runpod.ai/v2/{endpoint_id}/openai/v1` |
| `pod_lease` | raises `ValueError` | derived from `active_pod_id` + `openai_proxy_port` when armed; else excluded |

`_require_endpoint_id` validates `runpod_endpoint_id` against allow-list regex. First char must be alphanumeric; remaining 1–63 chars alphanumeric/`-`/`_` only. Blocks host injection, metadata IP (`169.254.169.254`), path traversal (`../..`), userinfo (`@`), port injection. Raises `ValueError` on absent/invalid id.

---

### `resolver/exceptions.py` / `resolver/result.py`

```python
class ResolverError(RuntimeError):
    error_code: str = "resolver_error"
    def to_dict(self) -> dict[str, Any]: ...

class CapabilityNotFoundError(ResolverError): error_code = "capability_not_found"
class CapabilityDisabledError(ResolverError): error_code = "capability_disabled"
class NoHealthyProviderError(ResolverError): error_code = "no_healthy_provider"
class ProviderNotFoundError(ResolverError): error_code = "provider_not_found"
class ProviderExhaustedError(ResolverError): error_code = "provider_chain_exhausted"

@dataclass(frozen=True) class ResolvedProvider: provider: Provider; is_fallback: bool = False
@dataclass(frozen=True) class ResolutionFailure: reason, capability_name, providers_tried: list[str]
ResolutionResult = ResolvedProvider | ResolutionFailure
```

---

### `routing/quota.py` — Free-pool quota snapshot, Stage-2 gate, and Stage-3 terms

```python
@dataclass(frozen=True, slots=True) class QuotaRecord:
    provider_id, pool_key, free_type, window_start, reset_at,
    budget_units: Decimal|None, used_units: Decimal, tos_verdict, evidence, updated_at
    def to_dict(self) -> dict[str, Any]; from_dict(cls, raw) -> QuotaRecord

@dataclass(frozen=True, slots=True) class QuotaSnapshot:
    records: tuple[QuotaRecord, ...]                  # sorted by (provider_id, pool_key)
    def empty(cls) -> QuotaSnapshot; get(self, provider_id, pool_key) -> QuotaRecord|None
    def to_dict(self) -> dict[str, Any]; from_dict(cls, raw) -> QuotaSnapshot

@dataclass(frozen=True, slots=True) class QuotaTerms: headroom: float = 0.0; reset_proximity: float = 0.0

def quota_eligible(provider, snapshot, now) -> tuple[bool, str|None]
def quota_score_terms(provider, snapshot, now) -> QuotaTerms
```

The pool key comes from `provider.config["gateway"]["catalog"]["pool_key"]`. `quota_eligible`
gates only `zero`-priced providers; all other pricing kinds are eligible unconditionally. The
truth table for zero-priced providers:

| Condition | Result | Reason |
|---|---|---|
| No snapshot record for `(provider_id, pool_key)` | ineligible | `zero-priced without catalog evidence` |
| `tos_verdict == "avoid"` | ineligible | `tos-avoid` |
| `reset_at <= now` with a budget set | eligible | window rolled; the poller re-zeros `used_units` on its next tick |
| `used_units >= budget_units` (budget set) | ineligible | `quota-exhausted` |
| otherwise | eligible | — |

`quota_score_terms` returns `headroom = (budget_units - used_units) / budget_units` clamped to
`0..1` (`1.0` when unbudgeted) and `reset_proximity = 1 - time_to_reset / window` clamped to
`0..1` (`0.0` when the window is unknown). `QuotaSnapshot` is carried on `PlanningContext`, is
part of the plan body hash, and is degraded to an empty snapshot when the quota repository is
unavailable so missing quota state never breaks provider delivery.

---

### `routing/lockout.py` — Per-(provider, model) lockouts

```python
def backoff_for(failures: int) -> dt.timedelta   # min(120s * 2**(failures-1), 30min)

@dataclass(frozen=True, slots=True) class LockoutKey: provider_id, model_id
@dataclass(frozen=True, slots=True) class LockoutState: failures=0; locked_until=None; reason=None; permanent=False

class LockoutTable:
    record_failure(key, *, now, reason, reset_at=None) -> LockoutState
    record_success(key, *, now) -> LockoutState
    is_locked(key, *, now) -> bool
    snapshot() -> dict[str, dict[str, Any]]; clear() -> None

def get_lockout_table() -> LockoutTable           # process-wide module singleton
def model_lockout_key(provider) -> LockoutKey | None   # from config.gateway.model_id
```

Lockouts mirror OmniRoute's `accountFallback` semantics for the `(provider_id, gateway.model_id)`
tuple. `record_failure` treats `permanent_ban` as never expiring, `quota_exhausted` with a
`reset_at` as locked until that reset, and everything else with the doubling 120 s → 30 min cap
backoff. `record_success` halves the failure count and lifts the lockout; permanent states are
never cleared. The table is in-memory authoritative and thread-safe; the reconciler quota poll
flushes `snapshot()` into `provider_quotas.evidence["lockout"]` for observability only. Stage 2
eliminates locked keys with `model_locked_out`, and the production executor records failures on
typed `QuotaExhausted` signals and successes after completed attempts.

---

### `routing/zero_cost.py` — Strict zero-cost filter

```python
@dataclass(frozen=True, slots=True) class ZeroCostVerdict: allowed: bool; reason: str|None; trains_on_prompts: bool

def zero_cost_verdict(provider) -> ZeroCostVerdict
```

ADR 0007 rule 2 as one pure function over `provider.config["gateway"]["catalog"]`. Non-zero
pricing kinds are always allowed. Zero-priced providers are rejected with the first matching
reason and allowed only on the last row:

| Condition | Verdict |
|---|---|
| no catalog evidence | `no-catalog-evidence` |
| `tos == "avoid"` | `tos-avoid` |
| `tos == "unknown"` | `tos-unknown` |
| `eligibility_gate` set | `eligibility-gated` |
| `free_type == "discontinued"` | `discontinued` |
| `free_type == "keyless"` or `hard_stop_guaranteed` | allowed |
| otherwise | `hard-stop-not-guaranteed` |

`trains_on_prompts` is surfaced either way so privacy-sensitive consumers can display it. Today
only Stage 3 calls `zero_cost_verdict` (to fill `ScoreExplanation.trains_on_prompts`); the
planner's Stage 2 gate for zero-priced providers is `quota_eligible` (`quota_ineligible`).

---

### `routing/cascade_seed.py` — Tier ladder and escape hatch

**File:** `src/pitwall/routing/cascade_seed.py`

```python
def ladder_rank(provider) -> int          # 0 own-serve, 1 keyless, 2 budgeted free, 3 cheap metered, 4 other
def order_ladder(providers) -> tuple
def escape_hatch_message(plan, *, quota_snapshot, now, own_pod_usd_per_hour, gpu_class) -> str | None
```

The seed extractor orders `fallback_chain` entries with `order_ladder`. Emergency free descent
(research §9.6) needs no separate mechanism: zero-priced keyless candidates carry a zero cost
ceiling, so they survive the budget elimination and the attempt reservation loop even when every
metered rung is budget-blocked (`tests/routing/test_production_routing.py::test_keyless_floor_survives_when_paid_rungs_are_budget_blocked`).
`escape_hatch_message` proposes, never executes, a `pitwall serve` command when every
zero-priced candidate is `quota_ineligible` and no metered candidate remains; it accepts both
bare and `stage:reason` elimination strings.

### `routing/affinity.py` — Cache-affinity pinning

```python
AFFINITY_BONUS = 5.0
def affinity_bonus(provider, hints, observed) -> float
```

Returns `AFFINITY_BONUS` when the provider catalog declares `prompt_cache: true` and
`hints.cache_key` equals `observed.last_cache_key`, else `0.0`. This pins prompt-cache-heavy
pools: a repeated cache key keeps the previous provider ahead of a marginally cheaper one, and
the cache key is part of the hints, so plan identity changes with it.

---

## 3. Routing Pipeline

```
RoutingRequest + Iterable[Provider]
  -> Stage 1: evaluate_hard_constraints (cap/region/CUDA/GPU/payload)
  -> Stage 2: stage12_elimination (enabled? health? cooldown?)
              + quota? lockout? capacity? transport? -> eligible
  -> Stage 3: rank (priority, or weights.cost x cost + weights.latency x latency_ms)
  -> Chain: primary + fallbacks, capped at max_attempts
  -> ProductionRoutePlan(candidates, eliminations, escape_hatch)
```

Stage 2 also carries the free-pool eliminations: quota ineligibility from the `QuotaSnapshot`
(`quota_ineligible`) and an active per-(provider, model) lockout (`model_locked_out`). The legacy
`pitwall.routing.scoring:explain_score` still implements the `w_quota` (default `10.0`) and `w_reset`
(default `2.5`) headroom terms and the 5.0 cache-affinity bonus.

Backoff: attempt 1 = 0s; later attempts follow `attempt_backoff_s`.

**Provider URL resolution** (`resolver/provider_urls.py`):
1. `_require_endpoint_id(provider)` — validate against allow-list regex
2. Dispatch by `provider.provider_type` to `lb_url`/`queue_url`/`public_endpoint_url`
3. Interpolate validated id into URL template

`routing/openai.py:openai_base_url_for_provider` derives `pod_lease` URLs only from the active pod facts; for other provider types it checks `config["openai_base_url"]` first and derives from `runpod_endpoint_id` if absent. `build_openai_url` prevents `/v1/v1/` duplication.

---

## 4. Public Interfaces

```python
# Production planner
def build_production_plan(*, capability, providers, payload, operation, registry, now, mode,
                          weights, max_attempts, ...) -> ProductionRoutePlan
def stage12_elimination(request, provider, *, capability, now) -> RouteElimination | None

# Async capability resolver
async def resolve_capability(capability_name, *, capability_repo, provider_repo,
                              provider_id=None, request=None, context=None, now=None,
                              provider_limit=100) -> Stage12Resolution
def select_stage12_provider(request, providers, *, capability,
                              context=None, now=None) -> Stage12Resolution

# OpenAI chain
def resolve_openai_provider_chain(providers, *, primary_provider_id=None,
                                   max_attempts=3, now=None) -> OpenAIProviderChain
def openai_base_url_for_provider(provider) -> str | None
def build_openai_url(openai_base_url, path) -> str

# Async fallback executor
async def execute_openai_with_fallback(request_ctx: OpenAIProxyRequest,
                                        providers: list[Provider],
                                        *, on_attempt=None) -> OpenAIProxyResult

# Provider URL builders
def openai_base_url(provider) -> str
def queue_url(provider, path: str = "") -> str
def lb_url(provider, path: str = "/") -> str
def public_endpoint_url(provider) -> str
def provider_url(provider) -> str
```

---

## 5. Configuration

### Module-level constants (no env vars)

| Constant | Module | Default |
|---|---|---|
| `DEFAULT_LB_MAX_PAYLOAD_MB = 30` | `routing/constraints.py` | Decimal("30") MB for serverless_lb |
| `DEFAULT_FAILURE_THRESHOLD = 3` | `routing/cooldown.py` | consecutive failures before first cooldown |
| `DEFAULT_INITIAL_COOLDOWN = 5min` | `routing/cooldown.py` | `timedelta(minutes=5)` |
| `DEFAULT_ESCALATED_COOLDOWN = 15min` | `routing/cooldown.py` | `timedelta(minutes=15)` |
| `DEFAULT_OPENAI_MAX_ATTEMPTS = 3` | `routing/openai.py` | max OpenAI chain attempts |
| `DEFAULT_OPENAI_FALLBACK_BUDGET_S = 5.0` | `routing/fallback.py` | total time budget for OpenAI fallback |

The production path uses typed `PITWALL_ROUTING_MODE`, `PITWALL_ROUTING_WEIGHTS`, and
`PITWALL_ROUTING_MAX_ATTEMPTS` settings documented in
[production routing](../operator/production-routing.md); capability-specific weight lookup remains
deterministic and audited.

### Provider config fields that influence routing

| Config key | Purpose |
|---|---|
| `capability_name`, `capability`, `capability_id` | Stage 1 capability |
| `region` | Stage 1 region |
| `enabled`, `health_status` | Stage 2 |
| `cooldown_until`, `consecutive_failures`, `cooldown_trips` | Stage 2 cooldown |
| `priority`, `priority_multiplier` | Stage 3 sort and multiplier |
| `cold_start_p50_ms`, `warm_workers`, `cost_per_second_active`, `recent_error_rate` | Stage 3 formula |
| `gpu_class`, `gpu_type`, `gpu_classes`, `gpu_type_priority` | Stage 1 GPU class |
| `allowed_cuda_versions`, `cuda_min`, `cuda_version` | Stage 1 CUDA |
| `max_payload_mb` | Stage 1 payload limit |
| `fallback_chain`, `fallback_provider_ids`, `fallbacks`, `fallback_for` | Fallback chain construction |
| `runpod_endpoint_id`, `endpoint_id` | URL derivation; SSRF-validated |
| `openai_base_url` | URL override |
| `dataCenterIds`, `datacenter`, `cloud_type`, `gpu_count` | Stage 4 capacity keys |

---

## 6. Failure Modes & Error Types

| Condition | Error | Location |
|---|---|---|
| `now` without timezone | `ValueError` | `routing/context.py`, `resolver/service.py` |
| `context` combined with `now` | `ValueError` | `resolver/service.py` |
| `primary_provider_id` not in candidates | `ValueError` | `openai.py` |
| `max_attempts` < 1 or boolean | `ValueError` | `openai.py` |
| `runpod_endpoint_id` absent/invalid | `ValueError` | `pitwall.resolver.provider_urls` |
| `pod_lease` OpenAI URL | `ValueError` | resolver `openai_base_url()` (unused by the proxy) still raises; the proxy derives pod URLs in `routing/openai.py`. |
| No provider survives S1+2 | `NoHealthyProviderError` | `service.py` |
| Capability not found | `CapabilityNotFoundError` | `service.py` |
| Capability disabled | `CapabilityDisabledError` | `service.py` |
| Explicit provider not found | `ProviderNotFoundError` | `service.py` |
| Budget exhausted (no headers) | `OpenAIProxyExecutionError` | `pitwall.routing.fallback` |

---

## 7. Testing

### `tests/property/test_routing_properties.py`
Hypothesis property tests for the production planner's invariants (`build_production_plan`, non-pod-lease providers only):
- `test_determinism` — `ProductionRoutePlan.to_dict()` stable across identical calls
- `test_partition_every_provider_ranked_xor_eliminated` — no provider lost or double-counted
- `test_attempts_bounded_and_subset_of_ranked` — attempt count ≤ `max_attempts`; attempt ids ⊆ ranked ids
- `test_priority_ranking_is_non_decreasing` — priority-mode ranks never decrease
- `test_disabled_or_unhealthy_never_attempted` — no attempt with `enabled=False` or `health_status=="unhealthy"`
- `test_max_attempts_outside_bounds_raises` — `ValueError`

### `tests/routing/` planner and gate tests
`test_planner_production.py`, `test_production_plan_purity.py`, `test_production_routing.py`, `test_hard_constraints.py`, `test_quota_gate.py`, `test_lockout.py`, `test_zero_cost.py`, `test_cooldown_state.py`, and `test_planning_context.py` cover the pure planner, its elimination reasons, and replay determinism.

### `tests/test_inference_routing.py`
Hermetic API tests. `test_inference_dry_run_routes_by_capability_name_to_priority_one_provider` — with `{unhealthy-p1, healthy-p2, healthy-p1}` selects `prov_priority_1`; verifies `provider_repo.list` with `enabled_only=True`. `test_inference_unknown_explicit_provider_returns_404` — 404 with `"provider_not_found"` for missing `provider_id`.

### `tests/security/test_provider_url_ssrf.py`
SSRF tests. `HOSTILE_IDS` includes `../../../internal`, `169.254.169.254`, `evil.example.com`, `endpoint@evil.com`, `endpoint:8080`, `runpod.ai.evil.com`. `test_lb_url_rejects_hostile_endpoint_id` / `test_queue_url_rejects_hostile_endpoint_id` verify `ValueError` per entry. `test_valid_endpoint_id_targets_only_runpod_host` confirms valid id `"eptest00000000"` → host `eptest00000000.api.runpod.ai`.

### Other files exercising this subsystem
- `tests/conftest.py` — `provider_factory` fixture, `setup_openai_proxy_app` helper
- `tests/reconciler/test_init_coverage.py` — patches `is_in_cooldown`; exercises cooldown transitions
- `tests/api/test_inference_contract.py` — imports `CapabilityDisabledError`, `NoHealthyProviderError`, `Stage12Resolution`
- `tests/api/test_e2e_sync_inference.py` — end-to-end with cooldown fields
- `tests/chaos/test_serverless_5xx.py`, `tests/chaos/test_serverless_429.py` — OpenAI proxy fallback
- `tests/perf/test_micro_benchmarks.py` — `build_production_plan` benchmarks

---

## 8. Dependencies

**From `pitwall.core`**: `ProviderType` enum; `Capability`, `Provider` models.

**From `pitwall.runpod_client`**: `AvailabilityCache`, `get_global_availability_cache` — `PlanningContext.live()` snapshots current RunPod availability before planning.

**Internal routing imports**:
- `production` → `context.PlanningContext`, `constraints.evaluate_hard_constraints`, `cooldown.is_in_cooldown`, `quota.quota_eligible`, `lockout`, `openai.openai_base_url_for_provider`
- `fallback` → `openai.openai_base_url_for_provider`, `openai_base_url`, `build_openai_url`
- `openai` → `cooldown.is_in_cooldown`
- `resolver.service` → `PlanningContext`, `routing` types, and `production.stage12_elimination`

**External libs**: `httpx` (async HTTP in fallback); standard library: `asyncio`, `datetime`, `decimal.Decimal`, `math`, `enum.Enum`, `dataclasses`, `collections.abc`, `time`.

---

## 9. Production planner and executor

`routing/production.py` is the production composition path over the existing constraint,
provider-registry, pricing, budget, guardrail, and repository contracts. Its pure
`build_production_plan` entry point accepts an explicit UTC `now`, deep-copies and orders provider
snapshots, filters hard constraints and adapter capabilities before scoring, and hashes the
canonical payload-free explanation into one stable plan ID
(`pitwall.routing.production`).

Selection has two typed modes. `priority` orders by `(priority, provider name, provider id)` for
rollout compatibility. `weighted` minimizes
`cost ceiling × cost weight + latency milliseconds × latency weight`, with priority/name/id as
stable tie breakers (`pitwall.routing.production`). Every candidate carries both Decimal components, its pricing quote, signal
provenance, and rank. Missing price and explicit unavailable capacity fail closed; absent capacity
is neutral; missing or invalid latency uses the worst known eligible value or zero when all values are absent
(`pitwall.routing.production`).

`ProductionRoutingService` owns the effectful sequence: pre-spend inspection, immutable plan,
optional webhook target validation, budget admission, plan persistence, and adapter invocation.
Synchronous execution reserves every fallback ceiling and can advance only through the persisted
attempt chain. Async submission reserves the selected ceiling and never retries an ambiguous
provider write (`pitwall.routing.production`). Preview performs neither persistence nor budget/provider
calls (`pitwall.routing.production`). Bounded lifecycle
events are derived from the workload record and report an explicit stable unsupported state rather
than creating another stream engine.

The OpenAI-compatible proxy applies the same conservative fallback rule: admission validates that
the provider snapshots exactly match the planned attempt chain and reserves the sum of every paid
attempt ceiling before the first request (`pitwall.api.routes.openai`). It asks the planner for at most the executor's three
attempts, even when the general routing setting is higher, so admission quotes the exact executable
chain (`pitwall.api.routes.openai`, `pitwall.routing.production`). The OpenAI
execution profile eliminates every candidate without a resolvable OpenAI base URL before admission,
then orders the selected ranked primary, its explicit configured fallbacks, and the remaining ranked
candidates. The `skip-primary` drill replans the exact remaining chain, so it removes only the first
attempt and retains every admitted fallback within the three-attempt cap
(`pitwall.routing.production`,
`pitwall.api.routes.openai`). Once the
outcome is known, the stored ceiling and structured quote narrow to the paid attempts actually made,
releasing every unattempted fallback reservation. The winning fallback provider replaces the
initially selected provider on completed and failed terminal workload records. Provider egress
begins only after the admitted row is proven `RUNNING`; a rejected or failed running transition
records a no-provider terminal truth-up and returns unavailable. Upstream-client construction also
fails closed with a no-provider truth-up before executor egress
(`pitwall.api.routes.openai`). Transport failure, exhausted HTTP
5xx, response-read failure, stream interruption, or cancellation after an attempt records a failed
terminal state and the attempted chain but leaves actual cost unavailable; it is never rewritten to zero. Cancellation
before any provider invocation records the narrow sourced-zero fact and releases the whole
reservation (`pitwall.api.routes.openai`).

An active, already-paid pod lease is zero incremental cost only for the OpenAI proxy execution
profile, only for sync inference, and only when its active lease and pod URL are demonstrable.
Generic sync inference excludes an active-pod-only candidate that its provider adapter cannot
execute, while compute/lease creation retains the provider's ordinary spend ceiling
(`pitwall.routing.production`). A successful
covered-lease attempt records sourced zero actual only when every attempt made was lease-covered,
releasing any paid-fallback headroom reserved but not spent. If a paid attempt preceded the lease
winner, or the covered-lease attempt failed, actual remains unavailable. Proxy responses remain
byte-streaming, so paid-provider usage is not buffered or invented for truth-up. Completion and the
success trace are persisted only after the response body is exhausted; mid-body failure or
cancellation closes both upstream objects and persists the same failed trace/audit truth with
delivered-byte count (`pitwall.api.routes.openai`).

`job_result()` is the single bounded result read for REST, MCP, CLI, and TUI. It returns stable
`job_not_terminal`, `provider_result_unavailable`, or `result_limit_exceeded` reasons and never
serializes workload input (`pitwall.routing.production`).

The full plan document and `route_plan_id` are persisted together on the workload. Existing REST
job reads and normalized MCP output include that identity. The Operations TUI calls the same pure
production builder for its safe selected-route/score summary; it never reads raw workload input or
provider credentials (`pitwall.tui.operations`).
