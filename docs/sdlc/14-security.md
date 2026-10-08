# Security Model

Pitwall handles a credentialed outbound surface (RunPod API keys, paid GPU calls) and an
administrative control plane (audit, kill-switch). Its security posture is fail-closed boot,
defense-in-depth on the admin + webhook surfaces, and a static-analysis gate chain in CI. The
runtime controls below were authored find→fix (a test surfaced the weakness, then a paired fix
closed it) under the release program security track.

## 1. Trust boundaries

| Boundary | Threats | Control |
|----------|---------|---------|
| Inbound API (non-health routes) | unauthorized public API use | bearer gate: required for a non-loopback bind, optional on loopback |
| Inbound admin (`/v1/admin/*`) | unauthorized audit / kill-switch | fail-closed, constant-time shared-secret gate |
| Inbound API edge | brute-force / DoS / abusive callers | token-bucket limiter, on by default (`120/60s`) |
| Inbound webhook (`/webhooks/runpod`) | forged terminal-status deliveries | mandatory HMAC verification; the receiver refuses to start without its secret |
| Bare HTTP entrypoints | accidental network exposure | loopback bind by default |
| Outbound RunPod URL construction | SSRF via `runpod_endpoint_id` | strict allow-list at one chokepoint |
| Outbound webhook delivery | tampering / replay of our callbacks | timestamped HMAC signing |
| Error / health responses | secret disclosure | non-disclosure assertions |
| Dependencies & source | CVEs, hardcoded secrets, unsafe patterns | pip-audit / detect-secrets / bandit |

Self-hosted endpoint egress uses the existing outbound URL policy; the profile does not relax
allow-lists. `api_key_env` stores only an environment-variable name, and bearer values are never
persisted or returned.

All four static provider adapters follow the same credential-reference boundary. Persisted
providers carry a validated environment-variable name in `credential_ref`; adapters materialize
and validate its value only when an operation is invoked. Repository, REST, and GitOps provider
config writes reject credential-shaped values recursively after normalizing camel-case and common
separators at every nesting level. Migration 0028 removes legacy values from durable provider
config and redacts provider audit history. Provider repr omits config, model/API/MCP audit
serialization redacts legacy credential-shaped entries, and resolution errors include only the
adapter id, reference name, and invalid field paths. Header authentication and safe base-URL
validation remain provider-specific.

## 2. Fail-closed boot

The API calls `require_runtime_env("api")` at import and then reads `DATABASE_URL` and
`REDIS_URL` from `os.environ` (`pitwall.api.app`); `RUNPOD_API_KEY` is resolved at the first RunPod
operation. The shared config map requires all three vars for `reconciler` and `mcp`, `DATABASE_URL`
and `REDIS_URL` for `api`, only `DATABASE_URL` for `cost-exporter`, and has no import-time required vars for
`webhook` (`pitwall.config`). `require_runtime_env(...)`
prints names only and exits `os.EX_CONFIG` on config errors (`pitwall.config`). This boot gate is separate from
the fail-closed admin gate below.

## 3. Fail-closed admin

`AdminSecretMiddleware` is always installed on the FastAPI app, regardless of whether
`PITWALL_ADMIN_SECRET` is configured (`pitwall.api.app`,
`pitwall.api.app:AdminSecretMiddleware`). It gates `/v1/admin` and `/v1/admin/*`; if the secret is
unset, those paths return 401 before handlers run, with detail:
`admin routes disabled: PITWALL_ADMIN_SECRET is not configured`
(`pitwall.api.app`).
This is fail-closed admin auth, not fail-closed process boot.

## 4. Whole-API bearer token

`PITWALL_API_TOKEN` is an all-scopes operator credential. The API refuses a non-loopback bind
unless both it and `PITWALL_ADMIN_SECRET` are set (§6); on loopback it is optional, and the API logs
`API authorization disabled for loopback development` when no token or scoped token is configured.
When a token is configured,
`ApiTokenMiddleware` checks `Authorization: Bearer <token>` on every non-public-health path
and returns 401 `{"detail": "invalid or missing bearer token"}` plus
`WWW-Authenticate: Bearer` for a missing or bad token (`pitwall.api.app`). Surface mechanics stay in `02-api-rest.md`.

## 5. Inbound rate limiting

`PITWALL_INBOUND_RATE_LIMIT` is a REST-edge abuse control that is on by default: when the variable
is unset the API applies `120/60s` (120 requests per 60 seconds). Setting it to a blank value,
`off`, `disabled`, or `none` disables the limiter explicitly; an invalid value is a fatal
configuration error rather than a reason to disable it (`pitwall.api.app`,
`pitwall.config.parse_inbound_rate_limit`). When the bucket is exhausted, the middleware returns 429
`{"detail": "rate limit exceeded"}` with `Retry-After` (`pitwall.api.app`).
Token-bucket details stay in `11-rate-limiting.md`.

