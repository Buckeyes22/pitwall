# Provider Plugins — SDLC Design Document

## 1. Purpose & Scope

`pitwall.providers` is the static adapter seam between persisted fulfillment records and
provider-specific compute or inference calls. It is intentionally not a discovery or installation
framework.

The current built-in adapters are:

- `runpod` — delegates to existing RunPod launch/status/reconcile/teardown services.
- `vast` — talks directly to Vast.ai REST endpoints and converts hourly price fields into `PerSecondPricing`.
- `together` — talks to Together's OpenAI-compatible inference API and uses `PerTokenPricing`.
- `lambda_cloud` — talks directly to Lambda Cloud REST endpoints and uses flat `PerVmSecondPricing`.
- `openai_gateway` — talks to any OpenAI-compatible free/cheap gateway with optional Bearer auth; keyless pools need no env var.
- `model_studio` — talks to Alibaba Cloud Model Studio through its OpenAI-compatible endpoint, with pricing derived from a committed shared catalog; credential reference `MODEL_STUDIO_API_KEY`.

Adapters use header-based authentication and never place secrets in URLs. Registration here does
not by itself promote a provider to a supported live product surface; the capability and support
matrices remain evidence-based.

`create_default_registry()` registers `runpod`, `vast`, `together`, `lambda_cloud`,
`openai_gateway`, and `model_studio` in that deterministic order. `ProviderType` remains the
compatibility execution-shape vocabulary used by existing RunPod routes (now including the
`model_studio` provider type). The separate persisted `adapter_id` selects runtime behavior.

---

## 2. Contracts and capability lookup

Every adapter implements the shared metadata contract. Compute and inference are separate narrow
protocols, so an inference-only adapter does not carry lifecycle stubs:

```python
class ProviderAdapter(Protocol):
    id: str
    name: str
    credential_schema: type[pydantic.BaseModel]
    capabilities: frozenset[ProviderCapability]

    def pricing_model(capability, provider_record) -> TaggedPricingModel: ...


class ComputeProvider(ProviderAdapter, Protocol):
    async def provision(ProvisionRequest) -> ProvisionResult: ...
    async def status(StatusRequest) -> StatusResult: ...
    async def reconcile(ReconcileRequest) -> ReconcileResult: ...
    async def teardown(TeardownRequest) -> TeardownResult: ...


class InferenceProvider(ProviderAdapter, Protocol):
    async def infer(InferenceRequest) -> InferenceResult: ...


class AsyncInferenceProvider(ProviderAdapter, Protocol):
    async def submit(AsyncInferenceRequest) -> AsyncInferenceSubmission: ...


class AsyncInferenceStatusProvider(ProviderAdapter, Protocol):
    async def job_status(AsyncInferenceStatusRequest) -> AsyncInferenceStatusResult: ...


class AsyncInferenceCancelProvider(ProviderAdapter, Protocol):
    async def cancel_job(AsyncInferenceCancelRequest) -> AsyncInferenceCancelResult: ...
```

The current capability declarations are:

| Adapter | Compute | Sync inference | Async submit/status/cancel | Availability | Actual cost |
|---|---:|---:|---:|---:|---:|
| `runpod` | yes | yes | yes | no (market service) | exact Pod billing only |
| `vast` | yes | no | no | yes | no |
| `together` | no | yes | no | yes | no |
| `lambda_cloud` | yes | no | no | yes | no |
| `openai_gateway` | no | yes | no | yes (catalog evidence, no egress) | no |
| `model_studio` | no | yes | no | yes (bounded `/models` read) | no |

`ProviderRegistry.lookup_compute()`, `lookup_inference()`,
`lookup_async_inference()`, `lookup_async_status()`, `lookup_async_cancel()`, `supports()`, and
`ids_for_capability()` reject or exclude unsupported operations without `NotImplementedError`
control flow. Registration validates every declared compute, sync inference, async submit, async
status, async cancellation, availability, and actual-cost capability against its own structural
contract. It also rejects duplicate/invalid ids, invalid credential schemas, and serialized
credential values. RunPod actual-cost support requires an exact persisted workload-to-Pod mapping
and bounded authoritative Pod billing; endpoint, network-volume, aggregate, empty, and lagging
billing results remain unavailable.

### The Model Studio adapter

`ModelStudioProvider` registers under provider type `model_studio` and adapter id `model_studio`
with capabilities `sync_inference` and `availability`. The credential reference is the
environment-variable name `MODEL_STUDIO_API_KEY` (Token Plan `sk-sp-` keys and pay-as-you-go
`sk-` keys both resolve through it); `check_key` refuses a key/plan pairing that Model Studio
would silently bill as pay-as-you-go. Sync inference streams from the plan- or region-derived
OpenAI-compatible base URL; availability performs one bounded read of the OpenAI-compatible `GET …/compatible-mode/v1/models` list (the Token Plan host answers the native `/api/v1/models` with 404) and
reports the configured model. Non-interactive Token Plan use is refused unless automation was
accepted (`model_studio.automation: "accept"` or `MODEL_STUDIO_TOKEN_PLAN_AUTOMATION=accept`).

