# Webhook contract

Pitwall's outbound webhook contract is versioned independently of the REST API.
The current event envelope is version `1`:

```json
{
  "version": "1",
  "event": "workload.completed",
  "delivery_id": "a stable UUID shared by every retry",
  "occurred_at": "2026-07-17T20:00:00Z",
  "workload_id": "wkl_...",
  "consumer": "example-consumer",
  "data": {}
}
```

| Event | Data fields |
| --- | --- |
| `lease.ready` | `capability`, `lease_id`, `served_model_id`, `variant` |
| | `expires_at`, `proxy_base_url`, `idle_timeout_min`, `created` |
| `lease.renewed` | `capability`, `lease_id`, `expires_at` |
| | `renewed_by` (`operator` or `activity`) |
| `lease.stopped` | `capability`, `lease_id`, `reason` |
| | Reasons: `ttl`, `idle`, `operator`, `kill_switch`, `budget` |
| | `max_lifetime`, or `provider_failure` |
| `lease.expiring` | `capability`, `lease_id`, `expires_at`, `minutes_left` |

Subscriptions select one or more allowed `event_types`. Existing subscriptions default to
`workload.completed`; routing receivers explicitly request the four lease lifecycle types.
Each subscription delivery receives its own delivery ID and signed body; that ID and body remain
stable across the bounded retries for that delivery.

Lease subscriptions accept `lease.ready`, `lease.renewed`, `lease.stopped`, and
`lease.expiring` in addition to existing event types. Each uses the same versioned, signed,
retrying envelope: `event`, `occurred_at`, and `data`. Ready data carries the
capability, lease/model/variant, expiry, proxy URL, idle timeout, and `created`;
renewed carries expiry and `renewed_by`; stopped carries a bounded `reason`.

The body is UTF-8 JSON serialized with sorted keys and compact separators. The
exact transmitted bytes are signed as `HMAC-SHA256("<timestamp>." + body)`.
`X-Pitwall-Signature` contains `t=<unix-seconds>,v1=<hex-digest>`, and
`X-Pitwall-Delivery-ID` matches the envelope. Consumers must verify the raw body
before parsing it, use a constant-time digest comparison, reject timestamps
outside their chosen window, and deduplicate by delivery ID. A standalone
stdlib verifier is in [`examples/verify_webhook.py`](../examples/verify_webhook.py),
with a golden vector in
[`tests/fixtures/webhooks/completion-v1.json`](../tests/fixtures/webhooks/completion-v1.json).

Delivery permits only HTTPS port 443. Pitwall rejects user information,
fragments, ambiguous numeric hosts, and every non-global A/AAAA answer. Each
attempt resolves again, rejects mixed public/private answers, pins the socket to
one validated address, verifies the connected peer, and uses the original host
for TLS SNI. Redirects are never followed. Statuses 408, 425, 429, and 5xx plus
transport timeouts retry at most four times with exponential backoff and jitter;
other non-2xx statuses are terminal.

Subscription management requires the `webhook:admin` bearer scope. Creation
returns a generated signing secret once. List responses omit the secret and URL
query string. Rotation revalidates the stored target against the current egress policy before it
reactivates the subscription, then returns a new secret once; deactivate, activate, and
delete operations are audited. Signing secrets use versioned AES-256-GCM at
rest. Configure `PITWALL_WEBHOOK_ENCRYPTION_KEYS` as a JSON map of key version
to URL-safe base64 32-byte key and select the write key with
`PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY`. Retain old keys until every
subscription has been rotated.

Inbound RunPod callbacks are a separate surface. Non-loopback binding requires
`PITWALL_WEBHOOK_SECRET`; `PITWALL_WEBHOOK_PREVIOUS_SECRETS` permits
zero-downtime rotation. The receiver enforces JSON content type, a 1 MiB default
streaming body limit, a `120/60s` per-client rate limit, and the signature
timestamp window before JSON parsing. Duplicate RunPod job/attempt deliveries
are acknowledged with 200 and marked `duplicate: true`; this is idempotent
processing, not rejection of every repeated signature.

### Local routing receivers

Webhook delivery remains HTTPS-only on port 443 and public-address-only by default. An operator
may set `PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST` to a comma-separated list of exact loopback
authorities, for example `127.0.0.1:8765,[::1]:8765`. Only `127.0.0.1` and `::1` are accepted.
An allow-listed subscription may use plain HTTP at that exact host and port; all other HTTP,
loopback, private, link-local, and non-443 public targets remain refused. Signing and retry rules
are identical for loopback and public deliveries.

Lease lifecycle deliveries have this top-level envelope:

```json
{
  "version": "1",
  "event": "lease.ready",
  "delivery_id": "4c2f1e4d-0958-4c4d-a3eb-ef75d1f42bdd",
  "occurred_at": "2026-08-28T12:00:00Z",
  "capability": "llm.glimmer",
  "data": {
    "capability": "llm.glimmer",
    "lease_id": "lease-01",
    "served_model_id": "muse-glimmer-30b",
    "variant": "fp8",
    "expires_at": "2026-08-28T14:00:00+00:00",
    "proxy_base_url": "http://pitwall/v1/openai/llm.glimmer/v1",
    "idle_timeout_min": 20,
    "created": true
  }
}
```

Lease envelopes omit `workload_id` and `consumer`. Receivers deduplicate on `delivery_id` and
verify the literal `X-Pitwall-Signature` header with the documented 300-second replay window.
