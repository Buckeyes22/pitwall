# Pod-Lease Lifecycle Subsystem

## 1. Purpose & Scope

Manages RunPod GPU broker lease lifecycle: creation through teardown. A lease is a stateful `Lease` row representing an allocated RunPod pod. Subsystem owns: state machine transitions, pod creation orchestration (`launch.py`), single-lease teardown (`teardown.py`), and reconciler-triggered auto-teardown on TTL expiry. Does **not** own account-wide kill switches or billing computation.

---

## 2. Components

### `src/pitwall/leases/state.py`

Defines the allowed lease state transition graph (`LEASE_STATE_TRANSITIONS`). Persisted paths must preserve it: service code validates through `transition_lease_state`, while launch-failure cleanup uses guarded SQL (`state NOT IN (...)` plus a `FOR UPDATE` recheck) whose predicate stays inside the graph.

**Public API:**

```python
def transition_lease_state(from_state: LeaseStateInput, to_state: LeaseStateInput) -> LeaseState
# Validates against LEASE_STATE_TRANSITIONS; raises IllegalLeaseTransitionError on illegal jumps.
# Returns the validated LeaseState — safe to persist.

def can_transition_lease(from_state: LeaseStateInput, to_state: LeaseStateInput) -> bool
# Returns True iff the transition is permitted.
```

**Exception hierarchy:**

```python
class LeaseTransitionError(RuntimeError):
    error_code = "lease_transition_error"


class IllegalLeaseTransitionError(LeaseTransitionError):
    error_code = "illegal_lease_transition"

    def __init__(self, from_state: LeaseState, to_state: LeaseState) -> None: ...
    def to_dict(self) -> dict[str, str]: ...


# Alias: InvalidLeaseTransitionError = IllegalLeaseTransitionError
```

**State sets:**

```python
TERMINAL_LEASE_STATES = frozenset({LeaseState.STOPPED, LeaseState.FAILED, LeaseState.EXPIRED})
ACTIVE_LEASE_STATES = frozenset(
    {
        LeaseState.CREATING,
        LeaseState.WAITING_RUNTIME,
        LeaseState.WAITING_PROBE,
        LeaseState.ACTIVE,
        LeaseState.STOPPING,
    }
)
```

**Transition map** (`LEASE_STATE_TRANSITIONS`):

| From | Allowed `to` |
|---|---|
| `CREATING` | `WAITING_RUNTIME`, `FAILED`, `STOPPING` |
| `WAITING_RUNTIME` | `WAITING_PROBE`, `FAILED`, `STOPPING` |
| `WAITING_PROBE` | `ACTIVE`, `FAILED`, `STOPPING` |
| `ACTIVE` | `STOPPING`, `EXPIRED`, `FAILED` |
| `STOPPING` | `STOPPED`, `EXPIRED`, `FAILED` |
| `STOPPED` / `FAILED` / `EXPIRED` | *(none — terminal)* |

**Invariant:** Persisted lease state must stay within `LEASE_STATE_TRANSITIONS`. Service paths validate through `transition_lease_state` before persisting; the launch-failure cleanup in `launch.py` uses guarded SQL (`state NOT IN ('stopped', 'failed', 'expired')`) with a `FOR UPDATE` recheck, which is the `FAILED` edge already present in the graph. String values are coerced via `_coerce_lease_state` raising `ValueError` for unknown strings.

---

### `src/pitwall/api/leases/launch.py`

Assembles and executes a RunPod pod launch for a `pod_lease` provider.

Every paid launch first calls the shared non-mutating
`enforce_kill_switch_admission(pool)` check, then budget admission. An account-wide
kill activation therefore prevents template or pod creation before any RunPod write.

`serve_model` resolves an active-lease replay before consulting `PITWALL_PRICE_MAX_AGE_S`.
For a branch that can launch, a fallback or over-age live GPU-price snapshot returns
`stale_price` (422) instead of creating a pod; dry-run previews retain the plan and report freshness.

**Entry points:**

