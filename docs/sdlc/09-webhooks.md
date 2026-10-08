# Inbound & Outbound Webhooks

## 1. Purpose & Scope

The webhook subsystem handles two opposing directions:

**Inbound (webhook_receiver)** — Receives HTTP callbacks from RunPod when queue jobs change state. Runs as a standalone FastAPI service (`python -m pitwall.webhook_receiver`, console script `pitwall-webhook`) on `PITWALL_WEBHOOK_HOST:PITWALL_WEBHOOK_RECEIVER_PORT` (default `127.0.0.1:8082`). Every delivery must carry a valid HMAC signature. The receiver persists every delivery attempt to `pitwall.runpod_webhook_deliveries` and, for terminal-status events, enqueues a background job to drive state reconciliation. Duplicate deliveries (same `runpod_job_id` + `attempt`) are acknowledged and not stored twice, via a DB-level `ON CONFLICT DO NOTHING`.

**Outbound (webhook_dispatcher)** — Sends signed HTTP POSTs to consumer-registered subscription URLs for `workload.completed` and the four lease lifecycle events. Each delivery carries a timestamped HMAC-SHA256 signature. A delivery attempt never sleeps: a retryable failure is recorded in `pitwall.webhook_delivery_failures` with a `next_retry_at`, and the reconciler's retry sweep makes the next attempt, up to four in all, so transient failures never pollute workload state. Subscription signing secrets are encrypted at rest.

Both directions share the same `sign`/`verify` scheme from `webhook_dispatcher.signer`. The public outbound contract (envelope, signature, egress rules, event data) is in [Webhook contract](../webhooks.md); this chapter documents how the code produces it.

## 2. Components

### `pitwall.webhook_receiver` — FastAPI receiver app

**Responsibility:** HTTP ingress for RunPod callbacks. Owns the lifespan (DB pool and optional Redis) and the `POST /webhooks/runpod` and `POST /runpod` routes, plus `GET /healthz`, `GET /health` (`{"ok": true, "service": "webhook-receiver"}`), and `GET /readyz`.

**Startup (module import and lifespan):**

- `require_runtime_env("webhook")` and `require_credentials_for_bind("webhook receiver", PITWALL_WEBHOOK_HOST, ("PITWALL_WEBHOOK_SECRET",))` run at import, so a non-loopback bind without the secret exits `os.EX_CONFIG`.
- The lifespan calls `require_webhook_secret()`, which raises `RuntimeError` when no inbound secret is configured: the receiver refuses to serve unauthenticated deliveries even on loopback.
- `PITWALL_WEBHOOK_SECRET` is the current secret and `PITWALL_WEBHOOK_PREVIOUS_SECRETS` (a JSON list of non-empty strings) lists older secrets that still verify, for zero-downtime rotation. A malformed list exits `os.EX_CONFIG`.
- It then creates an `asyncpg` pool (min 1, max 4) from `DATABASE_URL`, and builds the arq `RedisSettings` from `REDIS_URL` when present; a missing or invalid `REDIS_URL` disables job enqueueing and the receiver stays up.

**`WebhookIngressGuardMiddleware`:** applies to `/webhooks/runpod` and `/runpod` only, before any body is parsed.

| Check | Response |
|---|---|
| `Content-Type` is not `application/json` | 415 `content type must be application/json` |
| `Content-Length` is not an integer | 400 `invalid content length` |
| Declared or streamed body over `PITWALL_WEBHOOK_MAX_BODY_BYTES` (default 1 MiB; must be positive) | 413 `webhook body too large` |
| Per-client-IP token bucket empty (`PITWALL_WEBHOOK_RATE_LIMIT`, default `120/60s`) | 429 `webhook rate limit exceeded` with `Retry-After` |

All guard responses are `{"ok": false, "detail": "<text>"}`.

**Route handler `runpod_webhook`:**

1. Reads the raw body.
2. Verifies `X-Pitwall-Webhook-Signature` against the current and previous secrets with `signer.verify` (constant-time, 300-second replay window); no match returns 401 `invalid or missing webhook signature`.
3. JSON-decodes the body; invalid JSON or a non-object returns 400.
4. Tries `normalize_runpod_webhook(payload, headers)`. If it raises `ValueError` (no job ID), a fallback key is built from the `id`, `job_id`, `jobId`, and `runpod_job_id` fields, then the `RunPod-Job-Id` / `X-RunPod-Job-Id` headers, then the SHA-256 of the raw body, with attempt `1`.
5. For a terminal status (`COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`, `TIMEOUT`, `TIME_OUT`) it enqueues the arq job `_process_webhook_terminal_status` (job id `webhook-terminal:<job id>:<status>`) **before** recording the delivery. If Redis is configured and the enqueue fails, the handler returns 503 `could not enqueue webhook job; retry later` and records nothing, so RunPod's retry is processed. With no Redis configured the enqueue is skipped.
6. Calls `WebhookDeliveryRepository.insert_or_skip(runpod_job_id, attempt, payload)` and returns `{"ok": true, "duplicate": <bool>}`.

