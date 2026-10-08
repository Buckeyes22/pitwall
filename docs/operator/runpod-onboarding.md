# RunPod onboarding

Pitwall can plan, apply, inspect, resume, and explain rollback for one complete
RunPod topology. The same typed request and result drive REST, MCP, CLI, and the
TUI. Planning is the default and never writes to RunPod or PostgreSQL.
The canonical request/command/result contracts are defined in
`pitwall.onboarding:RunPodOnboardingRequest`, `OnboardingCommand`, and
`OnboardingResult`.

Onboarding is a fixed, idempotent sequence over the existing RunPod control
plane, provider registry, capability registry, cost estimator, resolver, and
pre-spend guardrail. It is not a workflow engine. Manual `init`, `seed`,
`create-capability`, and `register-endpoint` remain supported for advanced or
recovery use (`pitwall.onboarding:RunPodOnboardingService`).

## Safety boundary

- Put the RunPod API key in `RUNPOD_API_KEY`. Requests contain only the
  credential reference name, never the value.
- For a private image, set `public_image` to `false` and select or create a
  registry auth. Registry creation takes a `password_env` reference; its value
  is resolved only at the RunPod client boundary.
- `plan`, `status`, and `rollback` are read-only. They may call RunPod discovery
  and resource-list APIs, but they make no provider or database writes.
- `apply` and `resume` require the exact `plan_id` produced for the unchanged
  request. Changing any desired field produces a different plan ID.
- The complete secret-free request is inspected before discovery, provider,
  audit, or database activity. A finding outside `probe_payload` fails closed;
  only the optional probe payload may be redacted.
- Endpoint plans default to `workers_min: 0`. Pod-lease onboarding constructs
  the production pod request with `intent: preview`; it never creates paid GPU
  compute.
- `describe_inference_probe: true` only describes the separately authorized
  `LIVE-RP-01` probe. Onboarding never performs paid inference.

Every result reports the exact proposed resource identities, database tables,
existing-resource reuse, cost ceiling, ordered steps, next step, dry-run
evidence, and rollback guidance. The dry-run exercises the production provider
configuration, capability resolution, cost quote, pre-spend inspection, and
endpoint or pod request construction.

`cost_impact` reports both the operator-confirmed rate and the conservative
current discovered rate. Planning fails if the confirmed rate is below the
current price for any selected cloud lane. It separates the execution ceiling
from the endpoint's requested minimum/maximum hourly worker range. Pod-lease
onboarding reports both endpoint hourly values as zero because it does not
launch the pod. A newly created network volume reports its exact size and
`network_volume_pricing: provider_rate_unavailable`: the existing RunPod
contracts do not supply a storage rate, so the plan makes that unpriced,
potentially billable impact explicit rather than inventing a dollar estimate.

## Endpoint request

This public-image request creates a template and a zero-minimum-worker queue
endpoint, then registers its capability and healthy provider binding:

```json
{
  "name": "demo-embedding",
  "topology": "endpoint",
  "capability_name": "embedding.demo",
  "capability_class": "embedding",
  "provider_name": "demo-runpod",
  "credential_ref": "RUNPOD_API_KEY",
  "image": "docker.io/example/worker:sha-abc",
  "public_image": true,
  "gpu_type_ids": ["NVIDIA L4"],
  "gpu_count": 1,
  "cloud": "ALL",
  "rate_per_hour_usd": "0.50",
  "template": {
    "mode": "create",
    "name": "demo-template",
    "disk_gb": 50,
    "ports": ["8000/http"]
  },
  "endpoint": {
    "mode": "create",
    "name": "demo-endpoint",
    "endpoint_type": "QUEUE",
    "workers_min": 0,
    "workers_max": 3,
    "idle_timeout_seconds": 60,
    "scaler_type": "QUEUE_DELAY",
    "scaler_value": 4
  },
  "probe_payload": {"input": "onboarding dry run"}
}
```

Use `mode: "existing"` with `resource_id` for a template or endpoint that
already exists. A matching resource created by this plan is detected by its
current provider state and durable ownership evidence. A same-name resource
that the plan does not own fails closed; select its ID explicitly to reuse it.
Selected and rediscovered resources must match every observable setting,
including endpoint workers/scaling/GPU count and resolved v2 pools. Template
argument and environment values are compared through non-serialized
fingerprints so those values never enter API, MCP, CLI, TUI, or audit output.

## Private-image pod-lease request

New network volumes require `cloud: "SECURE"` and a discovered datacenter.
Volumes are deliberately retained after a later failure because automatic
cleanup must not destroy data.

```json
{
  "name": "private-worker",
  "topology": "pod_lease",
  "capability_name": "worker.private",
  "capability_class": "custom",
  "provider_name": "private-runpod",
  "credential_ref": "RUNPOD_API_KEY",
  "image": "ghcr.io/example/private-worker:sha-abc",
  "public_image": false,
  "gpu_type_ids": ["NVIDIA L4"],
  "gpu_count": 1,
  "cloud": "SECURE",
  "data_center_id": "US-KS-2",
  "rate_per_hour_usd": "0.50",
  "registry": {
    "mode": "create",
    "name": "private-ghcr",
    "username": "operator",
    "password_env": "REGISTRY_PASSWORD"
  },
  "volume": {
    "mode": "create",
    "name": "private-cache",
    "size_gb": 100,
    "data_center_id": "US-KS-2"
  },
  "template": {
    "mode": "create",
    "name": "private-worker-template",
    "disk_gb": 50,
    "ports": ["8000/http"]
  },
  "endpoint": {"mode": "none"},
  "probe_payload": {}
}
```

