# REST API Surface — SDLC Subsystem Documentation

> All claims grounded in `src/pitwall/api/` source, with `path:line` citations.

## 1. Purpose & Scope

The Pitwall REST API (`src/pitwall/api/`) is the sole HTTP interface to the GPU broker.
FastAPI app at `/v1` for core routes, `/v1/admin` for administrative writes.

**What it does:** capability and provider registry CRUD, lease lifecycle (launch/renew/teardown),
sync inference dispatch with idempotency and budget gating, OpenAI-compatible passthrough proxy
with fallback chain, job status/result reads, webhook subscription registration,
admin pre-spend audit, and emergency kill-switch.

**Auth:** Non-health paths use opaque bearer authorization when
`PITWALL_API_TOKEN` or `PITWALL_API_SCOPED_TOKENS` is configured. Required
scopes are `read`, `spend`, `lease:mutate`, `webhook:admin`, or
`server:admin`. `/v1/admin/*` routes additionally require
`X-Pitwall-Secret`; without `PITWALL_ADMIN_SECRET` they fail closed with 401.
Non-loopback API startup requires both the all-scopes API token and admin secret.

**Position:** Top-most layer. Depends on `pitwall.db`, `pitwall.core`, `pitwall.cost`,
`pitwall.resolver`, `pitwall.routing`, `pitwall.runpod_client`, `pitwall.observability`.

The hermetic J23 release journey exercises the OpenAI chat proxy in-process after a mocked
serve-model pod has armed its provider; it asserts that the original chat JSON body reaches the
derived pod URL and that teardown subsequently removes that route.

---

## 2. Components

### `app.py` — Bootstrap and middleware (`src/pitwall/api/app.py`)

Fail-closed: `require_runtime_env("api")` runs at import; the required
`DATABASE_URL` and `REDIS_URL` are read immediately after that (`pitwall.api.app`). The RunPod
credential is not needed to boot: `RUNPOD_API_KEY` is resolved when the first RunPod operation
runs, and its absence raises `RunPodError` naming the variable.

```
AdminSecretMiddleware(app, secret: str | None)
    async __call__(scope, receive, send)
        Gates /v1/admin and /v1/admin/*, except GET/HEAD /v1/admin/runpod/*, which
        need only the `read` bearer scope (pitwall.api.app).
        If secret is unset, returns 401
        {"detail": "admin routes disabled: PITWALL_ADMIN_SECRET is not configured"}
        (pitwall.api.app).
        If secret is set, compares X-Pitwall-Secret with hmac.compare_digest;
        mismatch returns 401 {"detail": "invalid or missing X-Pitwall-Secret"}
        (pitwall.api.app).

BearerTokenAuthorizer(master_token: str | None, scoped_tokens_json: str | None)
    Constant-time token lookup. PITWALL_API_TOKEN grants every scope;
    PITWALL_API_SCOPED_TOKENS entries grant exactly the listed scopes
    (pitwall.api.scopes:parse_scoped_tokens). Disabled when neither is set.

ApiTokenMiddleware(app, authorizer: BearerTokenAuthorizer)
    If the authorizer is disabled, passes through and records every scope as
    granted. If enabled, checks Authorization: Bearer <token> on every
    non-public-health path: a missing or unknown token returns 401
    {"detail": "invalid or missing bearer token"} plus WWW-Authenticate: Bearer;
    a token that lacks the route's scope returns 403
    {"detail": "bearer token lacks required scope", "required_scope": "<scope>"}
    (pitwall.api.app:_required_scope).

InboundRateLimitMiddleware(app, config: InboundRateLimitConfig | None, authorizer)
    If config is unset, passes through. Public health/probe paths pass through.
    One token bucket per client IP and bearer-token digest, bounded to 10,000
    buckets. When the limit is exceeded, returns 429 {"detail": "rate limit exceeded"}
    with Retry-After; if the limiter itself fails it fails closed with 503
    {"error": "rate_limiter_unavailable"} and Retry-After: 1 (pitwall.api.app).

RequestBodyLimitMiddleware(app, max_body_bytes: int)
    Reads and bounds every HTTP request body before a route runs. A bad
    Content-Length returns 400, and a declared or streamed body over the cap
    returns 413, both as {"error": "request_rejected", "detail": "invalid content
    length" | "request body too large"} (pitwall.api.app).

app = FastAPI(title="Pitwall API", version="1", lifespan=api_lifespan)
    Middleware is always installed:
    RequestBodyLimitMiddleware, AdminSecretMiddleware, ApiTokenMiddleware,
    InboundRateLimitMiddleware (pitwall.api.app).
    Includes 24 routers (pitwall.api.app).
    Exception handlers: PitwallApiError (install_api_error_handler),
    VolumeFileError (install_volume_file_error_handler), BudgetRejected, and
    RequestValidationError (pitwall.api.app).

v1_health(request) -> dict[str, Any]  (pitwall.api.app)
    Checks postgres (SELECT 1 via pool) and redis (ping) ->
    {"ok": bool, "postgres": {...}, "redis": {...}}.

readyz(request) -> JSONResponse  (pitwall.api.app)
    The same two checks, but answers 503 when either dependency is down.
```

Admin enabled/disabled log lines report configuration state only; the admin
middleware is installed either way (`pitwall.api.app`).

Public health/probe path exemptions are `/health`, `/healthz`, `/readyz`, and
`/v1/health` (`pitwall.api.app`). `/healthz` and `/health` return `{"ok": true}`
without touching a dependency (`pitwall.api.app`).

A request body that fails validation returns 422
`{"error": "invalid_request", "detail": [{"type": "<pydantic error type>"}, ...]}`; the
handler never reflects request-controlled values (`pitwall.api.app`). The volume-file routes answer
422 `{"error": "invalid_volume_file_request"}` instead, and the onboarding routes answer
`invalid_request` with a `detail` string when the body's `action` does not match the route.

Request-time middleware order (outermost to innermost, then routes):

1. `InboundRateLimitMiddleware` (`pitwall.api.app`)
2. `ApiTokenMiddleware` (`pitwall.api.app`)
3. `AdminSecretMiddleware` (`pitwall.api.app:AdminSecretMiddleware`)
4. `RequestBodyLimitMiddleware` (`pitwall.api.app`)
5. Route handlers (`pitwall.api.app`)

No request body is buffered until the caller has authenticated.

Inbound rate limiting is a REST-edge guard configured by
`PITWALL_INBOUND_RATE_LIMIT` (default `120/60s`; `off`, `disabled`, or `none` disables it); the middleware returns caller-visible 429 +
`Retry-After` when active and exhausted (`pitwall.api.app`). Token-bucket details stay in
`11-rate-limiting.md`.

---

### `exceptions.py` — Mapped exception hierarchy (`pitwall.api.exceptions`)

