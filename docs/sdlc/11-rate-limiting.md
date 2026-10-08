# Rate Limiting Subsystem

## 1. Purpose & Scope

`pitwall.rate_limits` holds the pure token-bucket arithmetic and the `Retry-After` parser that the
rest of Pitwall shares. It has no database or Redis dependency and no process-wide state: a bucket
is an in-memory object owned by whoever creates it. Three callers use it:

- **Inbound REST limiter** — `InboundRateLimitMiddleware` in `pitwall.api.app` keeps one
  `TokenBucket` per `(client_ip, token_digest)` (section 4).
- **Gateway limiter** — `pitwall.gateway.auth:GatewayAuth` keeps one `TokenBucket` per authenticated
  gateway token (`PITWALL_GATEWAY_RATE_LIMIT_RPM` requests per rolling 60 s; see
  [24 — Gateway](24-gateway.md)).
- **RunPod clients** — `pitwall.runpod_client.retry` uses `parse_retry_after` to bound a 429
  `Retry-After` delay (`QueueClient`, `LBClient`, `ServerlessClient`).

An earlier release also persisted outbound per-endpoint buckets in Postgres. That store, its
`TokenBucketRateLimiter`, and the `pitwall.rate_buckets` table are gone (migration
`0040_drop_rate_buckets.sql`); nothing in the current code paces outbound RunPod calls through a
shared bucket. Outbound protection is the bounded retry policy in `pitwall.runpod_client.retry`.

---

## 2. Components

### `pitwall.rate_limits.algorithm`

Two pure functions and one small mutable class.

```python
REFILL_WINDOW_S = 10.0

def refill_tokens(*, tokens, capacity, elapsed_s, refill_window_s=10.0) -> float
def seconds_until_available(*, tokens, capacity, tokens_needed=1.0, refill_window_s=10.0) -> float
```

- `refill_tokens` refills at `capacity / refill_window_s` tokens per second, capped at `capacity`.
- `seconds_until_available` is `0.0` when enough tokens exist, `math.inf` when `tokens_needed >
  capacity`, otherwise the deficit divided by the refill rate.

Every function validates its inputs and raises `ValueError` on a non-positive capacity, window, or
token request, or on negative tokens or elapsed time.

`TokenBucket(capacity, tokens=None, last_refilled_at_s=None, refill_window_s=10.0)` is an in-memory
bucket on `time.monotonic()`:

- `refill(now_s=None) -> float` refills in place and returns the current token count.
- `resize(capacity, now_s=None)` refills, then applies a new capacity and caps the tokens.
- `try_consume(tokens=1.0, now_s=None) -> bool` refills and consumes if enough tokens exist.
- `retry_after_s(tokens=1.0) -> float` is the seconds until that many tokens are available.

A new bucket starts full. `pitwall.api.exceptions.RateLimited` (HTTP 503,
`{"error": "rate_limited", "retry_after_s": ...}`) is re-exported from `pitwall.rate_limits` for callers.

### `pitwall.rate_limits.retry_after`

`parse_retry_after(value, *, now=None, max_delay_s=60.0) -> float | None` turns a `Retry-After`
header into bounded seconds. It accepts delay-seconds or an HTTP-date, clamps the result to
`[0, max_delay_s]`, and returns `None` for a missing, empty, malformed, or non-finite value so the
caller falls back to its own retry schedule. `DEFAULT_MAX_RETRY_AFTER_DELAY_S` is `60.0`; each RunPod
client accepts a `max_retry_after_s` constructor argument that defaults to it.

---

## 3. Token-Bucket Algorithm

A bucket holds up to `capacity` tokens and refills at `capacity / refill_window_s` per second, so a
full window refills an empty bucket. A request consumes one token; when none is available the caller
learns the wait from `retry_after_s`.

---

## 4. Inbound request rate limiting (on by default)

This is caller-facing HTTP rate limiting. `InboundRateLimitMiddleware` is registered on the FastAPI
app with the parsed limit and the API token, and runs outermost in the request order. See
[02 — REST API](02-api-rest.md) for the full middleware order (`pitwall.api.app`).