```python
async def run_launch(
    *, pool, capability, provider,
    request_id: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    payload: Mapping[str, Any] | None = None,
    budget_gate: Any | None = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    request_fingerprint: str | None = None,
) -> dict[str, Any]
# Returns: backend, dry_run, pod_id, lease_id, template_id, workload_id, etc.
# A repeated idempotency_key never launches again; see "Idempotent launches" below.

async def prepare_lease_launch(pool, capability, provider, *, dry_run=False, request_id=None, extra_env=None) -> LeaseLaunchPlan
# Resolves template and assembles env/workload/placement without creating pod.
# LeaseLaunchPlan includes readiness_path: str = "/health". run_launch forwards it to
# create_pod_with_fallback for the matching readiness wait.

async def ensure_launch_template(pool, capability, provider, *, dry_run=False) -> LaunchTemplate
# Creates/retrieves RunPod template.

async def admit_lease_launch(pool, capability, provider, *, budget_gate=None, payload=None, idempotency_key=None, request_fingerprint=None) -> BudgetAdmission
# Admits through budget gate, returns workload_id (format: wkl_<ulid>) and is_new.
# A keyed admission records request_fingerprint on the new workload in the same transaction.

async def replay_idempotent_launch(workload, *, lease_repo, idempotency_key, request_fingerprint, legacy_identity=None) -> Lease
# The lease a same-key launch recorded, or idempotency_mismatch / mutation_in_progress /
# idempotency_conflict. Never launches. legacy_identity (capability id, provider id) admits a
# digest-less RunPod lease workload admitted before digests.

def estimate_lease_launch_cost(capability, provider, payload=None) -> Decimal
```

**Idempotent launches:** `POST /v1/leases`, `pitwall_lease_pod`, `POST /v1/serve`,
`pitwall_serve_model`, and `pitwall serve` accept an optional `idempotency_key`. A keyed
admission records the SHA-256 of the caller's request on its workload (`input`
`{"launch_request_sha256": ...}`) inside the budget-admission transaction: `admit_lease_launch`
for RunPod, `admit_provision` for Lambda Cloud and Vast. For a lease the request is the resolved
capability and the optional provider pin, not the planner's choice; for serve it is every
`ServeRequest` field the caller set except the key and `dry_run` (request env and start args are
already stored in clear in the provider config and cannot carry launch credentials). A launch
with no caller digest uses the capability, provider, provider config, payload, and an HMAC of
any launch credentials keyed from the RunPod API key (`default_launch_fingerprint`). When the key
already names a workload, nothing is planned, priced, written to the registry, created at the
provider, or reserved again:

| Key's workload | Result |
|---|---|
| Different digest (another request) | `idempotency_mismatch` (422) |
| No digest (admitted before digests, or by another surface) | The launch path's own replay: a RunPod lease workload of the same capability and provider with no payload replays as below, a Lambda Cloud or Vast workload goes to the adapter replay (`load_provision_replay`), anything else is `idempotency_mismatch` |
| Has a lease, in any state | That lease (the one billing the workload, or for Lambda Cloud and Vast the one the completed provision names); lease responses carry `replayed: true`, serve answers `created: false` |
| No lease, workload `queued`/`running` (first launch in flight, or stopped before recording its lease) | `mutation_in_progress` (409); retry the same key later |
| No lease, workload closed (a failed or unknown-outcome create the reconciler settled) | `idempotency_conflict` (422); the key is spent, use a new one |

A launch failure proven to have created no pod (`lease_launch_precreation_failure` at $0: a
template or plan failure, or no capacity before any pod attempt) closes the workload and
releases its key (`WorkloadRepository.fail_and_release_idempotency_key`), so a same-key retry
admits and launches again. Lambda Cloud and Vast do the same when a provision was never
dispatched or its resource was deleted (`mark_provision_failed` with the reservation released).
A failure after a pod attempt, or with an unknown outcome, keeps the key bound and is refused as
above.

A replayed lease carries the route plan the first launch recorded. That launch attaches its plan
only after it finishes (after the readiness wait), so the replay's `route_plan_id` and
`route_plan` are null until then, and stay null if that launch failed after recording its lease.

A pod-lease serve publishes `lease.ready` only after `verify_served_model` passes. A serve replay, keyed or
not, of a lease still being readied (`creating`, `waiting_runtime`, `waiting_probe`) is
`mutation_in_progress` (retry the same key) and publishes nothing. An `active` lease replays only
after a verification window (`VERIFY_REQUEST_TIMEOUT_S`, up to three requests) lists the served
model; otherwise it is `mutation_in_progress` too (`LeaseNotServing`: `reason: lease_not_serving`,
the lease `id`, and a fixed remedy to retry later or end the lease with `pitwall_stop_lease`, which
the MCP boundary passes through), and the replay never tears the lease down. A keyed lease that is
stopping or ended is `lease_state_conflict` (409, operation `serve`; use a new key). Two
concurrent same-key requests serialize on the budget lock: the loser sees the winner's workload
and digest and returns the winner's lease under the same rules, never re-planning or tearing it
down. Exactly one pod is created, including when every racer reaches admission at once
(`tests/integration/test_lease_launch_idempotency.py`).