## 6. Private-by-default network binding

The three HTTP console scripts route to separate entrypoint modules (`pyproject.toml`). Their `main()` functions default the uvicorn host
to `127.0.0.1` and require an explicit host env var to bind elsewhere:
`PITWALL_API_HOST`, `PITWALL_WEBHOOK_HOST`, and `PITWALL_COST_EXPORTER_HOST`
(`pitwall.api.__main__`,
`pitwall.webhook_receiver.__main__`,
`pitwall.cost.__main__`).
This reduces default network exposure for bare `pitwall-api`, `pitwall-webhook`, and
`pitwall-cost-exporter` runs.

A non-loopback bind also needs credentials. At import, `pitwall-api` refuses a non-loopback
`PITWALL_API_HOST` unless `PITWALL_API_TOKEN` and `PITWALL_ADMIN_SECRET` are both set, and
`pitwall-webhook` refuses a non-loopback `PITWALL_WEBHOOK_HOST` unless `PITWALL_WEBHOOK_SECRET` is
set. Each exits `os.EX_CONFIG` and names the missing variables on stderr
(`pitwall.config.require_credentials_for_bind`). The explicit override
`PITWALL_UNSAFE_ALLOW_INSECURE_BIND=1` downgrades the refusal to a stderr warning; it is for
isolated development only.

## 7. Admin authentication — constant time

`AdminSecretMiddleware` (`src/pitwall/api/app.py`) gates every request whose path is `/v1/admin` or
starts with `/v1/admin/`. The supplied `X-Pitwall-Secret` header is compared to the configured
secret with `hmac.compare_digest`, **not** `!=` — so the gate leaks neither the secret's length nor
its prefix through response timing (`pitwall.api.app`).
Missing admin-secret behavior is covered by §3.

- Tests: `tests/security/test_admin_constant_time.py` pins the constant-time source (a find→fix
  static guard) + a through-the-stack table; `tests/security/test_admin_auth_surface.py` enumerates
  every `/v1/admin/*` route from `app.routes` (so future admin routes are auto-covered) and asserts
  401 without/with-wrong secret, non-401 with the right one.
- Note: auth is app-level middleware, not per-route — the bare-router audit tests (`tests/audit/`)
  therefore make no 401 assertion.

## 8. SSRF — provider-URL endpoint-id allow-list

`runpod_endpoint_id` is interpolated into outbound RunPod URLs as the **host label** for
`serverless_lb` (`https://{id}.api.runpod.ai`) and into the **path** for `serverless_queue`
(`https://api.runpod.ai/v2/{id}`). Unvalidated, a hostile id could redirect Pitwall's own
credentialed calls (host-label injection, path traversal, userinfo/port injection, or the cloud
metadata IP `169.254.169.254`).

Control: an allow-list regex `^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$` is enforced at the single chokepoint
`resolver/provider_urls.py::_require_endpoint_id`, through which **every** URL builder funnels. The
charset bans `. / : @` and whitespace by construction. Prefer extending the charset over relaxing
it; never interpolate an unvalidated id into an outbound URL.

- Tests: `tests/security/test_provider_url_ssrf.py` drives hostile ids through `lb_url`/`queue_url`
  (raise `ValueError`) plus an allow-list host invariant (`*.api.runpod.ai` only) that stays green
  before and after the fix. See `04-routing.md`.

## 9. Inbound webhook authentication — mandatory HMAC

The RunPod receiver requires `PITWALL_WEBHOOK_SECRET`: its lifespan calls
`require_webhook_secret()` and raises when no secret is configured, so the service refuses to start
(`tests/webhook_receiver/test_startup.py`). Every inbound delivery must then carry a valid
`X-Pitwall-Webhook-Signature`, verified through the **outbound dispatcher's** signer
(`webhook_dispatcher.signer.verify` — constant-time `compare_digest` + a 300s replay window).
Reusing the proven verifier avoids a second HMAC implementation. `PITWALL_WEBHOOK_PREVIOUS_SECRETS`
(a JSON list) keeps earlier secrets valid during rotation. Invalid/missing signature → 401
`{"ok": false, "detail": "invalid or missing webhook signature"}`.

- Tests: `tests/security/test_webhook_receiver_unauthenticated.py` pins that the route handler itself
  accepts an unsigned POST when no secret is loaded (the app is built without its lifespan, which is
  where startup refuses); `tests/security/test_webhook_receiver_signed.py` drives
  accept / missing / wrong-secret / tampered-body / replayed-stale-timestamp plus a static guard
  that the route delegates to the shared verifier. See `09-webhooks.md`.