**`GET /readyz`:** 200 only when PostgreSQL answers and, when `REDIS_URL` is configured, Redis answers; otherwise 503. Bodies name each dependency's state and never carry connection details.

**Invariant:** Valid, authenticated deliveries are acknowledged idempotently. Duplicate deliveries (same job ID + attempt) are idempotent at the DB level.

---

### `pitwall.webhook_receiver.runpod` — Event model & normalization

**`RunPodWebhookEvent`:** Pydantic `BaseModel` with `extra="allow"`. Fields: `runpod_job_id: str` (non-empty after normalization), `status: str`, `attempt: int` (default 1, 1 to 3), `output: dict | None`, `error: str | None`, and `raw: dict` (the original payload).

**`normalize_runpod_webhook(payload, headers) -> RunPodWebhookEvent`:** reads `id`, `job_id`, `jobId`, `runpod_job_id` (in that order) for the job ID. Reads `attempt` from the payload (1 to 3), then from the `X-RunPod-Attempt`, `X-Runpod-Attempt`, `RunPod-Attempt`, `Runpod-Attempt` headers, else `1`. Raises `ValueError` if no valid job ID is found.

---

### `pitwall.webhook_receiver.__main__` — Entrypoint

`main()` parses service arguments (`--help` only, see [Deployment](19-deployment.md)), runs `require_valid_service_env("webhook")`, then starts uvicorn on `PITWALL_WEBHOOK_HOST` (default `127.0.0.1`) and `PITWALL_WEBHOOK_RECEIVER_PORT` (default `8082`) with `limit_concurrency=PITWALL_WEBHOOK_MAX_CONCURRENCY` (default `50`; at least 1).

---

### `pitwall.webhook_dispatcher` — Outbound dispatcher

The package exports `DEFAULT_RETRY_DELAYS`, `DEFAULT_TIMEOUT_SECONDS`, `MAX_ATTEMPTS`, `DeliveryOutcome`, `attempt_delivery`, `dispatch_completion`, `sign`, and `verify`.

**Constants:** `DEFAULT_RETRY_DELAYS = (0.0, 1.0, 3.0, 9.0)`, `DEFAULT_TIMEOUT_SECONDS = 30.0`, `MAX_ATTEMPTS = 4`.

**`DeliveryOutcome`:** `success`, `attempt`, `status_code`, `error_message`, `next_retry_at`, `delivery_id`, and `retryable`. `should_retry` is true only for a failed, retryable outcome with attempts remaining whose status is `None` (transport failure), `408`, `425`, `429`, or `5xx`. `state` is `delivered`, `retry_scheduled`, or `terminal_failure`.

**`attempt_delivery(webhook_url, payload, hmac_secret, *, attempt=1, retry_delays=..., timeout_seconds=..., delivery_id=None, loopback_allowlist=None, now=None) -> DeliveryOutcome`:** makes exactly one attempt and never sleeps.