39 concrete exception classes, all inherit `PitwallApiError(status_code=500, error_code="internal_error")`.
`to_response_body()` → `{"error": "<code>", ...extra}`. `ApiErrorResponse(status_code, body)` carries an already-shaped, redacted service body. Every `Serve*` class below derives from `_ServeError`, whose body is `{"error": "<code>", "detail": "<text>", ...fields}`; `no_serve_history`, `cap_exceeded`, `price_unknown`, `budget_exhausted`, `kill_switch_engaged`, and `warm_failed` override it and carry no `detail`.

| Exception | status_code | error_code | Extra fields |
|---|---|---|---|
| `CapabilityNotFound` | 404 | `capability_not_found` | `name` |
| `CapabilityDisabled` | 409 | `capability_disabled` | `name` |
| `CapabilityConflict` | 409 | `capability_conflict` | `name` |
| `ProviderNotFound` | 404 | `provider_not_found` | `id` |
| `ProviderUnavailable` | 503 | `no_providers_available` | `capability`, `chain`, `escape_hatch` (only when present) |
| `ProviderConflict` | 409 | `provider_conflict` | `name` |
| `ProviderCapabilityMissing` | 422 | `provider_capability_missing` | `capability_id`, `message` |
| `RateLimited` | 503 | `rate_limited` | `retry_after_s` |
| `LeaseNotFound` | 404 | `lease_not_found` | `id` |
| `LeaseStateConflict` | 409 | `lease_state_conflict` | `id`, `state`, `operation` |
| `ChangeSetTooBroad` | 400 | `change_set_too_broad` | `conflicting_fields` |
| `UnsupportedLeasePatch` | 422 | `unsupported_lease_patch` | `fields` |
| `EmptyLeasePatch` | 422 | `empty_lease_patch` | — |
| `LeaseExpiryLimitExceeded` | 409 | `lease_expiry_limit_exceeded` | `id`, `max_horizon_minutes` |
| `IdempotencyConflict` | 422 | `idempotency_conflict` | `idempotency_key` |
| `IdempotencyMismatch` | 422 | `idempotency_mismatch` | `original_workload_id` |
| `PreSpendPayloadRejected` | 422 | `pre_spend_payload_rejected` | `decision`, `findings` |
| `InvalidProxyPath` | 400 | `invalid_proxy_path` | `detail` |
| `WebhookSubscriptionNotFound` | 404 | `webhook_subscription_not_found` | `id` |
| `WebhookTargetNotAllowed` | 422 | `webhook_target_not_allowed` | `detail` |
| `WorkloadNotFound` | 404 | `workload_not_found` | `id` |
| `JobNotReady` | 409 | `job_not_ready` | `id`, `state` |
| `JobNotCancellable` | 409 | `job_not_cancellable` | `id` |
| `JobCancelFailed` | 502 | `job_cancel_failed` | `id` |
| `ServeConflict` | 409 | `serve_conflict` | `capability`, `active_model` |
| `ServeNoServeHistory` | 422 | `no_serve_history` | — |
| `ServeCapExceeded` | 422 | `cap_exceeded` | `gpu_class`, `price_usd_per_hour`, `max_usd_per_hour` |
| `ServePriceUnknown` | 422 | `price_unknown` | `gpu_class`, `max_usd_per_hour` |
| `ServeBudgetExhausted` | 422 | `budget_exhausted` | `reason`, `snapshot` |
| `ServeKillSwitchEngaged` | 422 | `kill_switch_engaged` | — |
| `ServeInvalidGpuClass` | 422 | `invalid_gpu_class` | `gpu_class`, `suggestions` |
| `ServeRateRequired` | 422 | `rate_required` | `detail` |
| `ServeUnknownVariant` | 422 | `unknown_variant` | `model`, `variant` |
| `ServeTemplateInvalid` | 422 | `invalid_template` | `detail` |
| `ServeTtlBelowStartup` | 422 | `ttl_below_startup` | `detail` |
| `ServeStalePrice` | 422 | `stale_price` | `detail`, `age_seconds`, `source`, `max_age_s` |
| `ServeVerificationFailed` | 502 | `served_model_mismatch` | `expected`, `observed` |
| `ServeLaunchFailed` | 503 | `launch_failed` | `detail` |
| `ServeWarmFailed` | 503 | `warm_failed` | `provider_id`, `model_id`, `state` |

---

### `capability_routes.py` (`src/pitwall/api/capability_routes.py`)

Admin: `POST /v1/admin/capabilities` (create, 201), `PATCH /v1/admin/capabilities/{id}`
(allows description/input_schema/output_schema/defaults/hints/served_model_id; blocks
name/version/class_/cost_mode),
`POST /v1/admin/capabilities/{id}/enable`, `POST /v1/admin/capabilities/{id}/disable`.

Capability detail and list responses add `served_model_id`, `active_lease`,
`idle_timeout_min`, `last_traffic_at`, `renewal_policy`, and
`max_usd_per_hour`. The four automation fields are copied from the newest
active lease and are `null` when there is no active lease. `active_lease`
remains `{lease_id, state, expires_at}` and is not widened.

`GET /v1/capabilities` accepts optional `class`, `cost_mode`, `source`, and
`enabled` filters. Class, cost mode, and source use their declared enum values;
unknown values return 422 before the repository runs. `enabled=true` selects
only enabled rows, `enabled=false` selects only disabled rows, and omission
includes both states. Filters are applied in SQL before `LIMIT`/`OFFSET`
(`pitwall.db.repository`). `GET /v1/providers` has the same enabled-state
semantics alongside its capability and provider-type filters
(`pitwall.db.repository`). Existing internal `enabled_only` callers
retain their behavior.

`_lease_repo()` builds the `LeaseRepository` dependency from the request pool
(`pitwall.api.capability_routes`). `active_lease_summary()` turns the newest active
lease into `{lease_id, state, expires_at}` (or `null`) (`pitwall.api.capability_routes`);
the list and detail handlers query it per capability (`pitwall.api.capability_routes`).

All mutating handlers call `insert_audit(pool, actor="rest:admin", ...)`. `PATCH /v1/admin/providers/{id}` writes the changed fields and the `enabled` toggle in one transaction, and a request that changes `enabled` audits as `enable` or `disable` (plus an `update` row when other fields changed too).

#### Consumer contract (subagent-model-routing)

`GET /v1/capabilities/{name}` returns an object from which routing reads `name`,
`served_model_id` (a string or `null`), and `active_lease` (either `null` or
`{lease_id, state, expires_at}`). An unknown name is a JSON `404`, never an
empty successful response. `expires_at` is an ISO 8601 timestamp with `Z` or a
UTC offset, suitable for `datetime.fromisoformat` after routing strips fractional
seconds. The routing flow uses the same bearer token for this read and for
`/v1/openai/{capability}/v1/*`; configure that token in
`PITWALL_API_SCOPED_TOKENS` with both `read` and `spend` scopes.
SSE responses are relayed unbuffered chunk-by-chunk; a mid-stream upstream failure ends the stream with `data: {"error":"upstream stream failure"}`.