## 10. Outbound webhook signing

Outbound deliveries are signed with a timestamped HMAC-SHA256 scheme
(`webhook_dispatcher/signer.py`): the signed message is `{timestamp}.{body}`, the header is
`t={ts},v1={hexdigest}`, and `verify` enforces a 300s `max_age` (replay window) with a constant-time
`compare_digest`. See `09-webhooks.md`.

## 11. Secret non-disclosure

Secret *values* must never appear in env-validation errors, the `/health` surface, or error
envelopes. `tests/security/test_*` assert that boot-failure messages name the missing
variable but never echo a value, and that health/error responses carry no secret material.

### Pre-spend payload inspection

The shared pre-spend scanner is a bounded service rather than transport logic. Its default
`balanced` policy blocks high-confidence secret shapes and safely redacts supported PII (complete
email addresses and valid-form US Social Security numbers). `PITWALL_PRE_SPEND_MODE` selects
`balanced`, all-`block`, or safe-`redact`; invalid values fail typed settings validation. Binary,
opaque, null-byte, over-limit,
too-deep, and timed-out inputs remain fail-closed in every mode. The explicit rule catalogue carries
descriptions and actions, never captured text (`pitwall.security.pre_spend`).

One inspection is capped at 256 KiB, 32 findings, 32 levels, 4,096 values, and 50 ms of scan CPU time by default
(`pitwall.security.pre_spend`). Findings contain a
redacted marker, sanitized path, rule id, action, and a rule/location compatibility fingerprint; the
fingerprint is never derived from matched content, and the shared semantic result contains no
original match (`pitwall.security.pre_spend`). Process-local counters and last-decision metadata are
updated under a lock for enforced requests. Preview calls use the exact scanner but perform no
provider, database, audit, counter, or last-decision write (`pitwall.security.pre_spend`).

The audit harness and existing inference route retain their established import and result shapes by
delegating to this bounded implementation. Request-path integration must call the stateful service
before budget reservation or provider egress; the compatibility helper is only for existing callers
while those paths converge (`pitwall.security.pre_spend`). Adversarial coverage includes
secret fields and tokens, explicit bounded base64, PII, Unicode, nested tool/header/environment
payloads, opaque multipart bytes, null bytes, limits, false positives, concurrency, and preview
non-mutation (`tests/security/test_pre_spend_inspection.py`).

Field-name matching normalizes camel case and realistic separators at every nesting level, so
names such as `apiKey`, `clientSecret`, `access-key`, and `private key` cannot bypass the secret
field rule. Current provider-spend seams apply the stateful service before I/O: synchronous
inference capability payloads; the complete asynchronous MCP submission including webhook and
idempotency metadata; model-serve requests; lease payload/environment plus JSON-normalized stored
provider launch metadata; all 16 RP-02 account-resource mutations; and bounded RP-04 object/log
requests. RP-02 registry inspection sees only a credential reference, with the password value
materialized later at the strict boundary. RP-04 scans decoded UTF-8 upload content, blocks opaque
binary or scanner-oversized content, and inspects log request metadata but not provider responses.

The current cross-surface audit and remaining serialized integration requirements are recorded in
[`2026-09-01-gov-04-entry-path-audit.md`](../evidence/2026-09-01-gov-04-entry-path-audit.md).
Application-wide FastAPI validation errors answer through one non-reflecting handler
(`pitwall.api.app.request_validation_exception_handler`), and broker MCP tool errors through
`pitwall.mcp.safe_boundary` on the MCP SDK 2 `MCPServer`; OpenAI query/header egress and legacy
top-level CLI commands must still converge without transport-local scanners.

The registered `GET /v1/guardrails`, `POST /v1/guardrails/preview`,
`pitwall_guardrail_status`, `pitwall_guardrail_preview`, and `guardrails status|preview` CLI
surfaces delegate to the same process-local service model. The existing Operations TUI renders the
complete rule catalogue, counters, last-decision metadata, and a cleared-after-submit JSON preview.
CLI/TUI preview JSON is byte-bounded before parsing; all preview surfaces leave counters and last
decision unchanged.

### RunPod v2 outbound controls

RunPod control-plane authentication is header-only. Resource-specific extra-forbid request models
reject unknown or legacy field names before network I/O; response bodies are type/envelope checked
before use. Provider errors are capped at 4096 characters and redacted with the request credential
before logging or wrapping. URLs never contain credentials.

Safe GET/DELETE/PATCH calls and explicit 429 rejections receive at most one immediate retry. A v2
create is not retried after an ambiguous transport or server failure because doing so could create
duplicate paid resources. Hermetic RunPod tests install a DNS guard for `api.runpod.io` and
`rest.runpod.io`; any fake-transport escape fails the test before a provider connection.
See [ADR 0006](../decisions/0006-runpod-rest-v2-control-plane.md).