**Revision-3 supplied templates:** A configured `template_id` is resolved against RunPod and must
name a non-serverless pod template. A supplied-template dry run does not call RunPod's template
API. Pitwall still supplies the pod's `imageName`, `dockerStartCmd`, environment, and ports at
launch; the template is not treated as the source of those launch settings.

**Dry-run template resolution:** Dry runs never write to RunPod. For image-based providers they
read the local `runpod_templates` cache by normalized template name and a deterministic SHA-256 of
image digest/tag identity, `docker_start_cmd`, ports, and sorted non-secret environment key names.
A cache hit is returned as `template_id`, while a miss returns the non-persisted literal
`"dry-run"`. Environment values and secret-shaped key names do not enter the cache hash or row.

**Model-cache volumes:** Cold model download at pod boot is implemented. Operators may attach a
per-datacenter network volume and set vLLM `HF_HOME=/workspace/hf` plus
`HF_HUB_CACHE=/workspace/hf/hub`, or llama.cpp `LLAMA_CACHE=/workspace/llama-cache`. The
`warm-volume` starts the exact catalogue serve launch with the volume attached, waits for
readiness plus `/v1/models`, then tears the lease down with `reason="warm_complete"`. This
populates the engine cache without a separate download implementation; a verification mismatch
is torn down with `reason="served_model_mismatch"`.
A verified warm run with a network volume records `config.warm_cache` as
`{variant, verified_at, volume_id}`. Dry runs and volume-less launches never
record or claim a warm cache; a changed variant or configured volume is cold.

`serve --plan-only` stops before this lease lifecycle: it creates no pool, capability,
provider, template, workload, or lease. It reports the catalogue argv/image, volume-cache
environment, selected-GPU fit, startup timeout, TTL estimate, and whether pricing came from the
read-only live snapshot or canonical-VRAM fallback. Registry-backed `--dry-run` is unchanged.

A self-hosted `public_endpoint` warm creates no lease row, returns `lease_id: null`, emits the
compatible ready event, and is skipped by renewal, idle-stop, and activity-controller logic.

**Dataclasses:**

```python
@dataclass(frozen=True) class LaunchTemplate:
    template_id, template_name, image_ref, registry_auth_id, container_disk_gb, volume_mount_path

@dataclass(frozen=True) class LeaseLaunchPlan:
    template: LaunchTemplate; env: dict[str, str]; workload: WorkloadConfig;
    network_volume_id, data_center_id, volume_attach_timeout_s, docker_start_cmd;
    startup_timeout_s: int = 600
```

**Exceptions:** `LaunchConfigError`, `InvalidProviderConfig`, `ProviderNotPodLease`, `TemplateImageNotConfigured`.

**Internal state sequencing** (`_persist_ready_lease`): on pod readiness, sequences `CREATING -> WAITING_RUNTIME -> WAITING_PROBE -> ACTIVE` via `LeaseRepository.update_state` + `update_readiness`.

**Readiness hook:** `arm_serve_provider(pool, *, provider, lease_id, pod_id)` runs after `_persist_ready_lease` when `config.openai_proxy_port` is set; writes `active_pod_id`/`active_lease_id`, `health_status="healthy"`, and a `config_audit` row (`actor="system:lease"`, `action="lease_ready"`).

**Served-model facts:** a serve provider persists its selected `config.engine`, optional
`config.variant`, complete launch configuration, generated `docker_start_cmd`, and caller
`start_args` separately, while its capability owns `served_model_id`. Capability-only re-serve
replays those persisted launch facts without consulting a changed or removed catalogue dossier.
Read-only console lease rows join
`pitwall.leases` to `pitwall.providers` and `pitwall.capabilities` and render those three facts
beside the provider id. The console maps a missing persisted value to `—`; it does not infer a
model or mutate provider state.

**Provider readiness facts:** the console provider projection reads `id`, `name`, `health_status`,
and `config` from `pitwall.providers`. It reports the non-empty `config.active_pod_id` and marks a
provider armed only when that pod id and `config.active_lease_id` are both present. Its pricing
model is `config.cost.mode`, falling back to `tagged`. These are display-only projections of the
readiness hook and disarm hook, never a launch, teardown, or health mutation path.

**Lease ID format:** `lease_{provider_id_no_dashes}_{uuid_hex_12}`.

**TTL:** `_expiry_for_lease` reads `provider.config['lease_ttl_ms']` or `ttl_ms` (default 7200000ms = 2h).