`PITWALL_INBOUND_RATE_LIMIT` defaults to `120/60s`. `off`, `disabled`, or `none` explicitly disables
it for loopback development. Invalid syntax is a configuration error and exits fail-closed instead of
silently disabling abuse control (`pitwall.config:parse_rate_limit`).

Public health and probe paths pass through without consuming tokens. The value format is
`<requests>/<window>`, for example `60/60s`: `requests` becomes bucket capacity, the parsed window
becomes `refill_window_s`, and each admitted request consumes one token.

Client buckets are keyed by `(client_ip, token_digest)`. The IP is `scope["client"][0]` or
`"unknown"`; `token_digest` is the SHA-256 digest of a matching bearer token only when
`PITWALL_API_TOKEN` is configured and the presented token matches. Otherwise the token component is
`None`.

When the bucket is exhausted the middleware returns HTTP 429 with body `{"detail": "rate limit
exceeded"}` and a `Retry-After` header equal to `ceil(bucket.retry_after_s(1.0))`, floored at one
second. This header is sent to Pitwall callers; it is not the outbound RunPod `Retry-After` handled by
`parse_retry_after`. The middleware never raises `RateLimited`; it writes the 429 response directly.

---

## 5. Public Interfaces

From `pitwall.rate_limits`: `DEFAULT_MAX_RETRY_AFTER_DELAY_S`, `REFILL_WINDOW_S`, `RateLimited`,
`TokenBucket`, `parse_retry_after`, `refill_tokens`, and `seconds_until_available`.

---

## 6. Configuration

The token-bucket arithmetic reads no environment variables; its tuning constants are Python values in
`pitwall.rate_limits.algorithm` and `pitwall.rate_limits.retry_after`.

| Constant | Value | Used by |
|---|---|---|
| `REFILL_WINDOW_S` | `10.0` | `refill_tokens`, `TokenBucket`, `seconds_until_available` |
| `DEFAULT_MAX_RETRY_AFTER_DELAY_S` | `60.0` | `parse_retry_after` default; the RunPod clients' `max_retry_after_s` |

Callers set their own limits: `PITWALL_INBOUND_RATE_LIMIT` for the REST edge (default `120/60s`) and
`PITWALL_GATEWAY_RATE_LIMIT_RPM` for the gateway (default 120).

---

## 7. Failure Modes & Edge Cases

- Invalid arguments raise `ValueError` (section 2).
- `tokens_needed > capacity`: `seconds_until_available` returns `math.inf`.
- `parse_retry_after` on a malformed HTTP date returns `None`.
- `RateLimited` (503) is the error envelope for callers that report a local admission failure:
  `exc.to_response_body()` is `{"error": "rate_limited", "retry_after_s": 10.0}`.

---

## 8. Testing

| File | What it covers |
|---|---|
| `tests/rate_limits/test_bucket_algorithm.py` | Refill, `seconds_until_available`, `TokenBucket`, and `parse_retry_after` |
| `tests/property/test_token_bucket_properties.py` | Hypothesis invariants: refill never exceeds capacity and is monotone in elapsed time, `try_consume` is all-or-nothing, and `retry_after_s` is zero exactly when enough tokens exist |
| `tests/test_api_security_middleware.py` (`test_inbound_rate_limit_returns_429_after_threshold`, `test_inbound_rate_limit_excludes_health_routes`, `test_inbound_rate_limit_keys_by_bearer_token_when_set`, `test_invalid_inbound_rate_limit_fails_closed`) | Inbound limiter: threshold returns 429 with `Retry-After`, health routes bypass it, bearer-token keying, fail-closed parsing |
| `tests/gateway/test_auth.py` | The gateway's per-token bucket |

---

## 9. Dependencies

- `pitwall.api.exceptions` — `RateLimited`.
- Standard library only otherwise: `dataclasses`, `datetime`, `email.utils.parsedate_to_datetime`,
  `math`, `time`.
- Callers: `pitwall.api.app`, `pitwall.gateway.auth`, `pitwall.webhook_receiver` (its per-client bucket), and
  `pitwall.runpod_client.retry` (via `parse_retry_after`).