## 12. API fuzzing

`tests/security/test_schemathesis_fuzz.py` (Schemathesis 4.x) drives every current OpenAPI
operation with schema-derived and malformed input and asserts `not_a_server_error`. The fuzz app
configures an admin secret and turns the inbound rate limiter off, so requests reach their
handlers instead of a 429. Two passes run:

- The public pass sends no `X-Pitwall-Secret`. Secret-gated `/v1/admin/*` operations are fuzzed
  on their missing- and wrong-secret 401 path.
- The authenticated pass sends the secret to all 37 secret-gated `/v1/admin/*` operations.
  RunPod GET reads under `/v1/admin/runpod/` skip the secret by design, so the public pass covers
  them.

Handlers run against hermetic doubles. The asyncpg double echoes inserts and finds a stored
provider or capability for half of all keys. It refuses NUL and lone-surrogate parameters the way
Postgres and asyncpg do. The real RunPod control-plane service runs over an in-memory backend.
Onboarding, volume files, market, and routing use service doubles. The kill switch is patched
off the global pool, Tailscale, and RunPod. No operation is excluded. See
`17-testing-strategy.md`.

## 13. Static-analysis gate chain (CI)

| Gate | Tool | Policy | Make target |
|------|------|--------|-------------|
| SAST | bandit | MEDIUM/MEDIUM (`-ll -ii`); accepted findings in `tools/security/bandit-baseline.json` (triaged, one bullet each in `tools/security/README.md`) | `make sec` |
| Dependency CVEs | pip-audit | audits the locked env; ignores require a dated justification in `tools/security/pip-audit-ignore.txt` (bump-by-default) | `make sec` |
| SAST (registry rules) | Semgrep | `p/python` + `p/security-audit` registry rulesets plus repo-local `tools/security/semgrep.yml` against `src/pitwall`; triaged under the same no-inline-suppression policy as bandit | `make sec-semgrep` |
| License policy | `tools/security/check_licenses.py` | installed runtime dependency graph checked against `tools/security/license-policy.json` allow/deny lists; anything else must be a reviewed `review_required_packages` entry | `make sec` |
| Secrets | detect-secrets | scans against `.secrets.baseline`; baseline drift fails the hook | (pre-commit + CI) |

**No inline suppressions:** `# nosec` (bandit), `# noqa` (ruff), `# type: ignore` (mypy) are banned
by `tools/guards/python_policy.py` unless they carry both a rule code and a `# reason:`; `except
Exception` needs a trailing `# reason:`. Accepted SAST findings live in the baseline, not inline.

## 14. Operational security

The kill-switch (`15-operations.md`) severs Tailscale ACL → tagged devices → RunPod compute in
<30s and persists every activation to `pitwall.kill_log`. It is reachable only through the
constant-time-gated `/v1/admin/kill-switch` route (and the CLI).

## 15. Configuration summary

| Env var | Effect |
|---------|--------|
| `DATABASE_URL` / `REDIS_URL` | required for the API core boot path (`pitwall.api.app`); `RUNPOD_API_KEY` is required by the reconciler and MCP boot and resolved at the API's first RunPod operation |
| `PITWALL_ADMIN_SECRET` | configures admin auth; unset makes `/v1/admin/*` return 401 (`pitwall.api.app`) |
| `PITWALL_API_TOKEN` | whole-API bearer gate; with `PITWALL_ADMIN_SECRET`, required for a non-loopback API bind (`PITWALL_UNSAFE_ALLOW_INSECURE_BIND=1` overrides with a warning) and optional with a warning on loopback |
| `PITWALL_INBOUND_RATE_LIMIT` | defaults to `120/60s` when unset; blank, `off`, `disabled`, or `none` disables it; exhausted buckets return 429 + `Retry-After`; invalid configuration fails startup |
| `PITWALL_API_MAX_BODY_BYTES` | defaults to 8 MiB and bounds fixed-length and chunked request bodies before route execution |
| `PITWALL_WEBHOOK_SECRET` | required: the receiver refuses to start without it, and every inbound `/webhooks/runpod` delivery must carry a valid HMAC (`pitwall.webhook_receiver`); also required for a non-loopback webhook bind |
| `PITWALL_API_HOST` / `PITWALL_WEBHOOK_HOST` / `PITWALL_COST_EXPORTER_HOST` | override loopback bind defaults for `pitwall-api`, `pitwall-webhook`, and `pitwall-cost-exporter` (`pitwall.api.__main__`, `pitwall.webhook_receiver.__main__`, `pitwall.cost.__main__`) |

See `tools/security/README.md` for the triage playbook and `17-testing-strategy.md` §4 for the full
security test inventory.