**Key invariants:**
- Only `pod_lease` provider types accepted; others raise `ProviderNotPodLease`
- Pre-readiness callback runs in a worker thread; persists `Lease` row before the readiness wait (leak-safety); uses `asyncio.run_coroutine_threadsafe` onto the owning loop to avoid `ConnectionDoesNotExistError`
- On `ProviderAttachHangRecoveryRequested`, writes 15-minute cooldown to provider row via `ProviderRepository.patch`

---

### `src/pitwall/api/leases/teardown.py`

Terminates one lease's pod, computes final cost, transitions to terminal, publishes Redis event.

**Entry point:**

```python
async def run_teardown(
    lease_id: str, *,
    pool, redis_client: Any | None = None,
    reason: str | None = None,
    now: dt.datetime | None = None,
    terminal_state: LeaseState | str = LeaseState.STOPPED,
) -> LeaseTeardownResult
# Alias: teardown_lease = run_teardown
```

**Steps:**
1. Fetch lease; raise `LeaseNotFound` if absent
2. If already terminal (`STOPPED`/`EXPIRED`), return early (no-op)
3. Transition `ACTIVE -> STOPPING` via `_mark_stopping`; raises `LeaseStateConflict` if not ACTIVE
4. Call `terminate_pod` with the retained RunPod id or its generic-id fallback
5. Compute cost via `close_lease_cost(lease, provider, terminated_at)`
6. Call `lease_repo.close_teardown` transitioning `STOPPING -> terminal_state` with cost/time/reason
7. Disarm the provider only when its `active_lease_id` matches the closing lease
8. Publish `lease.terminated` to Redis and the signed `lease.stopped` event using the capability
   identity persisted on the lease, even if provider enrichment is unavailable. Every stop path
   publishes when `REDIS_URL` is set: REST, CLI (`pitwall leases stop`), MCP, and `serve`'s own
   teardowns. The publish is best-effort: a Redis outage is logged and the completed teardown
   still returns

**Result:**

```python
@dataclass(frozen=True) class LeaseTeardownResult:
    lease: Lease; event: dict[str, str | None] | None; published_subscribers: int = 0
```

**Cost computation** (`close_lease_cost`): computes `rate * elapsed_seconds` and quantizes to 6 decimal places. The rate comes from `settlement_rate_per_second`, the one resolver that renewal also reserves at: the Lambda Cloud or Vast adapter rate; for a RunPod lease, `provider.config['cost']['per_second_active']`, then the rate of the provider's tagged pricing (`lease_rate_per_second(parse_pricing_model(cost))`, the rate launch admission reserved), then `lease.max_usd_per_hour`; for an uncapped raw-pod lease (`runpod_direct`), RunPod's recorded `costPerHr` (`raw_pod_observed_usd_per_hour`, validated by `representable_usd_per_hour`), else the $0.50/h reservation rate. Any other lease with no rate settles at `FALLBACK_LEASE_USD_PER_HOUR` ($0.50/h), the same never-$0 rule Lambda Cloud and Vast use.