Both this adapter and Agent Routing read one committed catalog:
`src/pitwall/providers/model_studio/catalog.json` is the only copy; there is no second copy to
keep in step. Provider settings,
base URLs, per-token pricing (cached-input and context tiers), and 429/401/403/404
classifications are derived from that catalog, never duplicated by hand.

## 3. Credentials and persisted identity

`pitwall.providers.adapter_id` is one of the six static adapter ids. `credential_ref` is an
environment-variable name such as `RUNPOD_API_KEY`; it is safe to serialize in REST, MCP, CLI,
TUI, GitOps, resolver, and audit output. Credential values are not persisted. A caller passes a
`CredentialReference`, and `resolve_adapter_credentials()` reads and validates the value only
inside the selected adapter operation. Operation request reprs omit the credential input, and
resolution errors report only adapter id, reference name, and invalid field names.

Provider configuration may contain reference keys such as `api_key_env`, but raw `api_key`,
`apiKey`, `clientSecret`, `token`, `password`, authorization, access-key, or secret values are
rejected recursively before repository writes. Credential-key matching normalizes snake, kebab,
dotted, spaced, and camel-case keys at every object/array nesting level. Invalid REST/GitOps inputs
are redacted before validation details are rendered.
An unset or empty referenced environment variable fails before provider HTTP with a safe
`CredentialResolutionError`; the error never contains the environment value. Operators rotate a
credential by updating the referenced environment variable and restarting the owning service,
without changing the persisted provider record.

## 4. Provider-neutral external identifiers

Migration `0028_provider_neutral_runtime.sql` adds:

- `providers.adapter_id` and `providers.credential_ref`;
- `leases.external_resource_id`; and
- `workloads.external_job_id`.

Existing identifiers are backfilled exactly from `runpod_pod_id` and `runpod_job_id`. Legacy-only
rows remain readable because domain/repository mappings fill the generic value from the RunPod
field. If generic and compatibility values are both present they must match. New RunPod writes
populate both fields. New Vast and Lambda writes populate only `external_resource_id`; they no
longer misuse `runpod_pod_id`. The RunPod columns remain in schemas and payloads.

The same migration derives `credential_ref` before recursively scrubbing legacy provider config.
Raw credential keys and invalid reference values are removed from `providers.config`, retaining
safe environment references and non-secret config. Existing provider audit snapshots keep their
shape but replace credential values with `[REDACTED]`. Runtime provider and audit models repeat
redaction at read/serialization boundaries as defense in depth.

Migration 0028 is append-only and has no destructive downgrade. Application rollback keeps the
new columns in place: previous RunPod code can continue reading/writing the retained compatibility
columns, and upgraded code continues to normalize legacy-only rows. Do not drop the generic
columns as a rollback action.

## 5. Extension rule

Adding an adapter requires code review that adds its enum id, credential model, truthful capability
set, one or both narrow contracts, registry registration, and hermetic contract/transport tests.
Discovery remains a literal built-in registration list: no entry points, SDK/plugin installer,
third-party loading, or dynamic imports are part of this seam.

---

## 6. Lambda Cloud Adapter

`LambdaCloudProvider` is registered under id `lambda_cloud`. It uses `LambdaCloudCredentials`, which requires `api_key: SecretStr` and accepts an optional `lambda_api_url` override defaulting to `https://cloud.lambda.ai/api/v1`. The URL validator requires an absolute HTTP(S) URL with no userinfo, query string, or fragment. Requests send the API key only as `Authorization: Bearer ...`.

Pricing is `PerVmSecondPricing`. Provider config should use:

```json
{
  "cost": {
    "kind": "per_vm_second",
    "rate_per_second": "0.00016"
  }
}
```

The adapter also accepts untagged `rate_per_second`, `per_vm_second`, `price_per_second`, or `price_usd_per_second` cost fields and converts hourly aliases (`price_per_hour`, `rate_per_hour`, `price_usd_per_hour`) into a VM-second rate. Any other tagged variant is rejected for Lambda Cloud.

Provision builds a launch body from `provider.config.launch` plus request payload overrides from `lambda_launch`, `instance`, `launch`, or whitelisted top-level launch fields. Required launch fields are `region_name`, `instance_type_name`, and non-empty `ssh_key_names`; `provider.region` fills `region_name` when omitted. If `name` is omitted, Pitwall generates `pitwall-{provider_id}-{request_id-or-random}`.

Network operations:

- `POST /instance-operations/launch` launches a VM and returns the first `data.instance_ids` value as `external_id`.
- `GET /instances/{id}` maps Lambda statuses to provider-neutral `ResourceStatus`.
- `GET /instances` reconciles the account-visible VM list when no explicit ids are supplied.
- `POST /instance-operations/terminate` tears down one VM with `{"instance_ids": [external_id]}`.

Lambda Cloud stores new VM ids in `external_resource_id` and leaves `runpod_pod_id` null. Reads
retain fallback compatibility for rows created before migration 0028.

---

## 7. Status & Reconcile

Lambda Cloud status mapping:

| Lambda status | Pitwall status |
|---|---|
| `booting`, `creating`, `launching`, `pending`, `provisioning`, `starting` | `provisioning` |
| `active`, `ready`, `running` | `running` |
| `terminating`, `terminated`, `deleted` | `terminated` |
| `preempted`, `unhealthy`, `failed`, `error` | `failed` |
| anything else | `unknown` |

Reconcile marks failed provider resources as persisted lease state `failed` when a database pool is available. `preempted` resources get `raw["pitwall_preempted"] = true` and `raw["pitwall_safe_state"] = "failed"` so operators can distinguish provider preemption from ordinary health failure.

---

## 8. Testing

Lambda Cloud tests are hermetic and use `httpx.MockTransport`; they do not require live network or database access. Coverage includes registry registration, credential URL safety, per-VM-second pricing, launch body construction, header auth, dry run, status mapping, missing-resource handling, reconcile failure convergence, teardown, and a property test for flat VM-second pricing invariants.

---

## 9. Provider declarations

Every adapter declares its own behaviour in a `pitwall.providers.interface:ProviderDeclaration`
(`adapter.declaration`), so shared layers never branch on provider type. The registry validates each
declaration on registration and refuses two adapters that claim the same provider type
(`pitwall.providers.registry`). Shared code reads declarations through
`ProviderRegistry.declaration_for_adapter`, `declaration_for_type`, and `declarations`, and through
the module helpers `declaration_for_provider`, `declarations_for_provider`, and
`declaration_for_provider_type`.

A declaration covers:

- **Ownership**: `provider_types` (the `providers.provider_type` values the adapter owns),
  `dedicated_provider_type` (a provider of an owned type must name this adapter; seed enforces it), and
  `default_for_untyped` (rows and requests with no provider type are this adapter's).
- **OpenAI proxy**: `openai_compatible`, `openai_proxy_types`, `derive_openai_base_url`,
  `proxy_skip_reason`, `proxy_rewrite_body`, and `proxy_outbound_headers`. Routing and the OpenAI
  fallback executor read these instead of testing provider types.
- **Lockout**: `lockout_model_paths`, the config key paths whose value is the model id a
  (provider, model) lockout is keyed on.
- **Config schema**: `openai_url_types`, `lb_url_types`, `self_hosted_types`, `openai_base_url`,
  `lb_base_url`, `validate_endpoint`, and `validate_url`.
- **Seed rows**: `requires_gpu_class` and `seed_config`.
- **Reconcile**: `quota_tick`, `health_probe_types`, `probe_endpoint`, and `poll_job_status`, which the
  reconciler calls for the rows an adapter owns.

Adding a provider means writing one adapter and its declaration; no shared module gains a branch for
it. The earlier drift-detection and provider-feasibility modules were removed; provider state is
compared through the reconciler's probes and the read model below.

---

## 10. Provider-operations read model

`ProviderOperationsService` is the one typed read model for safe descriptors,
bounded availability, and persisted or explicitly probed health. It is consumed
by the feature-local REST router, MCP tool module, CLI group, and Providers TUI
source; these adapters do not independently resolve credentials, invoke a
provider client, calculate pricing, or serialize a second response model.

Descriptor reads are persisted-only. Availability and `health(probe=True)` make
one explicit bounded read through the adapter’s declared `availability`
capability. Missing credentials fail before HTTP with the stable
`credential_unavailable` code. Provider exceptions are collapsed to stable safe
error codes; raw provider response bodies and credential values never cross a
surface boundary. Availability prices are serialized as Decimal strings, so the
same value reaches REST, MCP, CLI JSON, and TUI source state without float
rounding.

The Providers TUI keeps these reads explicit: refresh loads persisted descriptors, `g` requests one
health probe, and `a` requests one availability/pricing page capped at 25 items through the same
service. Availability presentation has explicit loading, populated, empty, stale, and unavailable
states. A service `unavailable`/`error` refresh retains the prior successful snapshot as stale; a
first failure renders a safe generic unavailable state (`pitwall.tui.providers`).

The serialized integration points are intentionally separate from the feature
module: API application route inclusion, MCP registry entries, top-level CLI
dispatch, default TUI source wiring, route inventory, OpenAPI snapshot, and
global DNS egress policy. See
[`docs/operator/current-providers.md`](../operator/current-providers.md) for
the current-provider contract, limits, and live-operation boundary.