---

### `provider_routes.py` (`src/pitwall/api/provider_routes.py`)

Admin: `POST /v1/admin/providers` (201), `PATCH /v1/admin/providers/{id}`,
`POST /v1/admin/providers/{id}/enable`, `POST /v1/admin/providers/{id}/disable`,
`POST /v1/admin/providers/{id}/hibernate` (sets health_status="hibernated").

Public: `GET /v1/providers` (list), `GET /v1/providers/{id}`, `GET /v1/providers/{id}/health`
(health_status, cooldown_until, consecutive_failures, cooldown_trips, recent_error_rate).

`validate_provider_registration_config()` called on every create and every patch that
includes provider_type/endpoint_id/cloud_type/config — raises `HTTPException(422)` on
ValueError (`pitwall.api.provider_routes`).

Capability list and detail responses expose nullable self-hosted readiness, residency,
cold-start, slot, context, tool-calling, and idle-unload metadata; non-self-hosted rows remain
wire-compatible with null additive fields.

---

### `routes/leases.py` (`src/pitwall/api/routes/leases.py`)

`POST /v1/leases` — calls `create_routed_lease()` (from `leases/launch.py`), the path MCP
`pitwall_lease_pod` shares: resolves the capability by name then registry id (404
`capability_not_found`), plans a compute route through the production planner (a preview for
dry_run; 503 `no_providers_available` when nothing is eligible), resolves the selected provider
(404 `provider_not_found`), calls `run_launch()`, and records the plan on the lease workload;
dry_run returns the plan without persisting.

`GET /v1/leases/{id}`, `PATCH /v1/leases/{id}` (validates multi-axis via
`lease_patch_conflicting_fields()` → `ChangeSetTooBroad` 400; supports only
`renewal_policy`, `auto_teardown_on_expiry`, and an optional idempotency key),
`POST /v1/leases/{id}/renew` (adds 1–43,200 minutes to the currently persisted
expiry, capped at 30 days from database time),
`POST /v1/leases/{id}/stop` (calls `run_teardown()`), `DELETE /v1/leases/{id}` (204, idempotent).

PATCH and renewal delegate to the shared `pitwall.leases.mutations` service.
They lock the lease row, write the mutation and audit record in one transaction,
and deduplicate retries when `idempotency_key` is supplied. Reusing a key with a
different operation or payload returns `idempotency_conflict` (422). Unsupported
PATCH fields return `unsupported_lease_patch` (422) and are never ignored.

---

### `routes/models.py` (`src/pitwall/api/routes/models.py`)

Read-scoped catalogue routes are `GET /v1/models/catalogue`,
`GET /v1/models/catalogue/{model}`, and
`GET /v1/models/catalogue/{model}/fit?variant=&ttl_minutes=120&cloud=secure`.
The `{model}` path encoding replaces the first `/` in a model id with `--`
(for example, `org--model` means `org/model`). `/v1/models` itself is unchanged:
it remains owned by the OpenAI proxy passthrough.

The list response is `{"models": [{model_id, vendor, family, openai_chat,
variant_ids, default_variant}]}`. Detail is the dossier JSON plus `body`, including
strict companion and evidence fields. Fit
returns `{model_id, variant, confidence, price_source, price_checked_at, price_age_seconds,
price_stale, options}`;
each option includes fit, VRAM/headroom, GPU count, selected-cloud price per hour,
and TTL cost. Decimal prices are strings and timestamps are ISO 8601 strings.
The `fit` enum is `fits` (zero or at least 10% positive headroom on one GPU),
`tight` (positive headroom below 10% on one GPU), `tp` (tensor parallel), or
`no` (not available within the cloud GPU-count limit or unknown VRAM requirement).
`ttl_minutes` must be positive and `cloud` is `secure` or `community`, so invalid
values receive FastAPI's 422. An unknown model is 404 with
`{"detail": "unknown model: ..."}`; an unknown variant is 422. A fallback
price snapshot still returns 200 with `price_source="fallback"` and null prices.
`price_stale` is false while `PITWALL_PRICE_MAX_AGE_S` is unset; when configured, it is
true for fallback data or a live snapshot older than that many seconds.

---

### `routes/inference.py` (`src/pitwall/api/routes/inference.py`)

`POST /v1/inference` — idempotency check via `Idempotency-Key` header or
`body.idempotency_key`; canonical JSON comparison of non-control fields.
Control fields: `{"capability_id","capability","capability_name","provider_id","dry_run","idempotency_key"}`. Every other top-level field is provider payload, except a key that normalizes (lowercase, alphanumerics only) to a control field without being one of these spellings — `dryRun`, `dry-run`, `idempotencyKey` — which is refused with 422 `invalid_request` before any routing, so a misspelled `dry_run` never runs live.

Flow: the route delegates to `ProductionRoutingService` (`pitwall.routing.production`), which owns
resolution, planning, budget admission, execution, and the workload record; the handler maps
`CapabilityNotFoundError`, `CapabilityDisabledError`, `ProviderNotFoundError`, and
`NoHealthyProviderError` to `CapabilityNotFound`, `CapabilityDisabled`, `ProviderNotFound`, and
`ProviderUnavailable` (carrying the `escape_hatch` proposal when every free pool is exhausted),
a blocked pre-spend scan to `PreSpendPayloadRejected`, an idempotency-key reuse with a different
body to `IdempotencyMismatch`, and any other planning failure to `ProviderUnavailable`. Concurrent
identical requests share one execution (`AsyncRequestCoalescer`). `dry_run` returns
`{"workload_id": "dry_run_inference_<16 hex>", "result": {"dry_run": true, "plan": {...}}}`
without executing; a live call returns `{"workload_id", "result"}`. Response headers:
`X-Pitwall-Workload-ID`, `X-Pitwall-Capability`, `X-Pitwall-Provider-ID`,
`X-Pitwall-Route-Plan-ID`, and `X-Pitwall-Trace` when the workload has a Langfuse trace.

---

### `routes/openai.py` (`src/pitwall/api/routes/openai.py`)

