# RunPod REST v2 operator note

Pitwall uses RunPod REST v2 for its control-plane reads and writes. This is a code-level contract
migration; there is no data migration, provider-resource conversion, dual-write, or runtime API
version switch.

## Configuration

| Variable | Default | Use |
| --- | --- | --- |
| `RUNPOD_REST_API_URL` | `https://api.runpod.io/v2` | Pods, templates, serverless endpoints, network volumes, and registries |
| `RUNPOD_REST_V1_API_URL` | `https://rest.runpod.io/v1` | Bounded legacy pod create/reset only |
| `RUNPOD_API_KEY` | none | Header-only bearer authentication for both bases |

Provider-plugin credentials expose the same separation as `rest_api_url` and
`rest_v1_api_url`. Do not point the v1 field at v2 or use the v1 field as a general rollback
mechanism.

## When v1 is still used

A pod create stays on v1 only when the request needs behavior v2 cannot represent:

- provider-side `gpuTypePriority=availability`;
- provider-side `dataCenterPriority=availability` with a data-center preference;
- `minVCPUPerGPU` or `minRAMPerGPU` resource floors;
- a Docker entrypoint override; or
- public-IP creation support.

Pod reset is also v1-only. Ordinary pod reads, lists, updates, start, stop, restart, termination,
template CRUD, network-volume CRUD, registry CRUD, and serverless endpoint CRUD use v2. Pitwall
logs the exact reason whenever pod creation selects the bounded v1 path. No option is silently
removed from a v2 request.

## Validation and recovery

Before deployment, run:

```bash
uv sync --frozen --extra dev --python 3.14.7
uv run --frozen pytest -q tests/runpod_client
uv run --frozen pytest -q tests/providers/test_runpod_adapter.py tests/leases/test_launch.py
```

These tests use fake transports and a DNS guard that fails any attempted connection to RunPod's
real control-plane hosts. They require no provider credential and create no resources.

If the deployed code must be rolled back, revert the Git change through the normal deployment
process. Do not edit provider state, leases, or databases; the migration changed request/response
handling only. A rollback does not require deleting or recreating RunPod resources.

The complete field mapping, schema provenance, compatibility decisions, and retry/error behavior
are in [ADR 0006](../decisions/0006-runpod-rest-v2-control-plane.md). The pre-migration live
evidence remains in
[the RunPod findings](../evidence/2026-08-30-runpod-live-findings.md).