- Serializes the payload as compact, sorted-key UTF-8 JSON (`allow_nan=False`) and signs those exact bytes; the body, headers, and `X-Pitwall-Delivery-ID` (the payload's `delivery_id`, else a fresh UUID) are identical across retries.
- Headers are `Content-Type: application/json`, `Content-Length`, `X-Pitwall-Delivery-ID`, and, when a secret is given, `X-Pitwall-Signature`.
- Resolves the target through `resolve_webhook_target` with `PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST` (unless `loopback_allowlist` is passed). A rejected target is a non-retryable failure, `Webhook target rejected by egress policy`.
- POSTs to each resolved address in turn; only an unreachable address set is a transport failure, `Webhook delivery transport failure`.
- A 2xx is `delivered`. A non-retryable non-2xx status below 500 is terminal (`Non-retryable HTTP status: <n>`). A retryable status or transport failure below attempt 4 sets `next_retry_at` from `retry_delays[attempt]` plus up to 20% jitter. Redirects are never followed.

**`dispatch_completion(workload_id, consumer, payload, subscriptions, ...) -> dict`:** builds one `workload.completed` envelope per `(subscription_id, webhook_url, hmac_secret)` tuple with `build_completion_event` (a fresh `delivery_id` each) and makes one `attempt_delivery` call per subscription. The result is keyed by subscription id with `success`, `attempt`, `status_code`, `error_message`, `next_retry_at`, `delivery_id`, and `state`. It does not raise on delivery failure.

**`build_completion_event(*, workload_id, consumer, payload, delivery_id, occurred_at=None)`:** the version `1` envelope `{version, event: "workload.completed", delivery_id, occurred_at (UTC, "Z"), workload_id, consumer, data}`.

**Callers:** the reconciler (`dispatch_workload_completion_webhooks`) calls `dispatch_completion` when a workload reaches a terminal state, stores each unsuccessful result in `pitwall.webhook_delivery_failures` through `WebhookDeliveryFailureRepository` (the payload keeps everything needed to rebuild the same envelope), and runs `_webhook_retry_sweep` every minute, which makes one `attempt_delivery` call per due row and settles it. A row with no `next_retry_at`, an inactive or deleted subscription, or four attempts is exhausted. `pitwall.leases.events` calls `attempt_delivery` directly for the lease lifecycle events and stores their exact envelope for redelivery.

---

### `pitwall.webhook_dispatcher.signer` — HMAC signing & verification

`sign(body: bytes, secret: str, timestamp: int | None = None) -> str` returns `t=<unix-seconds>,v1=<hex HMAC-SHA256 of "<timestamp>." + body>`. `verify(body, header, secret, max_age=300) -> bool` returns `False` for an empty header or one without a comma, a missing `t` or `v1`, a non-integer timestamp, a timestamp more than `max_age` seconds from now, or a digest mismatch (checked with `hmac.compare_digest`).

Invariant: both sides use the message `<timestamp>.<body>` with SHA-256. The 300-second `max_age` limits replay of captured payloads.

---

### `pitwall.webhook_dispatcher.secret_store` — secrets at rest

`WebhookSecretCipher` encrypts subscription signing secrets with versioned AES-256-GCM (a random 12-byte nonce per secret and the fixed associated data `pitwall:webhook-signing-secret:v1`). `from_environ` reads `PITWALL_WEBHOOK_ENCRYPTION_KEYS` (a non-empty JSON object of key version to URL-safe base64 32-byte key) and `PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY` (the version used for new and rotated secrets, which must be in the object). `decrypt` raises `ValueError` for a secret whose key version is no longer configured, so retain old keys until every subscription has been rotated. `WebhookSubscriptionRepository` encrypts on write and decrypts only for dispatch (`list_for_dispatch`, `get_for_dispatch`).

---

### `pitwall.webhook_dispatcher.security` — egress policy and pinned delivery

`resolve_webhook_target(url, *, resolver, loopback_allowlist=())` returns a `ResolvedWebhookTarget(url, hostname, port, request_target, addresses)` or raises `WebhookTargetRejected`. It rejects a malformed URL, a fragment, user information, `\` or `%` in the host, `localhost` and `*.localhost`, encoded or non-canonical numeric hosts, an invalid IDNA host, any scheme other than HTTPS (except allow-listed loopback HTTP), any port other than 443, and any DNS answer that is not a global address. An `http` URL is accepted only with an explicit port whose `127.0.0.1` or `::1` authority is in the allow-list. `post_resolved_webhook` pins the socket to one validated address (HTTPS verifies the connected peer and uses the original host for SNI; allow-listed loopback uses plain HTTP) and reads at most 4096 bytes of the response. `redact_webhook_url` drops the query and fragment for display.

## 3. Delivery Semantics

### Inbound idempotency

The receiver relies on a PostgreSQL UNIQUE constraint on `(runpod_job_id, attempt)` in `pitwall.runpod_webhook_deliveries`. `WebhookDeliveryRepository.insert_or_skip` emits:

```sql
INSERT INTO pitwall.runpod_webhook_deliveries
    (runpod_job_id, attempt, payload, received_at)
VALUES ($1, $2, $3, NOW())
ON CONFLICT (runpod_job_id, attempt) DO NOTHING
RETURNING id
```

It returns `WebhookDeliveryResult(is_new=True)` if a row was inserted, `is_new=False` if the conflict was skipped. The handler's response includes `"duplicate": not result.is_new`. Separate attempt numbers (1, 2, 3) each get their own row; only identical `(runpod_job_id, attempt)` pairs are deduplicated.

### Inbound HMAC verification (mandatory)

Every inbound request must carry a valid `X-Pitwall-Webhook-Signature` for `PITWALL_WEBHOOK_SECRET` or one of `PITWALL_WEBHOOK_PREVIOUS_SECRETS`. The receiver does not start without a secret. Every configured secret is checked on each request.

### Outbound signing

Every outbound POST carries `X-Pitwall-Signature: t=<timestamp>,v1=<hexdigest>`, computed over the exact transmitted bytes with the subscription's signing secret (decrypted just before the attempt). The timestamp is taken at each attempt, while the body and `X-Pitwall-Delivery-ID` stay the same across retries.

### Retry schedule

Attempt 1 is made when the event occurs. After a retryable failure the next attempt is due at `now + delay` with the delay from `DEFAULT_RETRY_DELAYS` indexed by the attempt just made (`1.0`, `3.0`, then `9.0` seconds, plus up to 20% jitter), but the reconciler's sweep runs once a minute, so the effective spacing is at least a minute. After attempt 4 the row is exhausted.

## 4. Public Interfaces

### From `pitwall.webhook_receiver`

- `app` — the FastAPI application instance (for mounting in tests).
- `require_webhook_secret()` — raises `RuntimeError` when no inbound secret is configured.
- `WebhookIngressGuardMiddleware` — the media-type, size, and rate guard.

### From `pitwall.webhook_dispatcher`

```python
async def attempt_delivery(
    webhook_url: str,
    payload: dict[str, Any],
    hmac_secret: str | None,
    *,
    attempt: int = 1,
    retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    delivery_id: str | None = None,
    loopback_allowlist: tuple[str, ...] | None = None,
    now: dt.datetime | None = None,
) -> DeliveryOutcome

async def dispatch_completion(
    workload_id: str,
    consumer: str,
    payload: dict[str, Any],
    subscriptions: list[tuple[int, str, str | None]],
    retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]
```

### From `pitwall.webhook_dispatcher.signer`

```python
def sign(body: bytes, secret: str, timestamp: int | None = None) -> str
def verify(body: bytes, header: str, secret: str, max_age: int = 300) -> bool
```

### Internal callers

| Caller | Calls |
|---|---|
| `pitwall.reconciler` | `dispatch_completion`, `attempt_delivery`, `WebhookDeliveryFailureRepository`; the ARQ job `_process_webhook_terminal_status` that the receiver enqueues |
| `pitwall.leases.events` | `attempt_delivery` for `lease.*` events |
| `pitwall.webhook_receiver` | `WebhookDeliveryRepository.insert_or_skip()`, `signer.verify` |
| `pitwall.api.routes.webhook_subscriptions` | `WebhookSubscriptionRepository`, `WebhookSecretCipher`, `resolve_webhook_target` |

## 5. Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `PITWALL_WEBHOOK_SECRET` | none (required) | Current inbound HMAC secret; the receiver refuses to start without it, and a non-loopback bind fails at import |
| `PITWALL_WEBHOOK_PREVIOUS_SECRETS` | `[]` | JSON list of older inbound secrets that still verify, for rotation |
| `PITWALL_WEBHOOK_HOST` | `127.0.0.1` | Receiver bind address |
| `PITWALL_WEBHOOK_RECEIVER_PORT` | `8082` | Receiver port |
| `PITWALL_WEBHOOK_MAX_BODY_BYTES` | `1048576` | Request body cap; must be positive |
| `PITWALL_WEBHOOK_RATE_LIMIT` | `120/60s` | Per-client-IP request limit, `<requests>/<window>` |
| `PITWALL_WEBHOOK_MAX_CONCURRENCY` | `50` | uvicorn concurrency limit; at least 1 |
| `PITWALL_WEBHOOK_ENCRYPTION_KEYS` | none | JSON object of key version to URL-safe base64 32-byte AES key for subscription secrets |
| `PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY` | none | Key version used for new and rotated secrets |
| `PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST` | empty | Exact loopback `host:port` targets that may receive plain-HTTP deliveries |
| `DATABASE_URL` | required | PostgreSQL connection string for the receiver |
| `REDIS_URL` | optional | ARQ Redis connection string; if absent, terminal-status job enqueueing is skipped |

## 6. Failure Modes & Error Types

### Inbound (webhook_receiver)

| Condition | HTTP response | Body |
|---|---|---|
| Missing or wrong `Content-Type` | 415 | `{"ok": false, "detail": "content type must be application/json"}` |
| Invalid `Content-Length` | 400 | `invalid content length` |
| Body over the cap | 413 | `webhook body too large` |
| Rate limit exceeded | 429 | `webhook rate limit exceeded`, with `Retry-After` |
| Missing or invalid HMAC signature | 401 | `invalid or missing webhook signature` |
| Invalid or non-object JSON | 400 | `webhook body must be valid JSON` or `webhook body must be a JSON object` |
| No valid job ID found | 200 | falls back to a SHA-based idempotency key |
| Redis configured but the terminal-status enqueue fails | 503 | `could not enqueue webhook job; retry later`; nothing is recorded |
| No inbound secret configured | process exit | lifespan raises `RuntimeError` naming `PITWALL_WEBHOOK_SECRET` |
| `DATABASE_URL` unset | process exit | lifespan raises `RuntimeError("DATABASE_URL is not set")` |
| Dependency unavailable at the readiness probe | 503 | per-dependency state only; no connection details |

`/healthz` is process liveness. `/readyz` validates PostgreSQL and, when Redis is configured, Redis.

### Outbound (webhook_dispatcher)

`attempt_delivery` and `dispatch_completion` return outcomes; they do not raise on delivery failure.

| Condition | Behavior |
|---|---|
| HTTP 2xx | `delivered` |
| HTTP 408, 425, 429, or 5xx | `retry_scheduled` with `next_retry_at`, until attempt 4 |
| Other HTTP status | `terminal_failure`, `Non-retryable HTTP status: <n>` |
| Timeout, `OSError`, or HTTP protocol error on every resolved address | `retry_scheduled`, `Webhook delivery transport failure` |
| Target rejected by the egress policy | `terminal_failure`, `Webhook target rejected by egress policy` |
| Attempt 4 failed | `terminal_failure`; the failure row has no `next_retry_at` |

Error messages never include a response body or the target URL.

### `signer.verify` returns `False` for

- Empty header or header without `,`
- Missing `t` or `v1` parts
- Non-integer timestamp
- Timestamp outside ±300s of current time
- HMAC digest mismatch (constant-time comparison)

## 7. Testing

| Test file | What it covers |
|---|---|
| `tests/security/test_webhook_receiver_signed.py` | Mandatory HMAC gate: valid signature accepted, missing/wrong/tampered/stale rejected; the handler delegates to `signer.verify`. |
| `tests/security/test_webhook_receiver_unauthenticated.py` | Pins that the request handler alone accepts an unsigned POST when no secret is configured; the lifespan's `require_webhook_secret` is what keeps a running service out of that state. |
| `tests/webhook_receiver/test_startup.py` | The receiver refuses to start without a secret. |
| `tests/webhook_receiver/test_enqueue_order.py` | A failed enqueue does not mark the delivery seen, a successful one uses the registered job name and then marks it, and the arq pool is reused across requests. |
| `tests/test_webhook_duplicate_delivery_stress.py` | 100 concurrent POSTs of the same payload produce exactly 1 DB row; different attempts each get their own row. |
| `tests/perf/test_webhook_fast200.py` | `/webhooks/runpod` under 50 ms median (no DB, mocked repo). |
| `tests/test_webhook_and_exporter.py` | Health endpoints and module import sanity. |
| `tests/webhook_dispatcher/test_dispatcher.py`, `test_retry_scheduling.py`, `test_security.py`, `test_signer.py` | Single-attempt delivery, retry classes and schedule, egress policy and pinning, signing and verification. |
| `tests/leases/test_event_delivery.py`, `tests/leases/test_events.py` | Lease lifecycle event delivery. |
| `tests/api/test_webhook_subscriptions_contract.py` | Subscription API contract. |
| `tests/api/test_e2e_async_job_webhook.py` | Live RunPod queue job: posts a real webhook to the receiver and asserts `duplicate` is false on the first delivery and true on the second identical POST. |

## 8. Dependencies

### From `pitwall.webhook_receiver`

```python
import asyncpg  # async PostgreSQL driver
import redis.asyncio as redis
from fastapi import FastAPI, Request
from pitwall.config import parse_rate_limit, require_credentials_for_bind, require_runtime_env
from pitwall.db.repository import WebhookDeliveryRepository
from pitwall.rate_limits import TokenBucket
from pitwall.webhook_dispatcher.signer import verify as verify_signature
from pitwall.webhook_receiver.runpod import RunPodWebhookEvent, normalize_runpod_webhook
```

### From `pitwall.webhook_dispatcher`

The dispatcher uses only the standard library (`http.client`, `ssl`, `socket`, `asyncio`), `cryptography` for AES-GCM, and `pitwall.config`; outbound delivery does not use `httpx`.

### External runtime dependencies

| Library | Used by | Purpose |
|---|---|---|
| `asyncpg` | receiver, repository | PostgreSQL async driver |
| `fastapi` | receiver | HTTP framework |
| `uvicorn` | receiver `__main__` | ASGI server |
| `pydantic` | runpod model | Schema validation |
| `cryptography` | `secret_store` | AES-256-GCM for subscription secrets |
| `arq` | receiver | Redis-based job queue (optional) |