All HTTP methods at `/v1/openai/{capability}/v1/{path:path}`; the path must be a normalized relative
path or the route answers 400 `invalid_proxy_path`. The route plans through `ProductionRoutingService`
(`preview_prepared`, with the pass-through lease proxy and at most `MAX_OPENAI_ATTEMPTS` attempts),
admits and records a pass-through workload, rewrites headers (`x-pitwall-capability`,
`x-pitwall-trace` set to the route plan id), and calls `execute_openai_with_fallback()`.
On failure the workload is failed and the caller gets `ProviderUnavailable` (503). On success the
workload completes and the inference trace is emitted. Returns a `StreamingResponse` with the
upstream bytes; response headers are `X-Pitwall-Workload-ID`, `X-Pitwall-Capability`,
`X-Pitwall-Provider-ID`, `X-Pitwall-Route-Plan-ID`, and `X-Pitwall-Trace` when a trace exists.
Request-path health telemetry treats 401/403 and recognized tool-launch 400s as misconfiguration, 429 as capacity, and other consumer 4xx responses as saturation observations without changing provider health.
Supports `x-pitwall-drill: skip-primary` to skip first provider.
Fallback budget: `DEFAULT_OPENAI_FALLBACK_BUDGET_S` (5 seconds, `pitwall.routing.fallback`).

---

### `routes/jobs.py` (`src/pitwall/api/routes/jobs.py`)

`GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/status`, `GET /v1/jobs/{id}/result`
(409 `job_not_ready` if non-terminal; terminal reads preserve the established id, route-plan,
selected-provider, and result fields while bounding the result),
`POST /v1/jobs/{id}/cancel` (idempotent on terminal states; calls
`QueueClient.cancel()` for RUNNING, may raise `RateLimited(503)`).

---

### `routes/webhook_subscriptions.py` (`src/pitwall/api/routes/webhook_subscriptions.py`)

`POST /v1/webhook-subscriptions` (201), `GET /v1/webhook-subscriptions`
(query: consumer, active_only), `POST /v1/webhook-subscriptions/{id}/rotate-secret`,
`POST /v1/webhook-subscriptions/{id}/activate`, `POST /v1/webhook-subscriptions/{id}/deactivate`,
and `DELETE /v1/webhook-subscriptions/{id}` (204). All require the `webhook:admin` scope and write
an audit record with actor `rest:webhook`. Create and rotate-secret generate a 32-byte URL-safe
`signing_secret` and return it once; list and read responses carry only `id`, `consumer`,
`webhook_url` (redacted), `active`, `event_types`, `created_at`, and `updated_at`. A target URL is
resolved through the webhook egress policy on create and on rotate-secret; a rejected target is 422
`webhook_target_not_allowed`, and plain-HTTP loopback targets must appear in
`PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST` ([Webhooks](../webhooks.md)). An unknown id is 404
`webhook_subscription_not_found`. Without `PITWALL_WEBHOOK_ENCRYPTION_KEYS` and
`PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY` every subscription route answers 424
`webhook_encryption_not_configured` (raised as `ApiErrorResponse`), because secrets are
encrypted at rest ([Inbound & Outbound Webhooks](09-webhooks.md)).

---

### `admin/audit_capability.py` (`src/pitwall/api/admin/audit_capability.py`)

`POST /v1/admin/audit-capability/{name}` — delegates to `pitwall.audit.capability.audit_capability()`.
Accepts optional query params as payload dict. Returns `CapabilityAuditResult.model_dump()`.

---

### `admin/emergency.py` + `admin/kill_switch.py`

`POST /v1/admin/kill-switch` (`pitwall.api.admin.emergency`). `KillSwitchRequest`:
`reason: str (required)`, `terminate_compute: bool = True`.

`run_kill(reason, actor, *, terminate_compute)` — builds `TailscaleNetworkSever`
from complete `TAILSCALE_OAUTH_CLIENT_ID/SECRET/TAILNET` config or falls back to
`NoOpNetworkSever`, calls `CloudKillSwitch.activate()`, persists `KillReport` to
`pitwall.kill_log` via `persist_kill_report()`.

`CloudKillSwitch.activate(reason)` (`pitwall.api.admin.kill_switch`): three-step (ACL deny,
device revoke, pod terminate) + best-effort R2 staging cleanup. Never raises;
returns `KillReport` with `errors` list on partial failure.

---

### `leases/launch.py` (`src/pitwall/api/leases/launch.py`)

```
ensure_launch_template(pool, capability, provider) -> LaunchTemplate
    Validates POD_LEASE; creates/resolves RunPod template.

prepare_lease_launch(pool, capability, provider, *, request_id, extra_env)
    -> LeaseLaunchPlan

run_launch(pool, capability, provider, *, request_id=None, extra_env=None,
           payload=None, budget_gate=None, idempotency_key=None, dry_run=False)
    -> dict[str, Any]
    admit_lease_launch() → prepare_lease_launch() →
    create_pod_with_fallback() → _persist_ready_lease()
    (CREATING→WAITING_RUNTIME→WAITING_PROBE→ACTIVE).
    Returns {lease_id, pod_id, workload_id, template_id, etc.}.

estimate_lease_launch_cost(capability, provider, payload=None) -> Decimal
```

Error classes: `LaunchConfigError`, `InvalidProviderConfig`, `ProviderNotPodLease`,
`TemplateImageNotConfigured`.

**Invariant:** `_env_for_pod()` blocks override of `PITWALL_*` identity keys and
`AWS_*`/`R2_*` storage credential keys by `extra_env` or `provider.config.env_vars`.

---

### `leases/teardown.py` (`src/pitwall/api/leases/teardown.py`)

```
run_teardown(lease_id, *, pool, redis_client=None, reason=None,
             now=None, terminal_state=LeaseState.STOPPED) -> LeaseTeardownResult
    Fetch lease → no-op if TERMINAL_LEASE_STATES. Mark ACTIVE→STOPPING.
    terminate_pod() → close_lease_cost() → repo.close_teardown() →
    publish to Redis LEASE_TERMINATED_CHANNEL. A provider teardown failure raises
    TeardownFailed (502 teardown_failed, no provider detail) and leaves the lease
    STOPPING; the lease reconciler retries it every tick.

LeaseTeardownResult = dataclass(lease, event, published_subscribers)
LEASE_TERMINATED_CHANNEL = "pitwall:lease:terminated"
```