## Operator sequence

Save a secret-free request as `onboarding.json`. The CLI plans when `--action`
is omitted:

```console
pitwall runpod-onboard onboarding.json --json
pitwall runpod-onboard onboarding.json \
  --action apply --confirmed-plan-id runpod_onboard_<24-hex> --json
pitwall runpod-onboard onboarding.json --action status --json
```

If apply fails, inspect `status`, correct the external cause without changing
the request, re-plan to confirm the same ID, and resume:

```console
pitwall runpod-onboard onboarding.json \
  --action resume --confirmed-plan-id runpod_onboard_<24-hex> --json
pitwall runpod-onboard onboarding.json --action rollback --json
```

A second apply after completion is zero-write. Apply and resume are serialized
across service instances and processes with a bounded PostgreSQL advisory lock;
the in-process lock remains an optimization. Current provider/broker state and
control-plane identity make a restart resumable. Missing or disabled broker
state makes a historical completion incomplete, and reapply recreates or
re-enables the deterministic row.

## Surface matrix

| Action | REST | MCP | CLI | TUI |
| --- | --- | --- | --- | --- |
| Plan | `POST /v1/admin/runpod/onboarding/plan` | `pitwall_runpod_onboarding_plan` | default or `--action plan` | Plan |
| Apply | `POST .../apply` | `pitwall_runpod_onboarding_apply` | `--action apply` | Apply + exact-ID modal |
| Status | `POST .../status` | `pitwall_runpod_onboarding_status` | `--action status` | Status |
| Resume | `POST .../resume` | `pitwall_runpod_onboarding_resume` | `--action resume` | Resume + exact-ID modal |
| Rollback guidance | `POST .../rollback` | `pitwall_runpod_onboarding_rollback` | `--action rollback` | Rollback guidance |

REST apply and resume use this envelope; `request` is the same object used for
plan:

```json
{
  "action": "apply",
  "confirmed_plan_id": "runpod_onboard_<24-hex>",
  "request": {}
}
```

REST onboarding is an admin namespace. MCP onboarding mutations are admin
tools. The CLI returns `0` on success, `2` for invalid input or plan-confirmation
mismatch, and `1` for bounded service/provider failure. TUI apply and resume
stay disabled until a plan or resumable status exists and require the exact
plan ID in a confirmation modal.

## Error codes

A failure is `{"error": "<code>", "detail": "<bounded text>", "step": "<step or null>", "result": <partial result or null>}`
on every surface; the detail never carries a credential or a provider response body. The REST
routes answer with:

| HTTP | Codes |
| --- | --- |
| 409 | `plan_confirmation_mismatch`, `broker_state_conflict`, `resource_drift`, `unowned_resource_conflict`, `onboarding_lock_timeout` |
| 422 | `credential_reference_unset`, `data_center_not_discovered`, `gpu_not_discovered`, `gpu_cloud_unavailable`, `gpu_count_unavailable`, `gpu_unavailable_in_data_center`, `guardrail_rejected`, `pricing_unavailable`, `rate_below_discovered_price`, `selected_resource_not_found`, `volume_unsupported_in_data_center`; and `invalid_request` when a route is called with the wrong `action` |
| 503 | `onboarding_lock_unavailable`, and every `runpod_audit_*` code |
| 502 | Every other code, including `discovery_unavailable`, `onboarding_state_unavailable`, `onboarding_step_failed`, `provider_result_invalid`, `resolution_mismatch`, `runpod_gpu_selection_unavailable`, `invalid_name`, `endpoint_not_ready`, `capability_disappeared`, `provider_disappeared`, and any RunPod control-plane failure as `runpod_<code>` |

The CLI maps `plan_confirmation_mismatch` and invalid input to exit `2`, and the other failures
to exit `1`.

## Resume and rollback behavior

Status is recomputed from current RunPod resources, broker rows, and bounded
events in `pitwall.config_audit`; no onboarding schema or migration is needed.
Events use the existing provider audit category with a
`runpod_onboarding_event` discriminator and contain no credential value.
The persistence adapter is `pitwall.onboarding:PostgresOnboardingState`.

On failure or cancellation, known plan-created resources are handled in reverse
dependency order:

- an endpoint, template, or registry auth created before provider registration
  is deleted when safe; a dependency is retained when an interrupted provider
  write may already reference it;
- a network volume is always retained for explicit operator review;
- after provider registration, dependent endpoint/template/registry resources
  are retained so the disabled broker records still describe a coherent
  topology;
- a plan-created provider and capability are disabled, not deleted, preserving
  audit history and enabling an exact resume;
- cleanup failure is reported as retained and requires console review.

A pre-write ownership event records the exact desired resource name. If RunPod
creates a resource but cancellation or a broken response loses its returned ID,
resume rediscovers that same name and validates its observable configuration.
Name ownership is bounded to a confirmed write attempt that first observed no
conflict; observable drift still fails closed.

These compensation rules are implemented in `pitwall.onboarding:RunPodOnboardingService`.

`rollback` is guidance, not a destructive command. Verify dependencies in the
RunPod console before deleting anything retained. After an interrupted provider
request, run `status` before retrying because the remote write may have
completed even if the caller was cancelled.

All automated onboarding tests use fakes, mocked transports, Textual Pilot, and
a disposable PostgreSQL schema. They do not read live credentials, call RunPod,
create paid compute, or perform inference.