**Lambda Cloud and Vast lease billing:** these leases reach the budget like RunPod leases.
Admission (`admit_provision` through `lease_reservation`) reserves the adapter's per-second rate
for the whole lease TTL (`lease_ttl_ms`, default 2 h), not the capability's execution timeout:
Lambda's VM rate, and for Vast the higher of `price_per_hour` and `bid_price_per_hour` (the
create sends the bid). The lease row carries its admission `workload_id`. Teardown settles it at
the same adapter rate (`_adapter_rate_per_second`, from the adapter's `pricing_model`) for the time
the VM ran, so `close_teardown` writes that cost to the lease and its workload, and the rest of the
reservation is released. A pricing with no per-second rate, or a rate that can no longer be read at
teardown (the capability or the rate is gone), uses `FALLBACK_LEASE_USD_PER_HOUR` ($0.50/h, the
raw-pod default), never $0; a provider config with no rate at all is refused before any VM is
created. A Vast `price` in provider config (`create.price`, then `price`) is what the create sends
as the bid, so it replaces `bid_price_per_hour` in the rate; a request payload `price` above the
counted rate is refused before admission or any Vast call. The adapter's teardown moves the lease
to its terminal state first, so `run_teardown` closes and settles a lease in that state too (it
holds the teardown lock). RunPod's billing truth-up stays scoped to adapter `runpod`.

A Lambda Cloud or Vast lease admitted before this billing change keeps its original 60 s workload
reservation and has no `workload_id`. Its teardown records the lease's own cost
(`cost_accrued_usd`, at the adapter rate) and leaves its workload as it was, so that lease's spend
stays at the 60 s charge in month-to-date spend. Its renewals pass the budget check but reserve
nothing, because there is no workload to charge.

**Event payload** (`lease_terminated_event`):
```python
{"event": "lease.terminated", "lease_id", "provider_id", "external_resource_id", "runpod_pod_id",
 "state", "terminated_at", "terminated_reason", "cost_accrued_usd"}
```

`external_resource_id` is the provider-neutral lease identity. Current RunPod launch writes it and
`runpod_pod_id` with the same value; legacy-only rows normalize on read. Vast and Lambda adapters
write only the generic field. REST, MCP, cost reporting, reconciliation warnings, and TUI lease
rows prefer the generic identity while retaining the nullable RunPod compatibility field.
The durable lease-stop audit `new_value` also records `external_resource_id`, so MCP audit reads
retain provider-neutral resource identity independently of the compatibility column.

**Exception:** `TeardownFailed`. Also propagates `LeaseNotFound`, `LeaseStateConflict`.

**Invariant:** `terminal_state` must be `STOPPED` or `EXPIRED`; `_teardown_terminal_state` raises `ValueError` otherwise.

**Dead-lease hook:** `disarm_serve_provider` runs after `close_teardown`; it clears the facts, sets `unhealthy`, and audits `lease_closed`. Only the provider armed by the closing lease is touched.

---

### `src/pitwall/api/routes/leases.py`

FastAPI router. Mounts: `POST /v1/leases`, `GET /v1/leases/{id}`, `PATCH /v1/leases/{id}`, `POST /v1/leases/{id}/stop`, `POST /v1/leases/{id}/renew`. Delegates lifecycle operations to `run_launch` and `run_teardown`, and PATCH/renewal to the shared atomic mutation service in `pitwall.leases.mutations`. Uses DI for `LeaseRepository`, `CapabilityRepository`, `ProviderRepository`.

Operators should use the documented stop endpoint after live serve-model
verification; see [`docs/operator/serve-quickstart.md`](../operator/serve-quickstart.md).

---

## 3. Lease State Machine

**States:**

| State | Meaning |
|---|---|
| `CREATING` | Pod creation request sent; initial row persisted via pre-readiness callback |
| `WAITING_RUNTIME` | Pod is booting; RunPod reports runtime up |
| `WAITING_PROBE` | Pod running; readiness probe not yet satisfied |
| `ACTIVE` | Fully operational; endpoints and readiness confirmed |
| `STOPPING` | Teardown requested; pod termination in progress |
| `STOPPED` | Pod terminated; cost closed — terminal |
| `FAILED` | Creation or runtime failed — terminal |
| `EXPIRED` | TTL elapsed, no renewal — terminal |

**Creation flow:**
1. `run_launch` checks the durable account kill-switch state, calls `admit_lease_launch`
   (budget gate), then `prepare_lease_launch`
2. `create_pod_with_fallback` called with `pre_readiness_callback`; callback persists `Lease` row (state=`CREATING`) in a worker thread via `asyncio.run_coroutine_threadsafe`
3. On pod readiness, `_persist_ready_lease` sequences: `CREATING -> WAITING_RUNTIME -> WAITING_PROBE -> ACTIVE`
4. Readiness signals are validated via `LeaseReadiness`; incomplete signals raise `LaunchConfigError`
5. When `openai_proxy_port` is configured, `arm_serve_provider` writes the active pod/lease facts and marks the provider healthy

Launch failure accounting follows the resource boundary. A failure in
`prepare_lease_launch` before any create attempt, or an explicit no-capacity
result carrying zero pod attempts, terminalizes the admitted workload as
failed and releases its reservation with zero actual cost. If setup, a paid
pod attempt, or an ambiguous create/readiness failure occurs, actual cost
remains unknown until the lease or provider evidence supplies it; the launch
path does not infer zero from the final exception class. Teardown links the
final accrued lease cost back to its workload, including when that workload is
already terminal, without replacing an existing actual-cost provenance.
Cancellation-safe cleanup waits for owned termination and terminalization and
consumes a cancelled child cleanup task without spinning.

**Automated reconciliation:** `_expiry_for_lease` sets TTL at creation. Every tick, the reconciler
writes Redis traffic through and independently evaluates every active lease for idle and absolute
lifetime controls; the separate near-expiry set emits T-15/T-5 warnings exactly once using durable
per-expiry markers, then performs T-0 expiry. Moving `expires_at` resets those warning markers.

Activity leases renew through the shared atomic mutation at the T-15 window when they are busy.
An unavailable or malformed Redis traffic read suppresses idle decisions for that lease, while a
successful read with no key permits the persisted traffic or readiness fallback. Activity leases
without an idle timeout renew only with traffic in the preceding 15 minutes. The reconciler stops
idle leases through `run_teardown(reason="idle")` and re-checks price, budget, and the kill switch
before renewal. The reconciler is the only automatic component that renews or stops a lease; an
operator may explicitly use the authenticated REST or MCP renew/stop operation. Manual renewal
still uses the same budget, price, kill-switch, idempotency, and maximum-lifetime gates and is not
an override of those controls.

An activity renewal keeps the lease ID and original TTL, but never extends beyond
`created_at + PITWALL_LEASE_MAX_LIFETIME_MIN`. A blocked renewal remains active until T-0, then
stops with `budget`, `kill_switch`, or `max_lifetime`; ordinary expiry uses `ttl`.
T-15/T-5 warnings retain the legacy Redis payload and also publish the signed `lease.expiring`
envelope to matching webhook subscribers. Only successful 2xx proxy responses stamp lease traffic.
Operator REST and MCP stops always emit `reason=operator`; optional user text is retained as the
legacy `terminated_reason`, while controller-only paths own system reasons.

## Serve-model automation

An `activity` lease renews at the T-15 window only when proxy traffic is recent.
`idle_timeout_min` starts at `ready_at`, is at least 5 minutes, and stops an idle
lease through Pitwall's teardown path. Missing Redis/traffic data is busy, never
idle. `max_usd_per_hour` is checked at serve and every activity renewal; fallback
or stale pricing under a cap is `price_unknown`, and a higher live price is
`cap_exceeded`. Budget and kill-switch refusals serialize as `budget_exhausted`
and `kill_switch_engaged`. Activity renewal never exceeds
`created_at + PITWALL_LEASE_MAX_LIFETIME_MIN` (default 1440 minutes).

**Mutation contract:** PATCH can persist `renewal_policy` and
`auto_teardown_on_expiry`; launch-shape fields such as image, GPU, template, and
volume are immutable after creation and receive a stable 422. Renewal is
additive from the current persisted expiry, accepts 1–43,200 minutes, and cannot
move expiry beyond 30 days from database time. Both operations use a row lock,
same-transaction audit entry, and optional exactly-once idempotency key. Only
creating, waiting, and active leases are mutable; stopping or terminal leases
return a state conflict.

**Renewal reserves budget.** Every renewal (REST, MCP, `pitwall leases renew`, and the
reconciler's activity renewal; RunPod, Lambda Cloud, and Vast) reserves rate × extension:
`renew_lease` prices the extension at the rate teardown settles at
(`settlement_rate_per_second`: the adapter rate for Lambda Cloud and Vast, `per_second_active`
then tagged pricing then `max_usd_per_hour` for RunPod, RunPod's recorded `costPerHr` then the
$0.50/h reservation rate for an uncapped raw pod, else the $0.50/h fallback; never $0). `LeaseRepository.renew` takes the
budget advisory lock in the renewal transaction and runs the budget gate's check
(`BudgetGate.check_available`, the monthly budget and the per-request cap). A renewal that would
pass the budget raises `BudgetRejected` and changes nothing: the lease keeps its expiry, the audit
row and idempotency key are not written. Otherwise the linked workload's `cost_ceiling_usd` grows
by the extension in the same transaction (the audit row records `reserved_usd`), so month-to-date
spend counts the renewed time until teardown settles it at rate × actual runtime. REST and MCP
answer `budget_rejected` (with the budget snapshot and remedy over MCP), the CLI exits with
`budget_rejected`, and when the budget is unset or invalid (not a positive, finite decimal) all
three refuse with `budget_not_configured` (REST answers 503 with a remedy naming
`PITWALL_MONTHLY_BUDGET_USD` and `PITWALL_PER_REQUEST_MAX_USD`, never the value; a REST launch
refuses the same way). The reconciler's activity renewal stops renewing, logs the
refusal, and lets the lease reach its TTL, where it is torn down with reason `budget`. Before
renewing, the reconciler's pre-check
(`_renewal_refusal_reason`) prices the renewal at the same settlement rate × the original TTL, for
any pricing; a refused or unconfigured budget, or a provider with no readable `lease_ttl_ms`, counts
as a `budget` refusal. The audit actors `cli:lease` and `reconciler:activity` are admitted
by `0042`; before it, those renewals failed the `config_audit_actor_check`.

---

## 4. Public Interfaces

```python
# pitwall.leases.state
next_state = transition_lease_state(from_state: LeaseStateInput, to_state: LeaseStateInput)
ok = can_transition_lease(from_state: LeaseStateInput, to_state: LeaseStateInput)
raise IllegalLeaseTransitionError(from_state, to_state)

# pitwall.api.leases.launch
result = await run_launch(pool=..., capability=..., provider=..., dry_run=False, ...)
plan = await prepare_lease_launch(pool, capability, provider, ...)
template = await ensure_launch_template(pool, capability, provider)
workload_id = await admit_lease_launch(pool, capability, provider, ...)
cost = estimate_lease_launch_cost(capability, provider, payload=None)
armed = await arm_serve_provider(pool, provider=..., lease_id=..., pod_id=...)

# pitwall.api.leases.teardown
result = await run_teardown(lease_id, pool=..., redis_client=None, terminal_state=STOPPED, ...)
result = await teardown_lease(lease_id, pool=..., ...)
cost = close_lease_cost(lease, provider=..., terminated_at=...)
event = lease_terminated_event(lease)
subscribers = await publish_lease_terminated(redis_client, event)
disarmed = await disarm_serve_provider(pool, provider=..., lease_id=...)
```

---

## 5. Configuration

All config from `provider.config` (JSON `Mapping` on `Provider` model). Defaults applied per-key.

| Config key | Type | Default | Description |
|---|---|---|---|
| `template_name` | `str` | `pitwall-{cap}-{prov}` | RunPod template name |
| `image_ref` | `str` | env `WORKER_IMAGE` | Docker image |
| `container_disk_gb` | `int` | `50` | Container disk GB |
| `volume_mount_path` | `str` | `/workspace` | Volume mount path |
| `network_volume_id` | `str` | env `RUNPOD_NETWORK_VOLUME_ID` | R2 volume |
| `data_center_id` | `str` | env `RUNPOD_DATA_CENTER_ID` | Pod region |
| `gpu_types` / `gpu_type_priority` | `list[str]` | **(required)** | GPU model list |
| `engine` | `"vllm" \| "llama.cpp" \| "sglang"` | `"vllm"` in serve-model | OpenAI server launch shape |
| `variant` | `str` | `None` | Non-empty, trimmed catalogue variant id |
| `gpu_count` | `int >= 1` | `1` | Forwarded to `WorkloadConfig.gpu_count`; no Pitwall upper bound |
| `template_id` | `str` | `None` | Existing non-serverless pod template; `^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$` |
| `startup_timeout_s` | `int` | `600` generic / `1800` serve-model | Readiness deadline, 60–7200 seconds |
| `readiness_path` | `str` beginning with `/` (≤128 chars) | `/health` | First HTTP readiness path; validated on registration and again while preparing a launch |
| `gpu_type_priority_mode` | `str` | `custom` | `"custom"` or `"availability"` |
| `data_center_priority` | `str` | `custom` | `"custom"` or `"availability"` |
| `allowed_cuda_versions` | `list[str]` | `None` | CUDA version constraints |
| `ports` | `int\|list[int]\|dict` | `None` | HTTP/TCP port mappings |
| `openai_proxy_port` | `int` | `None` | HTTP port the OpenAI proxy fronts; arms the readiness hook |
| `active_pod_id` / `active_lease_id` | `str` | `None` | Written by the hooks, never by operators |
| `docker_start_cmd` | `list[str]` | `None` | RunPod `dockerStartCmd` container arguments |
| `lease_ttl_ms` / `ttl_ms` | `int` | `7200000` (2h) | Lease TTL ms |
| `max_cost_per_hr` | `float` | `None` | Budget ceiling |
| `max_attach_hang_s` | `float` | `None` | Volume attach timeout |
| `cost.per_second_active` | `Decimal` | `None` | Billing rate |

**Budget reservation:** `estimate_lease_launch_cost` = ceiling rate × TTL seconds. The ceiling rate is the per-second rate (the higher of rate and bid when a bid is configured) for `per_second` and `gpu_hour` pricing; any other pricing reserves its lease rate (`lease_rate_per_second`, for example a `per_vm_second` rate), else `FALLBACK_LEASE_USD_PER_HOUR` ($0.50/h, for example `zero` or `per_request`), never the capability's execution timeout. That is the rate teardown settles at, so a lease that runs its whole TTL settles at what it reserved.

**Pod env forwarded from process:** `REDIS_URL`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `R2_ENDPOINT`, `R2_BUCKET_STAGING`

The J23 hermetic release journey covers the serve lifecycle in order: a registry-backed dry-run
performs no fake pod creation, a fake pod then reaches ready/ACTIVE and arms the provider, the
OpenAI proxy forwards one chat request to its derived pod URL, and disarm removes the active pod
facts again.

---

## 6. Failure Modes & Error Types

**Transition:** `IllegalLeaseTransitionError` — `to_dict()` returns `{"error": "illegal_lease_transition", "from_state": "...", "to_state": "..."}`.

**Launch:** `ProviderNotPodLease` (non `pod_lease` provider); `TemplateImageNotConfigured` (no image ref); `InvalidProviderConfig` (malformed config); `LaunchConfigError` (base). `ProviderAttachHangRecoveryRequested` — provider enters 15-min cooldown, response has `provider_fallback=True, provider_cooldown_until`. `ProviderFallbackRequested` — fallback exhausted, `provider_fallback=True`.

**Teardown:** `LeaseNotFound` (404); `LeaseStateConflict` (409 if not ACTIVE on stop); `TeardownFailed` (502 `teardown_failed` when the provider call fails; the lease stays `stopping` and the reconciler retries it).

**API routes:** `ChangeSetTooBroad` (PATCH spanning multiple axes); `LeaseNotFound` (404); `LeaseStateConflict` (409).

**Edge cases:**
- If `pool` lacks `acquire`, `_persist_ready_lease` and `_set_provider_attach_hang_cooldown` silently no-op
- If `redis_client` is `None`, `publish_lease_terminated` logs warning and returns 0 (teardown still succeeds)
- If a lease has no billing rate (a RunPod provider with no `per_second_active`, no tagged pricing and no cap, or a Lambda Cloud or Vast rate that cannot be read), `close_lease_cost` settles it at the $0.50/h fallback, never $0

---

## 7. Testing

**`tests/unit/leases/test_state_transition_matrix.py`**

Parametrized `test_full_lease_state_transition_matrix`: all `LeaseState` pairs tested. Allowed pairs assert `can_transition_lease` is `True` and `transition_lease_state` returns target. Disallowed pairs assert `can_transition_lease` is `False` and `transition_lease_state` raises `IllegalLeaseTransitionError` with correct `from_state`/`to_state`. `test_expected_matrix_covers_every_lease_state` validates full enum coverage.

**`tests/api/test_leases_contract.py`**

Hermetic route tests for `/v1/leases` POST/GET/PATCH using override dependencies. Tests 404 on missing capability, response shape regression (ensures create returns `LeaseResponse` not raw `run_launch` dict), and contract compliance.

**`tests/release/test_dry_run_tier.py`**

`test_dry_run_leases_returns_template_without_creating_pod`: `dry_run=True` returns template info without calling RunPod. `test_dry_run_lease_no_paid_call_to_runpod_api`: verifies no paid RunPod calls in dry-run mode.

**`tests/test_audit_checks.py`**

`_pod_lease_provider_fixture()` provides a `pod_lease` provider fixture. Key tests: `test_fail_pod_lease_fixture_without_probe_signal` (readiness validation), `test_fail_pod_lease_fixture_cost_after_readiness` (cost after readiness), `test_fail_pod_lease_attach_timeout_over_five_minutes` (attach hang), `test_fail_pod_lease_long_lived_r2_strategy`, `test_fail_pod_lease_static_r2_env_injection`, `test_fail_missing_single_lease_stop_route`.

---

## 8. Dependencies

**Internal imports:**

| Module | What is used |
|---|---|
| `pitwall.core.enums` | `LeaseState`, `LeaseRenewalPolicy`, `ProviderType` |
| `pitwall.core.models` | `Lease`, `LeaseEndpoints`, `LeaseReadiness`, `Capability`, `Provider` |
| `pitwall.db.repository` | `LeaseRepository`, `ProviderRepository` |
| `pitwall.api.exceptions` | `LeaseNotFound`, `LeaseStateConflict` |
| `pitwall.runpod_client.pods` | `create_pod_with_fallback`, `terminate_pod`, `ProviderAttachHangRecoveryRequested`, `ProviderFallbackRequested` |
| `pitwall.runpod_client.templates` | `ensure_template`, `get_image_ref_from_env`, `get_registry_auth_id_from_env` |
| `pitwall.runpod_client.workloads` | `WorkloadConfig` |
| `pitwall.cost.budget_gate` | `BudgetGate` |
| `pitwall.cost.sync_gate` | `estimate_cost` |
| `pitwall.r2_temp_credentials` | `vend_r2_temp_credential_pod_env` |
| `pitwall.reconciler` | `_fire_lease_expiry_actions` |

**External libraries:** `asyncpg` (Pool), `redis`/`redis.asyncio` (pub/sub), `pydantic` (all `PitwallModel` subclasses), `fastapi` (router).