`close_lease_cost()`: rate from `settlement_rate_per_second` (see 06-leases, "Cost
computation"; never $0), elapsed = `terminated_at - created_at`.

---

### `routes/serve.py` (`src/pitwall/api/routes/serve.py`)

`POST /v1/serve` accepts `ServeCreate` and returns `ServeResponse`; it requires the
`spend` bearer scope (`pitwall.api.app`). The route delegates to the
shared service (`pitwall.api.routes.serve`), which gets or creates the
capability/provider, replays a matching active lease, and rejects a live lease for a
different model (`pitwall.serve`).

A capability-only body restores the last persisted serve launch, including its image,
GPU selection, engine, environment, volume, disk size, and Docker command. It does not
re-resolve a complete history against the current catalogue; explicit request fields
still override history. Serve-only budget and kill-switch refusals are top-level 422
`budget_exhausted` and `kill_switch_engaged` bodies. Other budget-gated API surfaces keep
the legacy 402 `budget_rejected` contract.

`dry_run: true` returns the launch plan and cost estimate without a lease
(`pitwall.serve`). Catalogue resolution selects the image and launch
configuration; an operator-supplied image is required without a dossier
(`pitwall.serve`). After a live launch, the service polls the pod's
`/v1/models` inventory within its bounded verification window and tears down a
mismatched lease (`pitwall.serve, `pitwall.serve).
Successful and replayed responses expose the OpenAI proxy base URL as
`${PITWALL_API_URL}/v1/openai/<capability>/v1`

The operator quickstart for the live `/models` and chat checks, including the
fixed proxy path, is [`docs/operator/serve-quickstart.md`](../operator/serve-quickstart.md).
(`pitwall.serve, `pitwall.serve).
`ServeCreate.engine` accepts `vllm | llama.cpp | sglang` with wire default `vllm`
(`pitwall.api.schemas.serve`). SGLang launch plans persist
`/health_generate`; vLLM and llama.cpp persist `/health`
(`pitwall.serve, `pitwall.serve).
Non-chat and unsupported-companion catalogue selections use the existing 422
`unknown_variant` response (`pitwall.serve`). The manually serialized
REST fit response does not gain a field from the `FitOption.container_disk_gb`
projection.
It does add the advisory `warm_cache` boolean, derived from the matching serve
provider's verified variant-and-volume cache record.

---

### `schemas/` — Pydantic request/response models

| File | Key types |
|---|---|
| `capability_schemas.py` | `CapabilityCreate`, `CapabilityPatch`, `CapabilityListFilter`, `CapabilityResponse` |
| `provider_schemas.py` | `ProviderCreate`, `ProviderPatch`, `ProviderListFilter`, `ProviderResponse`, `ProviderHealthResponse`, `validate_provider_registration_config()`, `EndpointRegistrationConfig`, `EndpointRegistrationRequest` |
| `leases.py` | `LeaseCreate`, `LeasePatch`, `LeaseResponse`, `LeaseRenew`, `LeaseStop`, `lease_patch_conflicting_fields()` |
| `inference.py` | `InferenceRequest` (extra="allow", AliasChoices on capability_id), `InferenceResponse` |
| `jobs.py` | `JobSubmitRequest`, `JobResponse` |
| `serve.py` | `ServeCreate`, `ServeResponse`; request defaults: `gpu_count=1`, `engine="vllm"`, `ttl_minutes=120`, `gated=false`, `dry_run=false`; engine values are `vllm`, `llama.cpp`, or `sglang`; response echoes `engine`, `variant`, `gpu_count` (`pitwall.api.schemas.serve`) |

`LeasePatch` + `lease_patch_conflicting_fields()` detects multi-axis PATCH
(image+GPU+volume axes).

---

## 3. Route Inventory

The table is the app's OpenAPI export (`tools/ci/export_openapi.py`); each operation carries
`x-required-scope` and the committed baseline is `docs/api/openapi-baseline.json`
(`make openapi-check`).

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/healthz`, `/health` | — | `{"ok": true}` |
| GET | `/v1/health` | — | postgres + redis check |
| GET | `/readyz` | — | postgres + redis check; 503 when either is down |
| POST | `/v1/admin/capabilities` | Secret | 201, duplicate→409 |
| PATCH | `/v1/admin/capabilities/{id}` | Secret | accepts `served_model_id`; blocks name/version/class_/cost_mode |
| POST | `/v1/admin/capabilities/{id}/enable` | Secret | |
| POST | `/v1/admin/capabilities/{id}/disable` | Secret | |
| GET | `/v1/capabilities` | read | list; adds lease automation metadata; query: `class`, `cost_mode`, `source`, `enabled` |
| GET | `/v1/capabilities/{name}` | read | detail; same metadata as list items |
| POST | `/v1/admin/providers` | Secret | 201; validates config; unknown capability→422 `provider_capability_missing` |
| PATCH | `/v1/admin/providers/{id}` | Secret | validates config on patched fields |
| POST | `/v1/admin/providers/{id}/enable` | Secret | |
| POST | `/v1/admin/providers/{id}/disable` | Secret | |
| POST | `/v1/admin/providers/{id}/hibernate` | Secret | |
| GET | `/v1/providers` | read | list; query: `capability_id`, `enabled`, `provider_type` |
| GET | `/v1/providers/{id}` | read | |
| GET | `/v1/providers/{id}/health` | read | |
| POST | `/v1/leases` | spend | 201; dry_run returns plan |
| GET | `/v1/leases/{id}` | read | |
| PATCH | `/v1/leases/{id}` | lease:mutate | Atomic policy/auto-teardown update; unsupported fields → 422 |
| POST | `/v1/leases/{id}/renew` | lease:mutate | Atomic additive renewal; optional idempotency key |
| POST | `/v1/leases/{id}/stop` | lease:mutate | calls run_teardown() |
| DELETE | `/v1/leases/{id}` | lease:mutate | 204, idempotent |
| POST | `/v1/serve` | spend | 200; dry-run or live lease; 409/422/502/503 |
| GET | `/v1/models/catalogue` | read | Summary list: model id, metadata, variants, and default |
| GET | `/v1/models/catalogue/{model}` | read | Dossier detail; model id uses first-`--` path encoding |
| GET | `/v1/models/catalogue/{model}/fit` | read | `variant`, `ttl_minutes=120`, `cloud=secure\|community`; fallback prices are null |
| POST | `/v1/inference` | spend | idempotency via header or body |
| POST | `/v1/messages` | spend | Claude-native Messages; `gw/…` model ids pin the proxy provider; `tools`, `tool_use`, and `tool_result` round-trip (streaming emits `input_json_delta`) and any other content block type is refused with `invalid_request_error`; an upstream stream error ends the stream with an `error` event and no `message_stop`; streaming by capability name rewrites `model` per provider; a streaming request is pre-spend inspected, budget-admitted, and recorded as a workload (`X-Pitwall-Workload-ID`) before any provider call, and only providers with `supports_streaming` are planned; always answers in the Anthropic error envelope |
| GET/POST/PUT/DELETE/PATCH | `/v1/openai/{capability}/v1/{path:path}` | spend | passthrough |
| GET | `/v1/quotas` | read | Free-pool rows with Stage-2 headroom and lockout reason |
| GET | `/v1/gateway/models` | read | Proxy model-id map with `trains_on_prompts` and `tos` flags from catalog evidence |
| POST | `/v1/admin/quotas/refresh` | Secret | Runs one quota poll; returns the sampled-row count |
| POST | `/v1/routing/preview` | read | Non-persisting production plan preview (section 9) |
| POST | `/v1/jobs` | spend | Guarded, budgeted asynchronous submission; `dry_run` previews only (section 9) |
| GET | `/v1/jobs/{id}` | read | stored input and result only for tokens holding `spend` |
| GET | `/v1/jobs/{id}/status` | read | |
| GET | `/v1/jobs/{id}/result` | read | 409 if non-terminal; bounded result; input is never serialized |
| GET | `/v1/jobs/{id}/events` | read | `limit`; at most 100 persisted lifecycle events (section 9) |
| POST | `/v1/jobs/{id}/cancel` | spend | idempotent on terminal states |
| POST | `/v1/webhook-subscriptions` | webhook:admin | 201; returns `signing_secret` once |
| GET | `/v1/webhook-subscriptions` | webhook:admin | query: consumer, active_only |
| POST | `/v1/webhook-subscriptions/{id}/rotate-secret` | webhook:admin | returns `{id, signing_secret}` once |
| POST | `/v1/webhook-subscriptions/{id}/activate` | webhook:admin | |
| POST | `/v1/webhook-subscriptions/{id}/deactivate` | webhook:admin | |
| DELETE | `/v1/webhook-subscriptions/{id}` | webhook:admin | 204 |
| POST | `/v1/admin/audit-capability/{name}` | Secret | |
| POST | `/v1/admin/kill-switch` | Secret | |
| GET | `/v1/admin/budget` | Secret | Runtime budget limits |
| PUT | `/v1/admin/budget` | Secret | Audited change without a restart ([Budget limits](../operator/budget-limits.md)) |
| GET | `/v1/cost/summary` | read | Aggregate daily cost; query: `capability_class`, `since`, `until` |
| GET | `/v1/cost/workloads` | read | Recent workload cost detail; query: `limit` (1-100, default 20), `state`, `capability_id`, `provider_id`, `provider_type`, `since`, `until` |
| GET | `/v1/cost/burn-rate` | read | Monthly burn-rate forecast; `window_days` 1-366, default 30 |
| GET | `/v1/guardrails` | read | Pre-spend rules, mode, and aggregate counters |
| POST | `/v1/guardrails/preview` | read | Inspects one payload with no provider, database, audit, or counter writes |
| GET | `/v1/provider-ops/descriptors` | read | Safe persisted descriptors; query: `capability_id`, `enabled_only`, `limit` (1-100) |
| GET | `/v1/provider-ops/descriptors/{provider_id}` | read | One safe descriptor, no live egress |
| GET | `/v1/provider-ops/{provider_id}/availability` | read | One bounded availability probe; `limit` (1-100) |
| GET | `/v1/provider-ops/{provider_id}/health` | read | Persisted health; `probe=true` makes one live probe |
| GET | `/v1/runpod/catalogue` | read | RunPod market read; `refresh=true` makes one live call |
| GET | `/v1/volumes/{volume_id}/objects` | read | Bounded page; query: `data_center_id`, `prefix`, `max_items` (1-500, default 200) |
| GET | `/v1/volumes/{volume_id}/objects/{object_key}` | read | One base64-framed chunk; query: `data_center_id`, `offset`, `max_bytes` (up to 128 KiB) |
| POST | `/v1/admin/volumes/{volume_id}/objects` | Secret | Bounded base64 upload with explicit intent and idempotency key |
| DELETE | `/v1/admin/volumes/{volume_id}/objects/{object_key}` | Secret | Delete after destructive intent and confirmation |
| GET | `/v1/pods/{pod_id}/logs` | read | Ordered, redacted, bounded logs; `max_lines` (1-200, default 100), `max_bytes` (up to 128 KiB, default 64 KiB) |
| GET | `/v1/admin/runpod/{pods,endpoints,templates,volumes,registry-auths}` and `.../{resource_id}` | read | RunPod account resource reads; no `X-Pitwall-Secret` |
| POST | `/v1/admin/runpod/{pods,endpoints,templates,volumes,registry-auths}` | Secret | Create a RunPod resource |
| PATCH | `/v1/admin/runpod/{pods,endpoints,templates,volumes}/{resource_id}` | Secret | Update (`volumes`: grow) |
| DELETE | `/v1/admin/runpod/{pods,endpoints,templates,volumes,registry-auths}/{resource_id}` | Secret | Delete (`pods`: terminate) |
| POST | `/v1/admin/runpod/pods/{resource_id}/action` | Secret | Pod action |
| POST | `/v1/admin/runpod/registry-auths/{resource_id}/replace` | Secret | Delete and recreate; the identifier changes |
| GET | `/v1/admin/runpod/hub/templates`, `/search`, `/{resource_id}` | read | Read-only Hub catalogue; `limit`, `offset` on the list; `query`, `limit` on search |
| POST | `/v1/admin/runpod/onboarding/{plan,apply,status,resume,rollback}` | Secret | Resumable onboarding (addendum below) |

Auth values are the bearer scope that `_required_scope` assigns (`pitwall.api.app`); `Secret`
means the `server:admin` scope plus the `X-Pitwall-Secret` header. When bearer authorization
is enabled, `ApiTokenMiddleware` requires `Authorization: Bearer <token>` on every non-health
row and returns 403 when the authenticated token lacks that route's scope. With no
token configured (loopback development) every caller holds every scope. The RunPod
account resource routes are described in
[RunPod account resource controls](../operator/runpod-resource-controls.md) and the volume and
pod-log routes in [RunPod volume files](../operator/runpod-volume-files.md).

---

## 4. Public Interfaces

```python
# app.py
app: FastAPI                                             # server startup
AdminSecretMiddleware(app, secret)                       # fail-closed admin gate
ApiTokenMiddleware(app, authorizer)                      # scoped bearer gate
InboundRateLimitMiddleware(app, config, authorizer)      # default inbound limiter
RequestBodyLimitMiddleware(app, max_body_bytes)          # request body cap
BearerTokenAuthorizer(master_token, scoped_tokens_json)  # scope grants

# exceptions.py
PitwallApiError, CapabilityNotFound, CapabilityDisabled, CapabilityConflict,
ProviderNotFound, ProviderUnavailable, ProviderConflict, RateLimited,
LeaseNotFound, LeaseStateConflict, ChangeSetTooBroad, UnsupportedLeasePatch, EmptyLeasePatch,
LeaseExpiryLimitExceeded, IdempotencyConflict, IdempotencyMismatch, PreSpendPayloadRejected,
ProviderCapabilityMissing, WebhookSubscriptionNotFound, WebhookTargetNotAllowed,
WorkloadNotFound, JobNotReady, JobNotCancellable, JobCancelFailed, ServeConflict,
ServeInvalidGpuClass, ServeRateRequired, ServeUnknownVariant, ServeTemplateInvalid,
ServeTtlBelowStartup, ServeStalePrice, ServeWarmFailed, ServeVerificationFailed, ServeLaunchFailed

# leases/
run_launch(pool, capability, provider, **kwargs) -> dict[str, Any]
run_teardown(lease_id, **kwargs) -> LeaseTeardownResult
LeaseTeardownResult = dataclass(lease, event, published_subscribers)

# admin/emergency.py
run_kill(reason, actor, *, terminate_compute=True) -> KillReport

# admin/kill_switch.py
KillReport = dataclass(triggered_at, reason, tailscale_acl_updated,
                       devices_removed, pods_terminated, total_duration_ms, errors)

# schemas/
validate_provider_registration_config(**kwargs)  # raises ValueError
lease_patch_conflicting_fields(patch) -> list[str]
ServeCreate, ServeResponse

# serve.py
ServeRequest, ServeResult
serve_model(pool, request, *, base_url, settings, catalogue=None) -> ServeResult
```

The REST `POST /v1/serve` contract remains registry-backed. The database-free catalogue planner is
intentionally CLI-only (`serve --plan-only`) and does not add or change a route contract.

The RunPod REST v2 migration is outbound-only and does not rename Pitwall routes or response
fields. Lease launch still accepts the established provider/workload configuration. A request
that needs a v1-only placement or resource-floor option is sent through the bounded v1 create
path; an unsupported template entrypoint/readme operation fails before any provider write. See
[ADR 0006](../decisions/0006-runpod-rest-v2-control-plane.md).

---

## 5. Configuration

**Required at import** (`pitwall.api.app`): `DATABASE_URL`, `REDIS_URL`
(`RUNPOD_API_KEY` is resolved at the first RunPod operation).

**Optional at import:**

| Variable | Default | Effect |
|---|---|---|
| `PITWALL_ADMIN_SECRET` | unset | Configures the admin secret. `AdminSecretMiddleware` is always installed; unset means `/v1/admin/*` fails closed with 401 (`pitwall.api.app`, `pitwall.api.app:AdminSecretMiddleware`). A non-loopback `PITWALL_API_HOST` also requires it and `PITWALL_API_TOKEN`, else startup exits `78` (`PITWALL_UNSAFE_ALLOW_INSECURE_BIND=1` overrides). |
| `PITWALL_API_TOKEN` | unset | All-scopes operator bearer token; required, together with `PITWALL_ADMIN_SECRET`, for a non-loopback `PITWALL_API_HOST`. |
| `PITWALL_API_SCOPED_TOKENS` | unset | JSON object mapping opaque tokens to explicit API scopes. |
| `PITWALL_INBOUND_RATE_LIMIT` | `120/60s` | Inbound REST rate limit; explicit `off`, `disabled`, or `none` disables it. |
| `PITWALL_API_MAX_BODY_BYTES` | `8388608` | Request body cap enforced by `RequestBodyLimitMiddleware`; a non-integer or non-positive value exits `os.EX_CONFIG`. |
| `PITWALL_API_HOST` / `PITWALL_API_PORT` | `127.0.0.1` / `8080` | Bind address read by `python -m pitwall.api`. |
| `PITWALL_API_MAX_CONCURRENCY` | `100` | Concurrent request limit passed to uvicorn by `python -m pitwall.api`; at least 1. |

**Runtime** (`config.py` via `load_settings_from_env()`):

| Variable | Default | Used by |
|---|---|---|
| `RUNPOD_REST_API_URL` | `https://api.runpod.io/v2` | Primary RunPod control-plane client |
| `RUNPOD_REST_V1_API_URL` | `https://rest.runpod.io/v1` | Bounded pod create/reset compatibility path only |
| `RUNPOD_NETWORK_VOLUME_ID` | — | Pod lease launch |
| `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` | — | Inference tracing |
| `R2_ENDPOINT/ACCESS_KEY/SECRET_KEY` | — | Kill-switch R2 cleanup |
| `R2_BUCKET_STAGING` | `pitwall-staging` | Kill-switch R2 cleanup |
| `TAILSCALE_OAUTH_CLIENT_ID/SECRET/TAILNET` | — | Kill-switch |
| `PITWALL_MONTHLY_BUDGET_USD` | `50.0` | Budget gate |
| `PITWALL_PER_REQUEST_MAX_USD` | `10.0` | Per-request ceiling |
| `PITWALL_DEFAULT_LEASE_TTL_S` | `7200` | Lease TTL |

---

## 6. Failure Modes & Error Types

All API exceptions inherit `PitwallApiError`. Error envelope: `{"error": "<code>", ...}`. Routes over services with their own structured errors (RunPod control plane, onboarding, webhook configuration) raise `ApiErrorResponse` with that service's redacted body, so they use the same top-level envelope; `install_api_error_handler(app)` serializes every one.

| Exception | Trigger | HTTP |
|---|---|---|
| `CapabilityNotFound` | `repo.get_by_name()` → None | 404 |
| `CapabilityDisabled` | `CapabilityDisabledError` from resolver | 409 |
| `CapabilityConflict` | Duplicate name on create | 409 |
| `ProviderNotFound` | `repo.get()` → None | 404 |
| `ProviderUnavailable` | `NoHealthyProviderError` from resolver | 503 |
| `ProviderConflict` | Duplicate name on create | 409 |
| `RateLimited` | `QueueClient.cancel()` raises | 503 |
| `LeaseNotFound` | `repo.get()` → None | 404 |
| `LeaseStateConflict` | Teardown non-ACTIVE lease | 409 |
| `ChangeSetTooBroad` | PATCH spans image+GPU+volume | 400 |
| `IdempotencyMismatch` | Reuse key, different body | 422 |
| `WorkloadNotFound` | `repo.get(workload_id)` → None | 404 |
| `JobNotReady` | GET /result on non-terminal state | 409 |
| `UnsupportedLeasePatch` | PATCH names a field other than `renewal_policy`, `auto_teardown_on_expiry`, or `idempotency_key` | 422 |
| `EmptyLeasePatch` | PATCH names no setting | 422 |
| `LeaseExpiryLimitExceeded` | Renewal would put expiry past the allowed horizon | 409 |
| `IdempotencyConflict` | Lease mutation key reused with a different operation or payload | 422 |
| `ProviderCapabilityMissing` | `POST /v1/admin/providers` names an unregistered capability id | 422 |
| `PreSpendPayloadRejected` | Pre-spend guardrail blocks a payload | 422 |
| `InvalidProxyPath` | OpenAI proxy path fails validation | 400 |
| `WebhookSubscriptionNotFound` | Unknown subscription id | 404 |
| `WebhookTargetNotAllowed` | Webhook URL rejected by the egress policy | 422 |
| `BudgetRejected` | Budget gate rejects launch | 402 |

Errors raised before routing, by middleware or request validation, use their own bodies:

| Status | Body | Source |
|---|---|---|
| 400 / 413 | `{"error": "request_rejected", "detail": "invalid content length" \| "request body too large"}` | `RequestBodyLimitMiddleware` |
| 401 | `{"detail": "invalid or missing bearer token"}` with `WWW-Authenticate: Bearer` | `ApiTokenMiddleware` |
| 401 | `{"detail": "admin routes disabled: PITWALL_ADMIN_SECRET is not configured"}` or `{"detail": "invalid or missing X-Pitwall-Secret"}` | `AdminSecretMiddleware` |
| 403 | `{"detail": "bearer token lacks required scope", "required_scope": "<scope>"}` | `ApiTokenMiddleware` |
| 422 | `{"error": "invalid_request", "detail": [{"type": "<pydantic error type>"}, ...]}` | request validation, never echoing input |
| 429 | `{"detail": "rate limit exceeded"}` with `Retry-After` | `InboundRateLimitMiddleware` |
| 503 | `{"error": "rate_limiter_unavailable"}` with `Retry-After: 1` | `InboundRateLimitMiddleware` failing closed |
| 402 | `{"error": "budget_rejected", "reason": ..., "snapshot": {...}}` | `BudgetRejected` handler |

**Edge cases:** `POST /v1/leases` + `dry_run=True` → launch plan, no lease persisted.
`DELETE /v1/leases/{id}` is idempotent (suppresses `LeaseNotFound`).
`POST /v1/jobs/{id}/cancel` on terminal state → returns current workload (no-op).
`run_teardown()` with no `redis_client` → logs warning, returns 0, no exception.

---

## 7. Testing

**`tests/api/`** (contract + integration):

`test_capabilities_contract.py`, `test_providers_contract.py`, `test_leases_contract.py`,
`test_inference_contract.py` (idempotency, dry_run, budget rejection),
`test_jobs_contract.py`, `test_webhook_subscriptions_contract.py`,
`test_admin_auth_matrix.py` (all `/v1/admin/*` — missing/wrong/correct secret),
`test_error_envelope.py`, `test_openapi_snapshot.py`, `test_route_inventory.py`,
`test_route_precedence.py`,
`test_e2e_lease_lifecycle.py`, `test_e2e_sync_inference.py`, `test_e2e_async_job_webhook.py`,
`test_openai_proxy.py`, `test_openai_proxy_fallback.py`, `test_openai_proxy_trace.py`,
`test_inference_langfuse.py`, `test_budget_trace.py`, `test_e5_audit_checks.py`,
`test_jobs_read_cancel.py`.

**`tests/admin/` + `tests/security/`**: `test_kill_switch_route.py`,
`test_admin_auth_surface.py`, `test_webhook_receiver_signed.py`, `test_schemathesis_fuzz.py`.

**Other:** `tests/audit/test_rest_audit_mode.py`, `tests/release/test_dry_run_tier.py`,
`tests/integration/test_reconcile_idempotency_concurrency.py`.

---

## 8. Dependencies

**Internal:** `pitwall.config` (`require_runtime_env`, `load_settings_from_env`),
`pitwall.core.models` (Capability, Provider, Lease, etc.), `pitwall.core.enums`
(LeaseState, ProviderType, etc.), `pitwall.core.ids` (`ulid_new()`),
`pitwall.db.repository` (all repositories + `insert_audit()`),
`pitwall.db.kill_log` (`persist_kill_report()`), `pitwall.cost` (`BudgetRejected`, `BudgetGate`),
`pitwall.cost.sync_gate` (`estimate_cost()`),
`pitwall.routing.production` (`ProductionRoutingService`),
`pitwall.resolver` (resolver exception types),
`pitwall.routing.fallback` (`execute_openai_with_fallback()`),
`pitwall.routing.openai` (`resolve_openai_provider_chain()`),
`pitwall.runpod_client.pods` (pod creation/termination),
`pitwall.runpod_client.templates` (template management),
`pitwall.runpod_client.queue` (`QueueClient`),
`pitwall.runpod_client.gpu` (GPU name validation),
`pitwall.workload_lifecycle` (workload state transitions),
`pitwall.leases.state` (`transition_lease_state()`, `TERMINAL_LEASE_STATES`),
`pitwall.observability.langfuse` (`emit_inference_trace()`),
`pitwall.audit.capability` (`audit_capability()`),
`pitwall.r2_temp_credentials` (`vend_r2_temp_credential_pod_env()`),
`pitwall.r2_staging_cleanup` (`cleanup_staging_for_pods()`).

**External:** `fastapi`, `pydantic` v2, `starlette` (ASGI types), `asyncpg`, `redis.asyncio`,
`httpx`, `hmac` (`compare_digest`).

---

## Addendum: RunPod onboarding admin routes

The feature-local router at `pitwall.api.routes.onboarding` exposes five POST
operations under `/v1/admin/runpod/onboarding`: `plan`, `apply`, `status`,
`resume`, and `rollback`. Plan/status/rollback accept the shared
`RunPodOnboardingRequest`; apply/resume accept the shared command envelope and
require the exact confirmed plan ID. The adapter contains no onboarding logic.
It delegates to `RunPodOnboardingService` and maps bounded typed errors to 409
(confirmation/current-state conflict), 422 (local validation/discovery), 503
(audit unavailable), or 502 (provider/service failure)
(`pitwall.api.routes.onboarding`).

The global app registers this router behind the existing admin-secret boundary
and owns one onboarding service for lifespan cleanup. The request/response and
recovery examples are in
[RunPod onboarding](../operator/runpod-onboarding.md). Isolated route coverage
is in `tests/onboarding/test_surfaces.py`; global route inventory, auth, and
OpenAPI snapshot coverage remain the integration gates.

---

## 9. Production routing and async submit

The feature-local routing router adds:

| Method | Path | Contract |
| --- | --- | --- |
| `POST` | `/v1/routing/preview` | Strict, non-persisting, no-egress production plan preview |
| `POST` | `/v1/jobs` | Guarded and budgeted provider-neutral asynchronous submission; `dry_run` previews only |
| `GET` | `/v1/jobs/{workload_id}/events` | At most 100 persisted lifecycle events plus explicit stream availability |

All three delegate to `ProductionRoutingService`. Submit honors the request or header idempotency
key, returns the stored route plan and structured cost quote, and lets the existing
`BudgetRejected` exception handler retain the canonical HTTP 402 envelope. Planner/resolver
failures map to existing capability/provider errors, and blocked pre-spend inspection maps to the
existing safe payload-rejection envelope. Existing status reads include the route plan and selected
provider identity; result reads delegate to the shared bounded, input-free `job_result()` model,
map its non-terminal state back to the established 409 `job_not_ready` response, and retain the
legacy terminal field names
(`pitwall.api.routes.routing`, `pitwall.api.routes.jobs`).
