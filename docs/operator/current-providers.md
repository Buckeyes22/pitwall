# Current Provider Operator Reads

This guide covers the bounded, read-only provider-operations model used by the
REST, MCP, CLI, and Providers TUI adapters. It does not authorize routing,
inference, provisioning, termination, or any other provider mutation.

Vast, Together, and Lambda Cloud remain experimental operator integrations.
Their registration and read visibility are not a claim of live support; that
requires the matching optional provider countersign before a deployment enables
provider-specific live egress.

## Safety boundary

Provider descriptors are persisted reads. They disclose a provider ID, adapter
ID, credential *reference* name, whether that reference is configured, declared
capabilities, persisted health, safe configuration, and pricing kind. They never
disclose an environment value or an authorization header.

Availability and `health --probe` are explicit, bounded live reads. They do not
create provider resources, update persisted health, write audits, reserve spend,
or change hard-budget behavior. An unset or empty credential reference produces
`credential_unavailable` before any provider HTTP request. Provider failure and
timeout states carry a stable error code, never a provider response body.

An availability read reports `status` `available` (items returned), `empty`, `unavailable`
(nothing was attempted), or `error` (the provider call failed), plus a nullable `error_code`:

| `error_code` | `status` | Meaning |
| --- | --- | --- |
| `provider_disabled` | `unavailable` | The provider record is disabled |
| `adapter_unregistered` | `unavailable` | No adapter is registered for the provider's `adapter_id` |
| `capability_unsupported` | `unavailable` | The adapter does not declare the `availability` capability |
| `credential_unavailable` | `unavailable` or `error` | The credential reference is unset or empty, or credential resolution failed |
| `provider_timeout` | `error` | The provider call timed out |
| `provider_error` | `error` | Any other provider failure |

`health --probe` maps those statuses to `live_status`: `healthy` for `available` and `empty`,
`unavailable`, and `unhealthy` for `error`; without `--probe` it is `not_probed` and
`error_code` is null.

The current bounds are one provider at a time and 1–100 availability items. The
TUI lists descriptors without provider-network egress; entering a provider ID
and pressing `g` is required before it performs one health probe.

`health --probe` uses an availability sample of at most one item to determine
live health, so its `availability_count` is a 0/1 probe sample rather than a
catalogue total. Use the availability operation when the bounded item list is
needed.

## Current adapter contracts

The hermetic source metadata is
[`tests/fixtures/providers/current_provider_contracts_2026-09-01.json`](../../tests/fixtures/providers/current_provider_contracts_2026-09-01.json).
It contains URLs and contract versions only—no account, credential, or live
response data. Each adapter has a normalized SHA-256 fingerprint over the
captured endpoint/auth/version facts. It detects fixture drift; it is not a
claim that a remote documentation page was downloaded or content-hashed.

| Adapter | Credential reference | Explicit availability source | Contract and operator notes |
| --- | --- | --- | --- |
| Vast | `VAST_API_KEY` | `POST https://console.vast.ai/api/v0/bundles/` | `vast-api-v0-bundles-2026-09-01`; bearer authentication. Instance reads use the separate v1 endpoint and `after_token`, bounded to 25 per page and 100 total. [Official API reference](https://docs.vast.ai/api-reference/introduction) |
| Together | `TOGETHER_API_KEY` | `GET https://api.together.ai/v1/models` | `together-v1-models-2026-09-01`; bearer authentication. This adapter supports inference and model-catalogue availability, not compute lifecycle. [Official API reference](https://docs.together.ai/reference/models-1) |
| Lambda Cloud | `LAMBDA_CLOUD_API_KEY` | `GET https://cloud.lambda.ai/api/v1/instance-types` | `lambda-cloud-openapi-1.10.0`; bearer authentication. The adapter keeps a one-second request interval and twelve-second launch interval; operator reads do not launch. [Official Cloud API](https://docs.lambda.ai/public-cloud/cloud-api/) |
| OpenAI-compatible gateway | `PITWALL_GATEWAY_API_KEY` (optional; keyless pools carry no credential at all) | Synced catalog evidence (`config.gateway.catalog` on the provider row); the availability read performs no egress | `gateway-catalog-2026-09-10`; the single availability item mirrors the catalog's `free_type`, `tos`, `trains_on_prompts`, `hard_stop_guaranteed`, `pool_key`, and token-budget fields for the provider's `gateway.model_id`. Inference proxies to the configured `gateway.base_url` (default loopback `http://127.0.0.1:20130/v1`) and maps HTTP 429 signals onto typed quota reasons |

All prices exposed by availability are decimal strings with their provider unit
names (for example, `usd_per_hour` or `usd_per_million_input_tokens`). They are
observed catalogue/offer facts, not an invoice, actual-cost reconciliation, or a
spend admission decision.

## Operator commands and surfaces

The registered CLI group is:

```text
pitwall provider-ops list --json
pitwall provider-ops describe PROVIDER_ID --json
pitwall provider-ops availability PROVIDER_ID --limit 100 --json
pitwall provider-ops health PROVIDER_ID [--probe] --json
```

`--json` is the same typed service serialization used by REST and MCP. REST
uses the `/v1/provider-ops` read namespace; MCP tool names begin with
`pitwall_provider_ops_`. The default health read is persisted-only. Use a
probe only when a real-time availability read is intentionally needed.

## Evidence and live operation

MC-01 validation uses `httpx.MockTransport`, ASGI transport, and a DNS-denial
test. It makes no live provider calls, uses no credentials, and performs no
provider writes. A live provider check is an operator-owned, separately
approved action: use an isolated account, an explicit provider-specific egress
gate, a known credential reference, a documented request limit, and the
provider’s own cleanup procedure. A read-only availability/health check should
not create a resource, so it has no Pitwall cleanup action.
