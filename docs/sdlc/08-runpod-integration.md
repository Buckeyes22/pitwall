# RunPod Client Integration — SDLC Design Document

## 1. Purpose & Scope

`pitwall.runpod_client` is the canonical integration layer between Pitwall and RunPod GPU cloud. It wraps every RunPod surface — Pod REST API, GraphQL market/billing API, Serverless queue API, Serverless load-balancer API, and template management — behind typed Python interfaces. No RunPod API call bypasses this package. All network I/O uses `httpx`; the SDK (`runpod`) is used only for template creation/listing compatibility. Every stateful pod operation has sync and async variants.

RunPod control-plane writes use REST v2 by default. The authoritative decision and compatibility
matrix are [ADR 0006](../decisions/0006-runpod-rest-v2-control-plane.md). Its schema source is the
official [RunPod v2 OpenAPI document](https://api.runpod.io/v2/openapi.json), observed
2026-08-31 as version `2.0.0` with SHA-256
`688c7b6f1d5386f04aa6d029b1c744fa11686540350e4411e8f3abe4bbf33d38`.
REST v2 is treated as a distinct contract: strict resource request models, renamed paths, nested
fields, collection envelopes, and unified pod actions are validated explicitly.

---

## 2. Components

### `providers/` reference adapter
`pitwall.providers` is the static adapter seam used by the four current providers. It is deliberately separate from `pitwall.core.models.Provider`: the core model is the persisted fulfillment record selected by routing, while an adapter owns only the capabilities it truthfully implements.

```python
class ProviderAdapter(Protocol):
    id: str
    name: str
    credential_schema: type[pydantic.BaseModel]
    capabilities: frozenset[ProviderCapability]
    def pricing_model(capability, provider_record) -> TaggedPricingModel

class ComputeProvider(ProviderAdapter, Protocol):
    async def provision(ProvisionRequest) -> ProvisionResult
    async def status(StatusRequest) -> StatusResult
    async def reconcile(ReconcileRequest) -> ReconcileResult
    async def teardown(TeardownRequest) -> TeardownResult

class InferenceProvider(ProviderAdapter, Protocol):
    async def infer(InferenceRequest) -> InferenceResult

class AsyncInferenceProvider(ProviderAdapter, Protocol):
    async def submit(AsyncInferenceRequest) -> AsyncInferenceSubmission

class AsyncInferenceStatusProvider(ProviderAdapter, Protocol):
    async def job_status(AsyncInferenceStatusRequest) -> AsyncInferenceStatusResult

class AsyncInferenceCancelProvider(ProviderAdapter, Protocol):
    async def cancel_job(AsyncInferenceCancelRequest) -> AsyncInferenceCancelResult
```

`ProviderRegistry` registers adapters in deterministic order, rejects duplicate/invalid ids and
contract/capability mismatches, and provides capability-aware compute/inference lookup. A persisted
`credential_ref` is resolved only inside the chosen adapter operation. `run_launch` and
`run_teardown` also resolve the provider's `credential_ref` when called without an explicit key
(an explicit key wins), so a REST stop or a routed launch uses the provider's own RunPod account;
the default reference `RUNPOD_API_KEY` is the process credential. Errors report names and
field paths, never credential values. See `20-provider-plugins.md` for the complete contract.

`RunPodProvider` is the built-in reference adapter under id `runpod`. It delegates provision to
`api.leases.launch.run_launch`, teardown to `api.leases.teardown.run_teardown`, status/reconcile to
`runpod_client.pods`, and pricing to `parse_pricing_model`. Its normalized synchronous inference
operation preserves the existing `ServerlessLBClient` base URL, one-attempt behavior, embedding
arguments, and close semantics. Its normalized async operations preserve the existing
`QueueClient.run`, `QueueClient.status`, and `QueueClient.cancel` call shapes, mapping only current
RunPod queue states into `WorkloadState`. Async job and lease paths dual-write generic and RunPod
ids, so existing provider calls and compatibility payloads remain intact.

`RunPodCredentials` requires `api_key: SecretStr` and supports optional `graphql_url`,
`rest_api_url` (v2), and `rest_v1_api_url` (bounded legacy) overrides. Credential URLs must be
absolute HTTP(S) URLs with no userinfo, query string, or fragment. Secrets are never accepted in
URLs; RunPod API authentication remains header-only. The v1 override is passed only to an admitted
legacy pod create; status, reconciliation, and teardown use the v2 override.

`VastProvider` is the first non-RunPod plugin registered by `create_default_registry()` / `get_default_registry()` under id `vast`. It proves the provider seam with a provider that does not delegate to existing RunPod services:

- `VastCredentials` requires `api_key: SecretStr`, optional `vast_api_url` (default `https://console.vast.ai/api/v0`), and `client_id` (default `"me"`). The base URL uses the same safe URL invariant as RunPod credential URLs: absolute HTTP(S), no userinfo, no query string, no fragment. Vast API keys are sent only as `Authorization: Bearer ...`; never in a URL.
- `pricing_model()` uses `PerSecondPricing`. Vast hourly money fields in provider config (`price_per_hour` / `rate_per_hour` / `dph_total` plus optional `bid_price_per_hour` / `bid_per_hour` / `min_bid`) are converted to Decimal per-second rates. The optional bid rate is preserved as `bid_rate_per_second`, so `upper_bound()` reserves against the larger of the live rate and bid ceiling.
- `provision()` accepts an `ask_id` / `offer_id`, builds a Vast create-instance body from provider `config.create` plus whitelisted request overrides, and sends `PUT /asks/{id}/`. If a bid price is configured, it is sent as Vast's `price` field in $/hour. Create payloads are encoded with Decimal money as JSON numbers rather than floats.
- `status()` reads `GET /instances/{id}/` and maps Vast states into provider-neutral `ResourceStatus`. `PREEMPTED`, outbid, interrupted, or evicted instances map to `ResourceStatus.FAILED` with `raw["pitwall_preempted"] = true` and `raw["pitwall_safe_state"] = "failed"`.
- `reconcile()` polls either requested external ids or `GET /instances/`. Preempted resources are converged to the safe persisted lease state `failed` when a pool is available.
- `teardown()` resolves `external_resource_id` with a legacy RunPod-column fallback, sends `DELETE /instances/{id}/`, and closes the lease. New Vast rows leave `runpod_pod_id` null.
`TogetherProvider` is the built-in inference-only plugin registered by the default registry under id
`together`. It targets Together's OpenAI-compatible `POST /v1/chat/completions` API using
`Authorization: Bearer <api_key>` and keeps secrets out of URLs. `TogetherCredentials` requires
`api_key: SecretStr`, accepts a safe `base_url` override (default `https://api.together.xyz/v1`),
and rejects URL userinfo, query strings, and fragments.

Together pricing is always per-token. `TogetherProvider.pricing_model()` requires a per-token
capability and returns the `PerTokenPricing` variant parsed from the provider record's
`config.cost`. Admission callers should bind the request payload to a `CostQuote` and reserve
`upper_bound()`, which uses `max_tokens` / `max_output_tokens` / `max_completion_tokens` /
`max_new_tokens` as the completion ceiling before Together returns actual usage. The exact
completion cost is therefore unknown until response usage is available, but the pre-spend gate can
still reserve a Decimal upper bound.

`TogetherProvider.infer()` is the direct inference helper. It adds the provider record's `config`
`model` (or accepts a payload-provided `model` override), posts the request body with header-only
auth, and maps the OpenAI-compatible response into `TogetherInferenceResult` with content, model,
finish reason, token usage, and raw response. Together does not implement or advertise compute
lifecycle methods because it is not a leaseable resource provider.

### `workloads.py`
`WorkloadConfig` (Pydantic BaseModel): `name`, `capability`, `template_name`, `gpu_types: list[str]` (min_length=1), `gpu_count`, `container_disk_gb`, `min_vcpu`, `min_memory_gb`, `cloud_type`, `gpu_type_priority`, `data_center_priority`, `allowed_cuda_versions`, `ports`. **Invariant:** `gpu_types` is validated at construction via `validate_canonical_gpu_names()` — shorthand aliases like `"H100"` are rejected, while five historical full-name aliases normalize to their live ids (`pitwall.runpod_client.workloads`).

### `gpu.py`
The 46 canonical ids and their VRAM values follow the **RunPod gpuTypes snapshot 2026-08-27**. Shorthand aliases are rejected rather than silently normalized; five historical full-name aliases remain accepted and normalize to their live ids.

```python
CANONICAL_GPU_NAMES: frozenset[str]  # 46 live ids, e.g. "NVIDIA H100 80GB HBM3"
LEGACY_GPU_NAME_ALIASES: Mapping[str, str]
GPU_VRAM_GB: Mapping[str, int]
def is_canonical_gpu_name(gpu_name: str) -> bool
def canonical_gpu_name_suggestions(gpu_name: str) -> tuple[str, ...]
def validate_canonical_gpu_name(gpu_name: str) -> str
def validate_canonical_gpu_names(gpu_names: Iterable[str]) -> list[str]
class NonCanonicalGPUNameError(ValueError):
    gpu_name: str; suggestions: tuple[str, ...]
# aliases: validate_gpu_type, validate_gpu_types
```

Shorthand suggestions in `_SHORTHAND_GPU_SUGGESTIONS` are diagnostic only — validation always raises for them. Legacy full-name aliases are the sole normalized inputs.

### `registry.py`
Selects the registry credential ID by image prefix (GHCR, GitLab, Docker Hub). Pod/template v2
requests send that ID as `registry`. The module also provides async CRUD for RunPod
container-registry credentials through REST v2 `/registries`.

**Env-helper functions** (unchanged):

```python
GHCR_PREFIX = "ghcr.io"; GITLAB_REGISTRY_PREFIX = "registry.gitlab.com"; DOCKER_HUB_PREFIX = "docker.io"
LEGACY_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID"
GHCR_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID_GHCR"
GITLAB_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID_GITLAB"
DOCKER_HUB_REGISTRY_AUTH_ENV = "RUNPOD_REGISTRY_AUTH_ID_DOCKER_HUB"

def image_registry_prefix(image_ref: str | None) -> str | None
def registry_auth_env_names_for_image_ref(image_ref: str | None) -> tuple[str, ...]
def registry_auth_id_from_env(image_ref: str | None = None, *, environ: Mapping | None = None) -> str | None
```

GHCR and GitLab fall back to `LEGACY_REGISTRY_AUTH_ENV`. `image_ref=None` uses `LEGACY` → `GHCR` priority.

**CRUD surface** (`registry.py`):

```python
class RegistryAuthError(RuntimeError): ...

class RegistryAuthCreateInput(BaseModel):
    name: str; username: str; password: str

class ContainerRegistryAuth(BaseModel):
    # RunPod never returns username or password after creation
    id: str; name: str

async def create_container_registry_auth(name, username, password, *, timeout_s=60.0) -> ContainerRegistryAuth
    # POST /registries — raises RegistryAuthError on 4xx/5xx

async def get_container_registry_auth(auth_id, *, timeout_s=60.0) -> ContainerRegistryAuth | None
    # GET /registries/{auth_id} — returns None on 404

async def list_container_registry_auths(*, timeout_s=60.0) -> list[ContainerRegistryAuth]
    # GET /registries; requires the registries response envelope

async def delete_container_registry_auth(auth_id, *, timeout_s=60.0) -> None
    # DELETE /registries/{auth_id} — idempotent (404 swallowed)
```

Auth: `Bearer {RUNPOD_API_KEY}`, base `https://api.runpod.io/v2` (configurable via
`RUNPOD_REST_API_URL`).

### `pods.py`
Pod lifecycle through REST v2. Handles strict request construction, response-envelope validation,
readiness probing, GPU/cloud fallback, volume-attach hang detection, and cost gates.

**REST helpers:** `_rest_request(...)` targets `RUNPOD_REST_API_URL` (default
`https://api.runpod.io/v2`). `_legacy_rest_request(...)` targets the distinct
`RUNPOD_REST_V1_API_URL` (default `https://rest.runpod.io/v1`) and is reachable only for an
option that v2 cannot preserve. Both use header-only bearer authentication, bounded timeouts,
redacted length-bounded errors, JSON validation, and bounded safe retries. V2 writes are built by
extra-forbid Pydantic models, so v1 names cannot leak into v2 requests.

**Exceptions:** `RunPodError` (base), `NoCapacityError` (all GPU types exhausted), `ProviderFallbackRequested` (skip provider), `PodStartupTimeout`, `PodVolumeAttachTimeout(pod_id, attach_timeout_s)`, `ProviderAttachHangRecoveryRequested(pod_id, attach_timeout_s)`, `PodStartupFailed`, `RunPodRestError(method, path, status_code, body)`.

**Probe order:** `ssh_localhost` (placeholder → `not_configured`) then `runpod_proxy` (`https://{pod_id}-{port}.proxy.runpod.net`). The proxy tries each launch's `readiness_path` first, then `/health`, `/status.json`, and `/`, removing duplicates while retaining that order; for example, `https://eptest00000001-8000.proxy.runpod.net/health_generate` is tried before the established fallbacks. 524 is distinguished from other errors (`pods.py`).

**Create:** `create_pod_with_fallback_sync(...)` maps `imageName`→`image`,
`cloudType`→`cloud`, `gpuTypeIds`/`gpuCount`→`gpu.id`/`gpu.count`,
`containerDiskInGb`→`disk`, command argv→quoted `args`, registry auth→`registry`, and storage→
structured `mounts`. Because v2 accepts one pod GPU ID, custom priority is preserved as
deterministic single-GPU requests in caller order; `cloud_type=ALL` tries `COMMUNITY`, then
`SECURE`. Capacity failures advance to the next combination. Ambiguous v2 create failures are not
retried. Pre-readiness guards still check allocation and cost, and terminate an unsuitable paid
pod before fallback.

The bounded v1 create path is used when a request needs `gpuTypePriority=availability`,
`dataCenterPriority=availability` with a data-center preference, `minVCPUPerGPU`,
`minRAMPerGPU`, `dockerEntrypoint`, or `supportPublicIp=true`. No such option is discarded.
`rest_v1_api_url` is separate from the v2 URL in provider credentials.

**Readiness wait:** `wait_for_pod_runtime_sync(pod_id, initial, timeout_s=600.0, poll_s=15.0, volume_attach_timeout_s, readiness_path="/health")` — polls `GET /pods/{pod_id}` until `runtime` + `portMappings` + probe. Attaches readiness signals to `pod["readiness"]`. Raises `PodStartupTimeout`, `PodVolumeAttachTimeout`, or `PodStartupFailed` (`pods.py`).

**Getters:** `get_pods_sync() -> list[dict]` requires the v2 `pods` collection envelope;
`get_pod_sync(pod_id) -> dict | None` normalizes v2 fields to Pitwall's established shape and
falls back to the SDK only when runtime/port mappings are absent. Async variants delegate via
`asyncio.to_thread`.

**Terminators:** `terminate_pod_sync(pod_id)` (idempotent: 404 swallowed), `terminate_all_with_tag(name_prefix="pitwall-") -> int`.

**Lifecycle:** start, stop, restart, and terminate use `POST /pods/{id}/action` with a strict
`{"action": ...}` body. `reset_pod_sync` remains the bounded legacy `POST /pods/{id}/reset`
because v2 has no reset action. Update uses `PATCH /pods/{id}` with v2 names such as `registry`.
Update requires at least one field. All public operations retain async variants.

**Env vars:** `PITWALL_RUNPOD_CAPACITY_ERROR_SUBSTRINGS` (default includes `"no longer any instances available"`, `"resourcesunavailable"`, `"insufficient"`), `PITWALL_VOLUME_ATTACH_TIMEOUT_S` (default 300.0s) (`pods.py`).

### `queue.py`
Async httpx wrapper for RunPod queue-based serverless (`api.runpod.ai/v2/{endpoint_id}/`). Auth: `Bearer {RUNPOD_API_KEY}`.

```python
class QueueJob(BaseModel): id, status, output, error, raw
class QueueHealth(BaseModel): jobs: dict[str, int], raw
class QueueCancelResult(BaseModel): cancelled, raw
class QueuePurgeResult(BaseModel): purged, raw

class QueueClient(*, api_key, timeout_s=600, retry_delays=(1.0,3.0,9.0), max_retry_after_s, sleep=asyncio.sleep, clock=utc_now, transport)
    async runsync(endpoint_id, input, webhook=None, policy=None) -> QueueJob
    async run(endpoint_id, input, webhook=None, policy=None) -> QueueJob
    async status(endpoint_id, job_id) -> QueueJob
    async health(endpoint_id) -> QueueHealth
    async cancel(endpoint_id, job_id) -> QueueCancelResult
    async purge_queue(endpoint_id) -> QueuePurgeResult
```

Job submission (`runsync` and `run`) is billable, so it replays only failures that provably never reached the server: connect failures and 429 (`retry_connect_failure`, `retry_rate_limit_only` in `pitwall.runpod_client.retry`), with `Retry-After` honoured up to `max_retry_after_s`. A 5xx or 4xx on submission is raised at once, never replayed (`pitwall.runpod_client.queue`).

### `lb.py`
Async httpx wrapper for RunPod load-balancer (`{endpoint_id}.api.runpod.ai`). Auth: `Bearer {RUNPOD_API_KEY}`.

```python
class ProbeResult(BaseModel): healthy, status_code, error, latency_ms
class LBResponse(BaseModel): status_code, data, raw

class LBClient(api_key, timeout_s=120.0, retry_delays=(1.0,3.0,9.0), max_retry_after_s, sleep, clock, transport)
    async post(endpoint_id, path, json) -> LBResponse
    async get(endpoint_id, path) -> LBResponse
    async ping(endpoint_id) -> bool  # True on 200
    async probe(endpoint_id, path="/ping", timeout_s=5.0) -> ProbeResult
```

`probe()` sets `error="524"` for Cloudflare 524, `"timeout"` for timeout, and `"connection_error"` for network errors (`pitwall.runpod_client.lb`).

### `serverless.py`
Async httpx wrapper for `/v1/chat/completions` on operator-configured RunPod Serverless endpoints. `base_url` must end with `/openai/v1`. Pitwall does not publish the endpoint worker image.

```python
class ServerlessResponse(BaseModel): content, model, input_tokens, output_tokens, finish_reason, duration_ms, raw

class ServerlessClient(base_url, api_key, model, timeout_s=600, retry_delays=(1.0,3.0,9.0), max_retry_after_s, sleep, clock, transport)
    async chat_completion(messages, max_tokens, temperature=0.0, extra=None) -> ServerlessResponse
```

Retries follow the `RetryPolicy` each call site passes to `send_with_retry` (`pitwall.runpod_client.retry`, `pitwall.runpod_client.serverless`).

Also exposes async CRUD for RunPod serverless endpoint management through REST v2
`/serverless`:

```python
class EndpointScalingConfig(BaseModel):
    workers_min: int = 0        # min idle workers kept warm
    workers_max: int = 3         # max concurrent workers
    idle_timeout: int = 60      # seconds before idle worker stops
    gpu_type_id: str | None     # resolved through the v2 serverless GPU catalogue
    flashboot: bool = False      # enable flashboot for faster cold-start
    scaler_type: Literal["QUEUE_DELAY", "REQUEST_COUNT"]
    scaler_value: float
    def to_request_json() -> dict[str, Any]  # nested workers/scaling/flashboot

class Endpoint(BaseModel):
    id: str; name: str; scaling: EndpointScalingConfig
    template_id: str | None; created_at: str | None; raw: dict[str, Any]

async def create_endpoint(name, template_id, *, gpu_ids=None, gpu_pools=None,
                          excluded_gpu_types=None, image_name=None,
                          endpoint_type="QUEUE", scaling=None, timeout_s=60.0) -> Endpoint
async def get_endpoint(endpoint_id, *, timeout_s=60.0) -> Endpoint
async def list_endpoints(*, name_prefix=None, timeout_s=60.0) -> list[Endpoint]
async def update_endpoint_scaling(endpoint_id, scaling: EndpointScalingConfig, *, timeout_s=60.0) -> Endpoint
async def delete_endpoint(endpoint_id, *, timeout_s=60.0) -> dict[str, Any]
```

Create requires explicit GPU types or pools and requires `image_name` when no template is
supplied; these constraints fail before a write. GPU type IDs are translated to v2 pools plus a
curated exclusion set using `GET /catalog/gpus?product=SERVERLESS`. Workers, scaling, GPU, and
flashboot are nested strict models; endpoint lists require the `endpoints` envelope. Endpoint IDs
are normalized and path separators are rejected. HTTP failures raise redacted `RunPodRestError`.

### `serverless_lb.py`
Async httpx wrapper for BGE-M3 `/embed` on RunPod LB. Supports "Pitwall mode" via `PITWALL_EMBEDDING_VIA_PITWALL` feature flag.

```python
class EmbeddingResponse(BaseModel): dense, sparse, colbert, raw

class ServerlessLBClient(lb_base_url, api_key=None, via_pitwall=None, timeout_s=330.0, retry_attempts=4, retry_backoff_s=2.0, transport)
    async embed(texts, return_dense=True, return_sparse=True, return_colbert=False) -> dict
```

`via_pitwall=None` follows `PITWALL_EMBEDDING_VIA_PITWALL`; `False` always calls the RunPod load balancer directly; `True` routes through Pitwall when `PITWALL_BASE_URL` is set. When Pitwall mode is on (via `load_settings_from_env()`), the client routes through Pitwall `/v1/inference` and authenticates with `PITWALL_API_TOKEN` (no header when it is unset); the RunPod key is sent only to the load balancer and never to the Pitwall origin. The broker's own RunPod adapter (`providers/runpod.py`, synchronous inference) always constructs the client with `via_pitwall=False`, so a broker request cannot loop back into the broker. 30 MB payload limit (`MAX_REQUEST_BODY_BYTES`) raises `ValueError` if exceeded (`pitwall.runpod_client.serverless_lb, 26`).

### `templates.py`
Creates RunPod templates once per deterministic full-config SHA, caches in PostgreSQL
`pitwall.runpod_templates` keyed on `(name, config_sha)`. The SHA covers image digest/tag identity,
entrypoint/start command, ports, the container disk size, the registry auth id, and sorted
non-secret environment key names; values and secret-shaped key names are excluded. The RunPod
account enters only as a keyed digest (HMAC-SHA256 keyed by SHA-256 of a fixed context string plus
the API key, `template_account_digest`), so two accounts never share a cached template and the key
is never stored or recoverable. The visible template name suffix (`template_suffix`) covers the same
inputs except the account, because RunPod names are unique within one account. Also provides get/update/delete for managing existing
templates and Hub (public marketplace) template discovery.

```python
TEMPLATE_NAME = "pitwall-cloud-worker"

class TemplateEnvVar(BaseModel): key, value
class Template(BaseModel): id, name, image_name, docker_args, container_disk_in_gb,
    volume_in_gb, volume_mount_path, ports, env, is_serverless, is_public, readme
class HubTemplate(BaseModel): id, name, image_name, description, github_url, docker_args,
    container_disk_in_gb, volume_in_gb, volume_mount_path, ports, env, is_serverless,
    display_name, template_description

def image_sha(image_ref: str) -> str
def template_account_digest(api_key: str | None = None) -> str
def config_sha(image_ref: str, *, docker_entrypoint=(), docker_start_cmd=(),
               ports=None, env_keys=(), container_disk_gb=50, registry_auth_id=None,
               account_digest="") -> str
def non_secret_env_keys(env) -> tuple[str, ...]
def template_suffix(image_ref: str, **config) -> str      # config_sha[:12], without the account
def normalize_template_name(name: str) -> str
def template_display_name(template_name: str, image_ref: str, **config) -> str

async def ensure_template(pool, image_ref, *, template_name=TEMPLATE_NAME,
                          registry_auth_id=None, container_disk_gb=50,
                          volume_mount_path="/workspace", docker_entrypoint=(),
                          docker_start_cmd=(), ports=None, env=None) -> str
async def get_template(template_id: str) -> Template
async def update_template(template_id, *, name=None, image_name=None, docker_args=None,
                          container_disk_in_gb=None, volume_in_gb=None, volume_mount_path=None,
                          ports=None, env=None, is_serverless=None, is_public=None,
                          readme=None) -> Template
async def delete_template(template_id: str) -> bool
async def list_account_templates(*, api_key=None, rest_api_url=None) -> list[Template]
async def list_hub_templates(*, limit=50, offset=0) -> list[HubTemplate]
async def get_hub_template(template_id: str) -> HubTemplate

def get_image_ref_from_env() -> str    # reads PITWALL_CLOUD_WORKER_IMAGE; raises RuntimeError if unset
def get_registry_auth_id_from_env(image_ref=None) -> str | None

class TemplateNotFoundError(RuntimeError)
class TemplateDeleteError(RuntimeError)
```

Template create/get/update/delete use REST v2 `/templates` with strict `image`, `args`, `disk`,
`mounts`, `registry`, `serverless`, and `public` fields. Account list responses require the
`templates` envelope; a custom GraphQL URL remains supported only for the existing duplicate-name
lookup seam. Command vectors use the same safe argv-to-`args` conversion as pod creation.
`docker_entrypoint` create and `readme` update are rejected before a write because v2 has no
equivalent. Hub discovery remains on its established GraphQL surface.

### `runpod_control_plane.py`

`RunPodControlPlaneService` is the single policy boundary used by the RunPod resource REST,
MCP, CLI, and Textual adapters. `StrictRunPodBackend` delegates to the existing strict clients;
it does not introduce a second HTTP client or a second lease state machine. Its resource-specific
request and result models centralize strict identifiers, explicit `preview`/`apply` intent,
exact-key idempotency, bounded calls, sanitized projections, stable errors, and audit writes.
`PostgresRunPodMutationJournal` keeps the idempotency journal in `config_audit` rows
(`kind: runpod_control_plane_mutation`; states `started`, `completed`, `failed`, `compensated`)
under a per-key advisory lock with a bounded wait. A repeated key replays the stored result,
a different request with the same key is an `idempotency_conflict`, an unknown earlier outcome
is a `mutation_outcome_ambiguous`, and `release_idempotency_key` frees a key whose resource
the caller deleted (see `docs/sdlc/03-mcp-server.md` §3.4). Every applied pod create
adds `PITWALL_CREATE_ATTEMPT=<create_attempt_marker(idempotency_key)>` to the pod's env,
overriding any caller value. The marker is a 32-hex SHA-256 digest of the key, so the key
itself never reaches the pod. Both create payload builders (`_v2_create_payload` and
`_legacy_v1_create_payload`) forward it. A `pod.create` row also records `recovery`: the
attempt marker, pod name, `ttl_minutes`, and `max_cost_per_hour`, never other env values,
args, or credentials. The orphaned-workload reaper uses it to resolve an MCP pod create
whose outcome is unknown and whose error named no pod. The RunPod REST `Pod` record returned
by `GET /pods` and `GET /pods/{podId}` includes `env`, and `_normalize_v2_pod` keeps it. So
the reaper adopts only the one pod whose `env` carries the matching marker, live or
already terminated (a terminated pod an earlier attempt with the same key recorded is not
matched). A same-name
pod without it, or with another attempt's marker, is held and never adopted or terminated.
A `completed` create with no lease (a crash before the lease insert) is leased by the pod id
its row recorded. A cancelled create's cleanup (`_terminate_orphaned_create`) absorbs further
cancellations until the create thread and any terminate have finished, so the journal key lock
is never released while the thread can still create a pod.

The service covers raw pods; serverless endpoints with nested workers, scaling, GPU pools, and
excluded GPU types; account templates; network volumes; and registry auth. Account-template
listing uses REST v2 and is separate from read-only Hub list/get/search. Volumes are grow-only.
Registry replacement is an explicit delete/recreate with a stable partial-failure result. Registry
passwords are accepted only as an environment-variable reference and resolved at the strict-client
call boundary. See [RunPod account resource controls](../operator/runpod-resource-controls.md) for
the complete operation matrix and operator safeguards.

Before any of the 16 mutations can call a strict client or write audit state, the service performs
one bounded pre-spend decision over the complete typed request. Preview uses the non-recording
inspection path. Because rewriting an image, command, resource identity, GPU/placement selection,
or registry username would change operator intent, every non-allow decision fails with the stable
`pre_spend_payload_rejected` code and omits the rejected resource id. Registry password references
are inspected before their values are resolved. Feature-local REST validation and CLI confirmation
failures are non-reflecting; the global MCP boundary is a serialized integration requirement.

### `graphql.py`
Async `httpx.AsyncClient` wrapper for RunPod's GraphQL endpoint (`https://api.runpod.io/graphql`), used for surfaces that REST cannot provide: live GPU prices, spot bid floor reads, spot bid resume mutation, datacenter/GPU availability enumeration, and credit balance reads.

**Construction:** `RunpodGraphQLClient(api_key, graphql_url=RUNPOD_GRAPHQL_URL, timeout_s=60.0, transport=None)`; `RunpodGraphQLClient.from_settings(settings=None, ...)` reads `PitwallSettings.runpod_api_key` via `load_settings_from_env()`. Requests authenticate only with `Authorization: Bearer {api_key}` plus `Content-Type: application/json`. The API key is never placed in the GraphQL URL.

**Models:** All response objects are Pydantic v2 models with camelCase aliases enabled. Money/price fields are `Decimal`, and response JSON is decoded with `json.loads(..., parse_float=Decimal)` so GraphQL numeric text is preserved exactly.

```python
class RunpodGraphQLClient:
    async def gpu_types() -> list[RunpodGpuType]
    async def datacenters() -> list[RunpodDatacenter]
    async def get_bid_price(gpu_type_id, dc=None, *, data_center_id=None, secure_cloud=None, gpu_count=1) -> RunpodBidPrice
    async def set_bid_price(*, pod_id, bid_per_gpu: Decimal, gpu_count=1) -> RunpodBidResumeResult
    async def credits_balance() -> RunpodCreditsBalance
    async def aclose() -> None
```

**`gpu_types()` query:** calls `gpuTypes` and returns `RunpodGpuType` entries with `id`, `displayName`, `memoryInGb`, `secureCloud`, `communityCloud`, on-demand price fields (`securePrice`, `communityPrice`, reservation prices), spot price fields (`secureSpotPrice`, `communitySpotPrice`), `lowestPrice`, and `nodeGroupDatacenters`. RunPod may return null for list fields; `availableGpuCounts`, `nodeGroupGpuSizes`, `nodeGroupDatacenters`, datacenter `gpuAvailability`, and `compliance` are normalized to empty lists while retaining their public list types. This is the discovery source for downstream GPU catalog and pricing stories.

**`datacenters()` query:** calls `myself { datacenters { ... gpuAvailability { ... } } }` and returns `RunpodDatacenter` with `gpu_availability` entries keyed by `gpu_type_id`, `stock_status`, and `available`. This is the GraphQL-only replacement for guessing availability from failed pod creation.

**Spot bid seam:** `get_bid_price()` calls `gpuTypes(input: {id})` with `lowestPrice(input: {gpuCount, dataCenterId, secureCloud, globalNetwork: false})` and returns `RunpodBidPrice` (`minimum_bid_price`, `uninterruptable_price`, spot fields, stock status, available GPU counts). `set_bid_price()` uses the `podBidResume` mutation for interruptible pods; the bid is formatted from `Decimal` into a GraphQL numeric literal and is never converted through Python `float`.

**Billing seam:** `credits_balance()` calls `myself { clientBalance currentSpendPerHr spendLimit minBalance underBalance }` and returns `RunpodCreditsBalance`. This is the account-credit read used by billing/credits work; spend admission still uses Pitwall's local budget gate as the source of policy.

`pitwall.cost.billing_read` wraps this seam for FinOps reconciliation:
- `read_billing_snapshot(client)` — thin async wrapper returning `BillingSnapshot` (typed `Decimal` fields)
- `reconcile_with_budget(client, budget_gate)` — compares RunPod `clientBalance` against Pitwall's `monthly_budget - mtd_spend` and returns `BudgetReconciliation` with `variance_usd`

**Errors:** GraphQL error envelopes (`{"errors": [...]}`) raise `RunpodGraphQLError`, a `RunPodError` subclass with the original errors retained. Non-2xx HTTP responses raise `RunpodGraphQLHTTPError`; malformed envelopes raise `RunpodGraphQLResponseError`.

### `availability.py`
5-minute TTL in-process cache keyed by `(datacenter, gpu_name, cloud_type, gpu_count)`. Thread-safe via `threading.RLock`.

```python
@dataclass(frozen=True)
class AvailabilityKey: datacenter, gpu_name, cloud_type, gpu_count
@dataclass
class AvailabilityValue: available, checked_at

class AvailabilityCache(DEFAULT_TTL_S=300.0):
    def is_available(datacenter, gpu_name, cloud_type, gpu_count) -> bool | None
        # None=missing/expired; True=available; False=unavailable
    def set_available(datacenter, gpu_name, cloud_type, gpu_count, available) -> None
    def bulk_set_available(list[tuple]) -> None
    def invalidate() -> None; def sweep_expired() -> int

def get_global_availability_cache() -> AvailabilityCache
def reset_global_availability_cache() -> None  # testing only
```

### `discovery.py`
GPU-type + datacenter discovery service.  Normalizes the raw GraphQL `gpuTypes` and `myself.datacenters` responses into an immutable, replay-friendly catalog snapshot.

```python
@dataclass(frozen=True, slots=True)
class GpuCatalogEntry:
    gpu_type_id: str
    display_name: str | None
    manufacturer: str | None
    memory_in_gb: int | None
    cuda_cores: int | None
    secure_cloud: bool
    community_cloud: bool
    secure_price: Decimal | None
    community_price: Decimal | None
    secure_spot_price: Decimal | None
    community_spot_price: Decimal | None
    lowest_bid_price: Decimal | None
    uninterruptable_price: Decimal | None
    datacenter_ids: tuple[str, ...]
    available_gpu_counts: tuple[int, ...]
    stock_status: str | None
    max_gpu_count: int | None

@dataclass(frozen=True, slots=True)
class DatacenterCatalogEntry:
    datacenter_id: str
    name: str | None
    location: str | None
    global_network: bool
    storage_support: bool
    listed: bool
    compliance: tuple[str, ...]
    gpu_types: tuple[str, ...]
    gpu_availability: Mapping[str, bool]

@dataclass(frozen=True, slots=True)
class GpuDiscoverySnapshot:
    fetched_at: datetime
    gpus: tuple[GpuCatalogEntry, ...]
    datacenters: tuple[DatacenterCatalogEntry, ...]
    def gpu_by_id(gpu_type_id) -> GpuCatalogEntry | None
    def datacenter_by_id(datacenter_id) -> DatacenterCatalogEntry | None
    def to_availability_entries(gpu_count=1, cloud_type=None) -> list[tuple]
    def to_availability_snapshot(gpu_count=1, cloud_type=None) -> AvailabilitySnapshot

class GpuDiscoveryService(graphql_client, *, ttl_s=60.0):
    async def refresh() -> GpuDiscoverySnapshot
    async def get_snapshot() -> GpuDiscoverySnapshot
    async def read_credits_balance() -> RunpodCreditsBalance
    def get_gpu(gpu_type_id) -> GpuCatalogEntry | None
    def get_datacenter(datacenter_id) -> DatacenterCatalogEntry | None
    def invalidate() -> None
    async def aclose() -> None
```

**TTL:** Default 60 seconds (`DEFAULT_DISCOVERY_TTL_S`).  Refresh is serialized behind an `asyncio.Lock` so concurrent callers share one GraphQL round-trip.

GPU and datacenter normalization sorts exact provider ids, so provider response
ordering does not change serialized catalogue or replay input.

**Replay substrate:** `GpuDiscoverySnapshot.to_availability_entries()` and `to_availability_snapshot()` flatten the discovered catalog into the same `(datacenter, gpu_name, cloud_type, gpu_count, available)` tuples consumed by `PlanningContext.replay()`.  Callers may capture a snapshot, freeze it, and replay deterministic routing decisions against historical or hypothetical capacity without touching live GraphQL.

### `billing.py`

`RunPodBillingClient` is an async, header-authenticated reader for the official
REST v1 billing-history routes. It implements only `GET` operations:

- `pod_history(..., pod_id=...)` sends both `podId` filtering and `grouping=podId`;
- `endpoint_history(..., endpoint_id=...)` retains endpoint-grouped account history; and
- `network_volume_history(...)` retains the provider's aggregate storage history.

Response JSON uses `parse_float=Decimal`. The model rejects a Python `float`
boundary, negative/non-finite amounts, naive timestamps, malformed envelopes,
and response records outside its strict typed fields. Exceptions contain the
method/path/status only; response bodies and authorization values are not
reflected.

Only pod history has the provider resource identity needed for a possible
workload truth-up. Endpoint history has no job/workload id, and network-volume
history has no volume id. Those two categories therefore remain account reads,
never workload actuals.

### `runpod_market.py`

`RunpodMarketService` is the feature-local product service over GraphQL
discovery/balance, REST v2 POD catalogue, and REST v1 billing. It owns one TTL
cache and one `asyncio.Lock`; cache miss, expiry, and concurrent forced refresh
are single-flight. The three read components refresh concurrently. A failed
component preserves its previous value as `stale`; without a prior value it is
`unavailable`. Public state is `fresh`, `partial`, `stale`, or `unavailable`,
with refresh time, cache expiry, data age, component provenance, safe reason
codes, and cache-hit/force metadata.

GraphQL and REST v2 fields stay nested under separate provenance. Exact GPU ids
may join the two views, but REST memory/pool/CUDA availability is never silently
substituted for GraphQL price, bid, stock, or datacenter fields. GPU,
datacenter, CUDA-version, availability, component, and billing-category output
has deterministic ordering. Money serializes as exact decimal strings.

`RunpodMarketRead.gpu_types_for_fit()` supplies live GraphQL on-demand rates to
the existing model-fit adapter; missing GraphQL rows retain the canonical
unpriced static fallback. `to_availability_snapshot()` supplies the same frozen
capacity values to routing/replay without a second provider call. This is a
read contract only: it does not buy a reservation, set a bid, or launch a Pod.

`provider_actual_cost()` accepts a bounded set of authoritative
workload-to-pod references, reads each exact pod history, rejects empty,
mismatched, duplicate, or out-of-window buckets, and returns COST's
`ProviderActualCostResult`. `reconcile_provider_actual_cost()` delegates the
available or unavailable result to COST's existing transactional, absolute-set
truth-up. Endpoint and network-volume categories remain explicitly unavailable;
account balance is never an actual-cost input.

The registered adapters are:

- REST `GET /v1/runpod/catalogue` in `api/routes/runpod_market.py`;
- MCP handler factory in `mcp/tools/runpod_market.py`;
- CLI renderer in `src/pitwall/cli/runpod_market.py`; and
- injectable `RunpodMarketPanel` in `tui/runpod_market.py`.

They all serialize or render `RunpodMarketRead`. The API and MCP lifespans and
the production TUI own one process-local cache; CLI reads are bounded one-shot
services. Model fit adapts the same snapshot and retains the unpriced fallback.

Migration `0030_lease_workload_billing_identity.sql` persists the exact
admitted workload on a Pod lease. Unique partial indexes prevent a linked
workload or provider resource from being attributed twice. The existing
reconciler builds actual-cost references only from terminal linked rows and
delegates the resulting RunPod adapter read to COST's transactional truth-up.

### `endpoints.py`
```python
def hibernate_endpoint(endpoint_id: str) -> dict[str, Any]
```
Normalizes `endpoint_id`, calls `PATCH /serverless/{id}` with
`{"workers": {"min": 0}}` via the v2 REST helper, and validates a resource response.

### `mounts.py`
```python
POD_VOLUME_MOUNT_PATH = "/workspace"
SERVERLESS_VOLUME_MOUNT_PATH = "/runpod-volume"
POD_PROVIDER_TYPES = frozenset({ProviderType.POD_LEASE})
SERVERLESS_PROVIDER_TYPES = frozenset({ProviderType.SERVERLESS_QUEUE, ProviderType.SERVERLESS_LB, ProviderType.PUBLIC_ENDPOINT})
PROVIDER_TYPE_VOLUME_MOUNT_PATHS: Mapping[ProviderType, str]  # covers all 4 RunPod provider types

def provider_type_volume_mount_path(provider_type: ProviderType | str) -> str
mount_path_for_provider_type = provider_type_volume_mount_path  # alias

class NetworkVolume(BaseModel): id, name, size, data_center_id
class S3Object(BaseModel): key, size, last_modified

class NetworkVolumeClient(api_key, rest_base_url, s3_access_key, s3_secret_key, timeout_s=60.0, transport)
    async create(name, size_gb, dc) -> NetworkVolume
    async get(volume_id) -> NetworkVolume
    async list() -> list[NetworkVolume]
    async update(volume_id, size_gb) -> NetworkVolume
    async delete(volume_id) -> None          # idempotent on 404
    async list_objects(volume_id, dc, prefix="") -> list[S3Object]
    async put_object(volume_id, dc, key, body) -> None
    async get_object(volume_id, dc, key) -> bytes
    async delete_object(volume_id, dc, key) -> None
```
REST operations use `httpx.AsyncClient` against `api.runpod.io/v2/network-volumes`
(configurable), send `dataCenter`, and require the `networkVolumes` list envelope. S3 operations
are unchanged: `boto3` runs through `asyncio.to_thread` against `s3api-{dc}.runpod.io` with
path-style addressing and SigV4 signing. Response parsing accepts v2 `dataCenter` and the former
`dataCenterId` alias for stable Pitwall objects.

---

## 3. RunPod Surfaces

| Surface | Module | Auth | Base URL |
|---|---|---|---|
| Pod CRUD and unified actions | `pods.py` | `Bearer {RUNPOD_API_KEY}` | `https://api.runpod.io/v2` (configurable) |
| Bounded pod create/reset fallback | `pods.py` | `Bearer {RUNPOD_API_KEY}` | `https://rest.runpod.io/v1` (separately configurable) |
| GPU market, datacenters, spot bids, credits | `graphql.py` | `api_key` query + `Bearer {RUNPOD_API_KEY}` | `https://api.runpod.io/graphql` |
| Container registry auth CRUD | `registry.py` | `Bearer {RUNPOD_API_KEY}` | `https://api.runpod.io/v2/registries` |
| Network volume CRUD + S3 | `mounts.py` | REST: `Bearer {RUNPOD_API_KEY}`; S3: `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | REST: `https://api.runpod.io/v2/network-volumes`; S3: `https://s3api-{dc}.runpod.io` |
| GPU catalog + datacenter discovery | `discovery.py` | Delegates to `graphql.py` | n/a (in-process) |
| GPU market, datacenters, spot bids, credits | `graphql.py` | `Bearer {RUNPOD_API_KEY}` | `https://api.runpod.io/graphql` |
| Template management | `templates.py` | `Bearer {RUNPOD_API_KEY}` | `https://api.runpod.io/v2/templates` |
| Serverless queue | `queue.py` | `Bearer {RUNPOD_API_KEY}` | `https://api.runpod.ai/v2` |
| Serverless load-balancer | `lb.py` | `Bearer {RUNPOD_API_KEY}` | `https://{endpoint_id}.api.runpod.ai` |
| Serverless OpenAI-compatible | `serverless.py` | `Bearer {api_key}` | caller-provided, must end with `/openai/v1` |
| Serverless LB embedding | `serverless_lb.py` | optional `Bearer {api_key}` | `{lb_base_url}/embed` |
| Serverless endpoint admin (CRUD) | `serverless.py` | `Bearer {RUNPOD_API_KEY}` | `https://api.runpod.io/v2/serverless` |
| Endpoint admin (hibernate) | `endpoints.py` | same as pods | `https://api.runpod.io/v2/serverless/{id}` |

---

## 4. Public Interfaces

Key callable symbols (RunPod client symbols are re-exported from `pitwall.runpod_client`;
provider plugin symbols are re-exported from `pitwall.providers`):

- **Config:** `WorkloadConfig`, `CANONICAL_GPU_NAMES`
- **Provider plugins:** `Provider`, `ProviderRegistry`, `ProviderOperationContext`, `ProvisionRequest`, `ProvisionResult`, `StatusRequest`, `StatusResult`, `ReconcileRequest`, `ReconcileResult`, `TeardownRequest`, `TeardownResult`, `ResourceStatus`, `RunPodProvider`, `RunPodCredentials`, `create_default_registry`, `get_default_registry`; registry errors: `DuplicateProviderError`, `ProviderNotRegisteredError`, `CredentialValidationError`
- **GPU validation:** `validate_canonical_gpu_name`, `validate_canonical_gpu_names`, `is_canonical_gpu_name`, `canonical_gpu_name_suggestions`, `NonCanonicalGPUNameError`
- **Registry:** `registry_auth_id_from_env`, `image_registry_prefix`, `registry_auth_env_names_for_image_ref`; **CRUD:** `create_container_registry_auth`, `get_container_registry_auth`, `list_container_registry_auths`, `delete_container_registry_auth`, `ContainerRegistryAuth`, `RegistryAuthCreateInput`, `RegistryAuthError`
- **Pods:** `create_pod_with_fallback` (+ sync variant), `wait_for_pod_runtime` (+ sync), `get_pods` (+ sync), `get_pod` (+ sync), `terminate_pod` (+ sync), `terminate_all_with_tag`, `get_pods_by_tag_prefix`; exceptions: `RunPodError`, `NoCapacityError`, `PodStartupTimeout`, `PodVolumeAttachTimeout`, `ProviderAttachHangRecoveryRequested`, `PodStartupFailed`, `RunPodRestError`; `PodProbeResult`
- **Registry:** `registry_auth_id_from_env`, `image_registry_prefix`, `registry_auth_env_names_for_image_ref`
- **Pods:** `create_pod_with_fallback` (+ sync variant), `wait_for_pod_runtime` (+ sync), `get_pods` (+ sync), `get_pod` (+ sync), `terminate_pod` (+ sync), `terminate_all_with_tag`, `get_pods_by_tag_prefix`, `start_pod` (+ sync), `stop_pod` (+ sync), `reset_pod` (+ sync), `restart_pod` (+ sync), `update_pod` (+ sync); exceptions: `RunPodError`, `NoCapacityError`, `PodStartupTimeout`, `PodVolumeAttachTimeout`, `ProviderAttachHangRecoveryRequested`, `PodStartupFailed`, `RunPodRestError`; `PodProbeResult`, `UpdatePodRequest`, `UpdatePodResponse`
- **Queue:** `QueueClient`, `QueueJob`, `QueueHealth`, `QueueCancelResult`, `QueuePurgeResult`, `RUNPOD_API_BASE`, `queue_url`
- **LB:** `LBClient`, `ProbeResult`, `LBResponse`, `lb_endpoint_url`
- **Serverless:** `ServerlessClient`, `ServerlessResponse`; endpoint CRUD: `create_endpoint`, `get_endpoint`, `list_endpoints`, `update_endpoint_scaling`, `delete_endpoint`, `Endpoint`, `EndpointScalingConfig`
- **Embedding LB:** `ServerlessLBClient`, `EmbeddingResponse`
- **GraphQL market/billing:** `RunpodGraphQLClient` (`RunPodGraphQLClient` alias), `RunpodGpuType`, `RunpodDatacenter`, `RunpodGpuAvailability`, `RunpodLowestPrice`, `RunpodBidPrice`, `RunpodBidResumeResult`, `RunpodCreditsBalance`, `RunpodGraphQLError`, `RunpodGraphQLHTTPError`, `RunpodGraphQLResponseError`, `RUNPOD_GRAPHQL_URL`
- **Discovery:** `GpuDiscoveryService`, `GpuDiscoverySnapshot`, `GpuCatalogEntry`, `DatacenterCatalogEntry`, `DEFAULT_DISCOVERY_TTL_S`
- **Billing history:** `RunPodBillingClient`, `RunPodBillingRecord`, `RunPodBillingError`, `BillingCategory`, `BillingBucketSize`
- **RunPod market product read:** `RunpodMarketService`, `RunpodMarketRead`, `RunpodActualCostReference`, `AsyncpgRunpodActualCostReferenceRepository`, `DEFAULT_RUNPOD_MARKET_CACHE_TTL_S`
- **Billing read / reconciliation:** `BillingSnapshot`, `BudgetReconciliation`, `BudgetGateLike`, `read_billing_snapshot`, `reconcile_with_budget` (from `pitwall.cost.billing_read`)
- **Templates:** `ensure_template`, `get_image_ref_from_env`, `get_registry_auth_id_from_env`, `image_sha`, `normalize_template_name`, `template_display_name`, `template_suffix`, `TEMPLATE_NAME`
- **Templates:** `ensure_template`, `get_template`, `update_template`, `delete_template`, `list_account_templates`, `list_hub_templates`, `get_hub_template`, `get_image_ref_from_env`, `get_registry_auth_id_from_env`, `image_sha`, `normalize_template_name`, `template_display_name`, `template_suffix`, `TEMPLATE_NAME`, `Template`, `HubTemplate`, `TemplateEnvVar`, `TemplateNotFoundError`, `TemplateDeleteError`
- **Account resource control plane:** `RunPodControlPlaneService`, `StrictRunPodBackend`, resource-specific mutation request models, sanitized resource models, `MutationResult`, and `RunPodControlPlaneError`
- **Endpoints:** `hibernate_endpoint`
- **Availability:** `AvailabilityCache`, `get_global_availability_cache`, `reset_global_availability_cache`
- **Mounts:** `PROVIDER_TYPE_VOLUME_MOUNT_PATHS`, `provider_type_volume_mount_path`, `POD_VOLUME_MOUNT_PATH`, `SERVERLESS_VOLUME_MOUNT_PATH`
- **Network volumes:** `NetworkVolumeClient`, `NetworkVolume`, `S3Object`; methods: `create`, `get`, `list`, `update`, `delete`, `list_objects`, `put_object`, `get_object`, `delete_object`

---

## 5. Configuration

| Env var | Module(s) | Default | Description |
|---|---|---|---|
| `RUNPOD_API_KEY` | all | *(required)* | Bearer token for all RunPod API calls |
| `RUNPOD_REST_API_URL` | pods, templates, endpoints, volumes, registry | `https://api.runpod.io/v2` | Primary RunPod REST v2 control-plane base URL |
| `RUNPOD_REST_V1_API_URL` | bounded pod create/reset fallback; billing history reads | `https://rest.runpod.io/v1` | Separate legacy base; billing uses GET only |
| `PITWALL_RUNPOD_MARKET_CACHE_TTL_S` | RunPod market product service | `300` | Non-negative in-process catalogue/balance TTL; `0` refreshes every non-concurrent read |
| `PITWALL_RUNPOD_CAPACITY_ERROR_SUBSTRINGS` | pods | `"no longer any instances available"`, `"resourcesunavailable"`, `"insufficient"`, etc. | Capacity error substring list |
| `PITWALL_VOLUME_ATTACH_TIMEOUT_S` | pods | `300.0` | Volume attach hang timeout (seconds) |
| `RUNPOD_REGISTRY_AUTH_ID` | registry | *(none)* | Legacy GHCR-compatible registry auth ID |
| `RUNPOD_REGISTRY_AUTH_ID_GHCR` | registry | *(none)* | GHCR registry auth ID |
| `RUNPOD_REGISTRY_AUTH_ID_GITLAB` | registry | *(none)* | GitLab registry auth ID |
| `RUNPOD_REGISTRY_AUTH_ID_DOCKER_HUB` | registry | *(none)* | Docker Hub registry auth ID |
| `PITWALL_CLOUD_WORKER_IMAGE` | templates | *(required)* | Image ref for cloud worker template |
| `PITWALL_EMBEDDING_VIA_PITWALL` | serverless_lb | *(none)* | Route embeddings through Pitwall instead of direct |
| `RUNPOD_S3_ACCESS_KEY` | mounts (S3) | *(falls back to `AWS_ACCESS_KEY_ID`)* | S3 API key access key for network volume file access |
| `RUNPOD_S3_SECRET_KEY` | mounts (S3) | *(falls back to `AWS_SECRET_ACCESS_KEY`)* | S3 API key secret for network volume file access |
| `PITWALL_BASE_URL` | serverless_lb | *(none)* | Pitwall base URL for embedding routing |

---

## 6. Failure Modes & Error Types

**Pod lifecycle:** `RunPodError` (base, treated as 5xx); `NoCapacityError` (all GPU/cloud combos exhausted → triggers provider fallback); `ProviderFallbackRequested` (pre-readiness cost/gpu gate → skip provider); `PodStartupTimeout` (never reached runtime within `startup_timeout_s`); `PodVolumeAttachTimeout` (zero-uptime volume hang → pod deleted, `ProviderAttachHangRecoveryRequested` raised); `PodStartupFailed` (terminal failed state reached); `RunPodRestError` (HTTP 4xx/5xx with method/path/status/body).

**GraphQL market/billing:** `RunpodGraphQLError` for `errors` envelopes, preserving the original error objects; `RunpodGraphQLHTTPError` for non-2xx endpoint responses; `RunpodGraphQLResponseError` for invalid JSON or missing `data` shapes. All are `RunPodError` subclasses so service code can route them with existing RunPod failure handling.
**REST billing history:** `RunPodBillingError` reports only a bounded operation,
path, and status/shape category. The product service maps provider failures to
safe `not_configured`, `timeout`, `authentication_failed`,
`invalid_provider_response`, or `provider_unavailable` component metadata.
**Container registry auth CRUD:** `RegistryAuthError` (base); `create_container_registry_auth` raises on HTTP 4xx/5xx; `get_container_registry_auth` returns `None` on 404; `delete_container_registry_auth` is idempotent (404 swallowed); `list_container_registry_auths` raises on HTTP 5xx.

**Serverless queue:** `httpx.HTTPStatusError` (429 on submission → bounded retry with `Retry-After`; other 5xx and 4xx → raised immediately).

**GPU validation:** `NonCanonicalGPUNameError` raised at `WorkloadConfig` construction time; blocks launch.

**Template errors:** `RuntimeError` — `PITWALL_CLOUD_WORKER_IMAGE` env var missing or RunPod returns unexpected `create_template` response shape. `TemplateNotFoundError` — template ID does not exist (get/update/delete); `get_template` raises it only for HTTP 404, and an empty or non-object 200 body raises `RunPodError`, as does a non-object 200 from `get_pod_strict`, so a provider glitch never reads as a gone resource. `TemplateDeleteError` — template could not be deleted.

**Network volumes:** `RunPodRestError` on REST 4xx/5xx (same shape as pods: method/path/status/body). `RunPodError` when boto3 is missing for S3 operations. Non-dict/list REST responses raise `RunPodError` with the offending type name. Delete is idempotent: 404 is swallowed (`pitwall.runpod_client.mounts`).

**Account resource control plane:** `RunPodControlPlaneError` provides stable codes for not found,
name conflict, grow-only refusal, missing credential references, timeout, provider failure,
malformed responses, unavailable audit storage, and registry replacement partial failure. Provider
messages are length-bounded and redacted; cancellation propagates. Preview performs no provider or
audit write. A live mutation refuses to start without an audit store; a post-provider audit failure
is reported as changed so transports can refresh state safely.

**Capacity detection:** `is_capacity_error(exc)` matches exception body against `PITWALL_RUNPOD_CAPACITY_ERROR_SUBSTRINGS`. Matched errors trigger the next GPU/cloud fallback. Unmatched non-2xx responses are logged as warnings and re-raised immediately (`pods.py`).

---

## 7. Testing

| File | Coverage |
|---|---|
| `tests/runpod_client/test_pods.py` | Pod create, readiness wait, terminate, capacity substring matching, GPU/cloud fallback, probe ordering; start/stop/reset/restart/update lifecycle (happy path, error envelope, missing fields) |
| `tests/runpod_client/test_queue.py` | QueueClient (runsync, run, status, health, cancel, purge-queue), submission replays only connect failures and 429 |
| `tests/runpod_client/test_lb.py` | LBClient (post, get, ping, probe), 524 distinction, `ProbeResult` structure |
| `tests/runpod_client/test_serverless.py` | ServerlessClient chat_completion, retry on 429/5xx |
| `tests/runpod_client/test_serverless_endpoints.py` | `EndpointScalingConfig` validation, `to_request_json()` round-trip, `create_endpoint`, `get_endpoint`, `list_endpoints`, `update_endpoint_scaling`, `delete_endpoint` (happy path, REST errors, edge cases) |
| `tests/runpod_client/test_availability.py` | AvailabilityCache TTL, thread-safety, `bulk_set_available`, `sweep_expired` |
| `tests/runpod_client/test_registry.py` | `image_registry_prefix`, `registry_auth_env_names_for_image_ref`, `registry_auth_id_from_env`; CRUD: `create_container_registry_auth` (happy path, error envelope, REST URL, missing key), `get_container_registry_auth` (found, 404→None, 5xx, missing key), `list_container_registry_auths` (non-empty, empty, unexpected shape, 5xx, missing key), `delete_container_registry_auth` (happy path, idempotent 404, 5xx, missing key) |
| `tests/runpod_client/test_templates.py` | `ensure_template` (full-config cache hit, miss+create, duplicate name handling, secret-safe metadata/logging), `image_sha`, `config_sha`, `template_display_name` |
| `tests/runpod_client/test_registry.py` | `image_registry_prefix`, `registry_auth_env_names_for_image_ref`, `registry_auth_id_from_env`, env fallback priority |
| `tests/runpod_client/test_templates.py` | `ensure_template` (cache hit, miss+create, duplicate name handling), `get_template`, `update_template`, `delete_template`, `list_hub_templates`, `get_hub_template`, `image_sha`, `template_display_name`, `Template`/`HubTemplate` model validation |
| `tests/runpod_client/test_endpoints.py` | `hibernate_endpoint` happy path, REST error propagation |
| `tests/runpod_client/test_graphql.py` | GraphQL GPU types, datacenters, spot bid read/set, credits balance, error envelopes, Decimal fidelity, settings auth plumbing |
| `tests/runpod_client/test_mounts.py` | `NetworkVolumeClient` REST CRUD (create/get/list/update/delete), error-envelope handling, idempotent delete, S3 operations (list/put/get/delete objects), credential resolution, transport injection |
| `tests/runpod_client/test_discovery.py` | `GpuDiscoveryService` refresh, TTL, cache hit/miss, `GpuCatalogEntry`/`DatacenterCatalogEntry` fields, `to_availability_snapshot`, error propagation, concurrency |
| `tests/runpod_client/test_billing.py` | Official route/filter shapes, header-only auth, Decimal decode, category separation, malformed/error redaction |
| `tests/test_runpod_market.py` | deterministic composition, cache/force/expiry/concurrency, partial/stale/unavailable states, fit/routing reuse, exact pod actuals, unavailable categories, idempotent COST replay |
| `tests/test_runpod_market_surfaces.py` | isolated REST/MCP/CLI shared JSON and forced-refresh delegation |
| `tests/tui/test_runpod_market_panel.py` | populated, empty, stale, unavailable, error/redaction, and force-refresh panel states |
| `tests/property/test_discovery_properties.py` | Determinism, entry-shape invariants, `_normalize_gpu`/`_normalize_datacenter` round-trips |
| `tests/providers/test_registry.py` | Provider protocol shape, registry registration/lookup, safe credential-schema validation, default RunPod registration, tagged pricing delegation |
| `tests/providers/test_runpod_adapter.py` | RunPod provider adapter delegation to existing launch/teardown/status/reconcile services |
| `tests/property/test_provider_registry_properties.py` | Credential URL userinfo rejection across generated secret strings |
| `tests/test_runpod_gpu_validator.py` | GPU canonicalization, WorkloadConfig validation, shorthand rejection with suggestions |
| `tests/test_runpod_mount_paths.py` | Mount path constants, provider-type mapping, enum/string accept |
| `tests/db/test_runpod_templates_migration.py` | PostgreSQL `runpod_templates` upgrade/backfill, unique constraint on `(name, config_sha)`, defaults |
| `tests/db/test_runpod_templates_config_sha_migration.py` | Hermetic 0023 ordering and replacement-key assertions |
| `tests/chaos/test_volume_attach_hang.py` | PodVolumeAttachTimeout behavior, zero-uptime detection |
| `tests/chaos/test_idempotent_terminate.py` | terminate_pod_sync idempotency on 404 |
| `tests/chaos/test_serverless_5xx.py` | ServerlessClient retry on 5xx |
| `tests/chaos/test_serverless_429.py` | ServerlessClient retry on 429 |
| `tests/leases/test_launch.py` | Pods + templates in lease launch flow |
| `tests/embedding/test_client_auth.py` | ServerlessLBClient embed flow, Pitwall mode, 30 MB limit |
| `tests/api/test_e2e_lease_lifecycle.py` | Lease create → get_pods → terminate end-to-end |
| `tests/cost/test_billing_read.py` | `read_billing_snapshot`, `reconcile_with_budget`, fake GraphQL transport, Decimal fidelity, error propagation, budget-gate protocol |
| `tests/runpod_control_plane/test_service.py` | Complete shared-service operation matrix, nested endpoint controls, audit/redaction, preview zero-write, grow-only volumes, idempotent delete/terminate, timeout/cancellation, malformed responses, and registry replacement partial failure |
| `tests/runpod_control_plane/test_api.py` | Isolated 29-operation REST router, explicit mutation intent/idempotency, strict v2 input, path/body identity, and stable safe errors |
| `tests/mcp/test_runpod_resources.py` | Isolated 29-tool manifest, thin shared-service delegation, read-only Hub inventory, and credential-safe serialization |
| `tests/cli/test_runpod_resources.py` | Cohesive 29-leaf parser, JSON output, dry-run, exact confirmation, nested endpoint controls, and safe validation errors |
| `tests/fakes/runpod.py` | Shared fakes: `RunPodRestFake`, `RunPodServerlessFake`, `RunPodQueueFake`, `RunPodLBFake`, `RunPodTemplateFake`, `RunPodBillingFake` |

---

## 8. Dependencies

**Internal imports:**

| Module | From | Used for |
|---|---|---|
| `pods.py` | `pitwall.runpod_client.registry` | `registry_auth_id_from_env` |
| `pods.py` | `pitwall.runpod_client.workloads` | `WorkloadConfig` |
| `graphql.py` | `pitwall.config` | `PitwallSettings`, `load_settings_from_env` |
| `graphql.py` | `pitwall.runpod_client.pods` | `RunPodError` base class |
| `providers/runpod.py` | `pitwall.api.leases.launch` | Existing RunPod pod-lease provision flow |
| `providers/runpod.py` | `pitwall.api.leases.teardown` | Existing RunPod single-lease teardown flow |
| `providers/runpod.py` | `pitwall.cost.estimator` | Tagged pricing union parsing |
| `providers/runpod.py` | `pitwall.runpod_client.pods` | Pod status and reconcile reads |
| `templates.py` | `pitwall.runpod_client.pods` | `_sdk()` lazy SDK import |
| `templates.py` | `pitwall.runpod_client.registry` | `registry_auth_id_from_env` |
| `workloads.py` | `pitwall.runpod_client.gpu` | `validate_canonical_gpu_names` |
| `mounts.py` | `pitwall.core.enums` | `ProviderType` enum |
| `mounts.py` | `pitwall.runpod_client.pods` | `RunPodError`, `RunPodRestError` |
| `lb.py`, `queue.py`, `serverless.py` | `pitwall.rate_limits.retry_after` | `DEFAULT_MAX_RETRY_AFTER_DELAY_S`, `parse_retry_after` |
| `serverless.py` | `pitwall.runpod_client.pods` | `RunPodError`, `RunPodRestError` |
| `serverless_lb.py` | `pitwall.config` | `load_settings_from_env` |
| `endpoints.py` | `pitwall.runpod_client.pods` | `RunPodError`, `_rest_request` |
| `discovery.py` | `pitwall.runpod_client.graphql` | `RunpodGraphQLClient`, response models |
| `discovery.py` | `pitwall.routing.context` | `AvailabilitySnapshot`, `AvailabilityEntryInput` |
| `__init__.py` | `pitwall.core.enums` | `ProviderType` |

**External:**

| Library | Used in | Purpose |
|---|---|---|
| `httpx` | pods, graphql, queue, lb, serverless, serverless_lb | HTTP client (sync in pods, async elsewhere) |
| `pydantic` | workloads, graphql, queue, lb, serverless, serverless_lb | `BaseModel` response envelopes |
| `runpod` (SDK) | pods (`_sdk()`), templates | Template create + GraphQL listing; lazy import |
| `boto3` | mounts (S3) | S3-compatible file access for network volumes; lazy import |
| `asyncpg` | templates | PostgreSQL pool for template cache |
| `asyncio` | pods, queue, lb, serverless, templates | `asyncio.to_thread` for sync wrappers; `asyncio.sleep` |
| `threading` | availability | `RLock` for thread-safe cache |
| `datetime` | queue, lb, serverless | `utc_now` clock for `Retry-After` arithmetic |
| `time` | pods, availability, lb | `monotonic` for TTL and latency measurement |

---

## Addendum: Bounded Network-Volume Files and Pod Logs

`pitwall.runpod_files.VolumeFileService` is the shared RP-04 operator layer for
one bounded network-volume object page, upload, object-range read, safe local
download, delete, and the existing provider-supported pod-log endpoint. It is
used by feature-local REST, MCP, CLI, and Operations-panel adapters; transport
framing may differ, but the `VolumeFileResult` metadata, progress ordering,
limits, SHA-256 outcomes, truncation state, and safe error codes do not.

`NetworkVolumeClient.list_objects_page()` makes one `ListObjectsV2` request and
returns `S3ObjectPage`; `get_object_range()` sends an explicit S3 `Range` and
reads at most one extra byte to reject an ignored range. Operator construction
passes explicit S3 credentials with `resolve_env_credentials=False`, preserving
the legacy client fallback only for its existing callers. `BoundedPodLogClient`
uses only `GET pods/{pod_id}/logs`, has explicit line/byte/time limits, and
redacts control-plane credentials before returning raw bounded text for service
parsing.

The service accepts only relative local paths below a non-symlink root. It
rejects traversal, symlinks, special files, and unsafe parents, holds no-follow
directory descriptors during download publication, and removes private partial
files on cancellation, failure, or checksum mismatch. Mutations support zero-
write dry-run and explicit overwrite/delete confirmation. The feature does not
provide SSH, remote execution, private-key storage, `croc`, an undocumented
endpoint, or unbounded MCP streaming. See
[`docs/operator/runpod-volume-files.md`](../operator/runpod-volume-files.md)
for operator framing, limits, credentials, and the registered surface matrix.

Every object/log request is inspected before S3, pod-log, or local-destination I/O. Upload scans
decoded content rather than its base64 transport frame; UTF-8 content is inspected normally,
while opaque binary and content beyond the scanner bound fail closed. Object identities and file
bytes are never silently rewritten, so redact decisions block. Provider-returned object bytes and
log text are outside GOV-04 response-scanning scope; range/size/checksum bounds and log credential
redaction remain authoritative.

Consequently, upload's effective bound is the smaller of `VolumeFileLimits.max_transfer_bytes`
and the pre-spend limit (256 KiB for the whole structured request by default, leaving slightly less
for file content). REST/MCP add a 128 KiB frame limit. Preview and live upload use the same decision;
both accept inspectable UTF-8 content only and fail closed for opaque binary, oversize, secret, or
PII content. RP-04 does not claim arbitrary binary/model-weight upload support.

`PostgresVolumeFileMutationJournal` holds one session advisory lock per
idempotency key across an upload/delete provider call so a concurrent equal-key
request waits and replays the completed result instead of issuing a second
mutation. Its read/start and completion writes use separate short database
transactions; provider I/O is outside every transaction. A crash releases the
session lock. A different request hash conflicts before provider mutation.
Create-only PutObject uses `If-None-Match: *` at the provider boundary, so
distinct idempotency keys and external writers cannot win a lookup/write race
and then be overwritten. Its ambiguous exact retry accepts an existing object
only after bounded length and SHA-256 verification; different or unverifiable
bytes end in a durable precondition conflict. A `started` overwrite or delete
is not automatically repeated: because another actor may have changed or
recreated the object after the first attempt, it returns
`volume_file_mutation_outcome_ambiguous` with zero further provider mutations
and requires inspection.
Local download is non-replayable: audit follows atomic local publication, and
post-write audit failure reports `changed: true`.
Audit data contains no object bytes, credentials, or local path.

The Operations panel renders shared progress and a typed confirmation with
provider, target, effect, ceiling, and irreversible consequence. Overwrite is
an explicit default-off choice preserved from preview to apply. Empty,
unavailable, and stale-refresh are widget-derived presentation states. An
in-flight mutation cancellation warns that the write may have completed and
requires provider/local-state plus audit inspection; it never claims a
rollback.

---

## Addendum: Resumable RunPod onboarding

`pitwall.onboarding` is the shared RP-05 plan/apply/status/resume/rollback-
guidance boundary. Its Pydantic request, command, step, evidence, event, and
result models (`pitwall.onboarding`, and
`pitwall.onboarding`) are the only onboarding contract consumed by REST, MCP, CLI, and
TUI adapters. Plan IDs are deterministic hashes of the secret-free desired
topology and guardrail-redacted probe payload. Apply and resume reject a plan
ID that does not match the current request (`pitwall.onboarding`).

The implementation is one explicit dependency sequence: config/reference
validation, GPU/datacenter discovery, optional registry auth, optional volume,
optional template, optional endpoint or pod-request preview, capability,
provider, production dry run, and completion evidence. It delegates resource
writes to `RunPodControlPlaneService`, provider/capability persistence to their
existing repositories, resolution to `resolve_capability`, cost to
`quote_cost`, and payload inspection to the pre-spend guardrail. Pod-lease
onboarding invokes only `intent="preview"`; optional inference is description
only and remains behind separate `LIVE-RP-01` authorization.

Resumption is derived from live resource inventory, broker rows, deterministic
names/IDs, and bounded `config_audit` events. No workflow engine, onboarding
table, or migration is introduced. A bounded PostgreSQL advisory lock
serializes apply/resume across processes, including different plans that could
name the same resources. Historical completion evidence is valid only while
the current resources and enabled broker rows still match. Known plan-created
resources compensate in reverse dependency order. Data-bearing volumes are retained; provider and
capability rows are disabled rather than deleted; provider dependencies are
retained after registration so an exact resume remains coherent. See
[RunPod onboarding](../operator/runpod-onboarding.md) for request examples and
operator recovery guidance (`pitwall.onboarding`).

Coverage is split across `tests/onboarding/test_service.py` (topologies,
zero-write plan, exact confirmation, idempotent second apply, concurrency,
failure/resume/compensation/cancellation, production dry-run evidence),
`tests/onboarding/test_surfaces.py` (shared-model REST/MCP/CLI adapters),
`tests/tui/test_onboarding_panel.py` (typed confirmation and cancellation), and
`tests/integration/test_onboarding_state.py` (existing Postgres registry and
audit persistence). No test makes a live RunPod call or performs paid work.
