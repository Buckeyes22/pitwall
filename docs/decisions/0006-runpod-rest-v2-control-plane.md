# ADR 0006: RunPod REST v2 control-plane contract

- Status: Accepted
- Date: 2026-08-31
- Decision owner: Project maintainer

## Context

RunPod REST v1 is deprecated and its official migration guide schedules retirement for
2026-11-15. Pitwall currently uses REST v1 for pod, template, network-volume, registry, and
serverless-endpoint control-plane operations. REST v2 is not a base-URL substitution: request
field names and structures, collection envelopes, resource paths, lifecycle actions, error
responses, and several placement controls differ.

This decision is grounded in:

- the [RunPod REST API v2 overview](https://docs.runpod.io/api-reference-v2/overview);
- the [official v1 to v2 migration guide](https://docs.runpod.io/api-reference-v2/migrate-from-v1);
- the official OpenAPI document at <https://api.runpod.io/v2/openapi.json>, observed on
  2026-08-31 as version `2.0.0`, SHA-256
  `688c7b6f1d5386f04aa6d029b1c744fa11686540350e4411e8f3abe4bbf33d38`;
- [`docs/evidence/2026-08-30-runpod-live-findings.md`](../evidence/2026-08-30-runpod-live-findings.md);
- the existing Pitwall public API, provider adapter, and hermetic RunPod tests.

The observed OpenAPI schemas use `unevaluatedProperties: false` or
`additionalProperties: false` on migrated write requests. Sending v1 names to v2 is therefore a
contract error, not a harmless compatibility behavior.

## Decision

Pitwall uses `https://api.runpod.io/v2` as the default RunPod control-plane base URL. Requests are
constructed by strict, resource-specific models before any network call. Minimal captured schema
fixtures test the exact fields, required envelopes, and paths Pitwall relies on; the full upstream
OpenAPI document is not copied into the repository.

### Resource mappings

| Operation | REST v2 contract |
| --- | --- |
| Pod create | `POST /v2/pods`; `imageName` becomes `image`, `cloudType` becomes `cloud`, `gpuTypeIds`/`gpuCount` become `gpu.id`/`gpu.count`, `containerDiskInGb` becomes `disk`, registry auth becomes `registry`, and persistent/network storage becomes `mounts` |
| Pod list/get/update | `/v2/pods`; list responses require the `pods` envelope and update uses v2 field names |
| Pod lifecycle | `POST /v2/pods/{id}/action` with `start`, `stop`, `restart`, or `terminate` |
| Templates | `/v2/templates`; create, get, update, and delete use v2 container fields and strict bodies; list responses require the `templates` envelope |
| Network volumes | `/v2/network-volumes`; `dataCenterId` becomes `dataCenter` and list responses require `networkVolumes` |
| Serverless endpoints | `/v2/serverless`; worker, scaler, GPU-pool, and flashboot settings use nested v2 shapes and list responses require `endpoints` |
| Registry credentials | `/v2/registries`; list responses require `registries` |

Pod responses are normalized back to Pitwall's existing internal/public field expectations where
needed. No database or user-state migration is involved.

### GPU and cloud fallback

REST v2 accepts one pod GPU type ID in each request. Pitwall preserves caller ordering by issuing
one validated create request per GPU ID, and preserves `cloudType=ALL` by trying `COMMUNITY` then
`SECURE`. Capacity-classified failures advance to the next combination; permanent validation or
authentication failures stop immediately. A created paid pod is still terminated before another
fallback is attempted when post-create allocation or cost guards fail.

The v1 `gpuTypePriority=availability` behavior cannot be reproduced from the create request alone,
so requests that explicitly require provider-side availability ordering retain the bounded legacy
v1 create path. Custom ordering is reproduced by the v2 sequence above.

### Command arguments

Pitwall converts command argument vectors to v2's single `args` string with POSIX shell quoting.
The conversion must round-trip through `shlex.split()` for spaces, quotes, empty arguments,
Unicode, and shell metacharacters. Arguments containing a NUL byte are rejected during local
validation, before any write. Pitwall never concatenates argv elements with a plain space.

### Capabilities without a behavior-preserving v2 equivalent

No existing option is silently dropped:

| Existing behavior | Decision |
| --- | --- |
| `gpuTypePriority=custom` | Emulate through ordered single-GPU v2 requests. |
| `gpuTypePriority=availability` | Use the legacy v1 pod-create path because v2 has no equivalent request field. |
| `dataCenterPriority=custom` | Preserve the exact single selection through v2 `dataCenterIds`. |
| `dataCenterPriority=availability` | Use the legacy v1 pod-create path when a data-center preference is supplied; v2 cannot preserve provider-side priority semantics. |
| `minVCPUPerGPU` | Use the legacy v1 pod-create path whenever a CPU floor is requested. |
| `minRAMPerGPU` | Use the legacy v1 pod-create path whenever a RAM floor is requested. |
| `dockerEntrypoint` | Use the legacy v1 pod-create path when an entrypoint override is requested; v2 exposes only `args`. Template entrypoint overrides are rejected before a write. |
| `supportPublicIp=true` | Use the legacy v1 pod-create path; v2 has no equivalent create field. |
| Pod `reset` | Keep the legacy v1 reset operation because v2's unified action schema has no reset action. |
| Template `readme` update | Reject before a write because REST v2 has no corresponding field. |

The legacy base is `https://rest.runpod.io/v1` and is configured separately through
`RUNPOD_REST_V1_API_URL` (or `RunPodCredentials.rest_v1_api_url`). It is used only for the cases
above. Ordinary pod reads, lists, updates, termination, start, stop, and restart always use v2.
The four placement and resource-floor fields are never sent to v2.

### Request, response, and failure handling

- Authentication remains `Authorization: Bearer <RUNPOD_API_KEY>`; credentials never appear in
  URLs or errors.
- Control-plane requests use bounded timeouts. Only operations that are safe to repeat, plus
  explicitly rejected `429` requests, receive a bounded retry; pod/resource creation is not
  retried after an ambiguous transport failure.
- Non-success responses are classified using status plus the RFC 9457 `title`, `detail`, and
  `errors` fields when present. Error text is length-bounded and secrets are redacted.
- Collection envelopes and resource objects are validated before callers consume them. Unknown
  request fields, malformed responses, invalid IDs, and unsupported compatibility combinations
  fail before a provider write where locally discoverable.
- Tests use in-process fake HTTP transports and captured minimal schema facts. They never require
  credentials, contact RunPod, or create billable resources.

## Compatibility

Pitwall's API and provider-adapter call shapes remain stable. Existing default pod workloads still
request CPU and RAM floors, so they deliberately use the bounded legacy create path until RunPod
v2 can express those constraints or the product owner changes those defaults. Callers that omit
the floors use strict v2 create. Pitwall's serverless API requires explicit GPU selection; a
create without one is rejected before a write rather than relying on the v1 scheduler default.

## Rollback

Rollback is an ordinary Git revert of this change. There is no feature flag, API-version router,
data migration, state migration, or dual-write. Reverting restores the previous code and default
base URL; provider resources and Pitwall database records need no conversion.

## Rejected alternatives

- A base-URL-only change, because strict v2 schemas reject v1 request names.
- Silently omitting unsupported placement/resource controls, because that can alter cost,
  capacity, or command execution.
- A generic API-version framework or provider transport plugin, because Pitwall has one concrete
  RunPod migration and no second consumer.
- A permanent dual-path feature flag, because Git revert is sufficient and does not create a
  second configuration source of truth.
