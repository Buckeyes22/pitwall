# T2-03 — The API's locks: auth, admin, rate limit

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T2-02 | **Output:** issues

## Why this matters

Authentication and rate limits stop strangers from spending the operator's money. A bearer
token that doesn't gate the data plane, an admin secret the API accepts without checking, or a
rate limiter that never trips, are all bypasses.

## Sources

- [User journeys J12, J13, J14](../../../docs/operator/user-journey-catalog.md)
- [Security model](../../../docs/sdlc/14-security.md)
- [REST API surface, route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory)
- [HTTP and status codes](../../concepts/http-and-status-codes.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). This mission
exercises the auth gates only — no `pitwall serve`, no live pods, no real RunPod calls. Use only
the README Quick Start placeholder values. A `200` where `401` or `403` is expected is
severity 1; stop and message the maintainer right away (rule R14).

## Setup

Run the tier 2 standard setup in terminal 1, with the five `export` lines from the README Quick
Start set:

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

In terminal 2 (same five `export` lines), start the API with the token on:

```bash
uv run pitwall-api 2>&1 | tee qa/.work/evidence/T2-03-api.txt
```

Expected: no other stack on `5444` or `6380`; setup finishes without errors; the API runs on
`127.0.0.1:8080`. Non-health routes need `Authorization: Bearer $PITWALL_API_TOKEN`. Admin
routes also need `-H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET"`.

## Steps

### Step 1 — Bearer token gate

Coach: with the token on, every non-health route must return `401` without the bearer and
`200` with it. `/healthz` must stay public.
Tester does, three times:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/v1/capabilities | tee qa/.work/evidence/T2-03-cap-no-bearer.txt
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/v1/capabilities | tee qa/.work/evidence/T2-03-cap-with-bearer.txt
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz | tee qa/.work/evidence/T2-03-healthz-public.txt
```

Expected: `401`, `200`, `200`.
If different: a `200` where `401` was expected is severity 1 — stop and message the maintainer
right away (rule R14). A `401` on `/healthz` is severity 3 (label `documentation`).

### Step 2 — Admin secret gate

Coach: admin routes have a second gate on top of the bearer: the admin secret in
`X-Pitwall-Secret`. Without it (or wrong), the call must fail closed.
Tester does, twice:

```bash
# No admin secret
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/admin/capabilities -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{}' | tee qa/.work/evidence/T2-03-admin-no-secret.txt
# Wrong admin secret
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/admin/capabilities -H "Authorization: Bearer $PITWALL_API_TOKEN" -H "X-Pitwall-Secret: $RANDOM" -H 'Content-Type: application/json' -d '{}' | tee qa/.work/evidence/T2-03-admin-wrong-secret.txt
```

Expected: `401` or `403` both times ([fail-closed admin](../../../docs/sdlc/14-security.md#3-fail-closed-admin)).
If different: a `200` or `201` is severity 1 — stop and file it with the maintainer right away (rule R14).

### Step 3 — Admin happy path

Coach: with the real admin secret, creating one returns `201` and an `id`; disable, enable,
and audit each return `200`.
Tester does:

```bash
# Create
curl -sX POST http://127.0.0.1:8080/v1/admin/capabilities -H "Authorization: Bearer $PITWALL_API_TOKEN" -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" -H 'Content-Type: application/json' -d '{"name":"embedding.qa","version":"1.0.0","class":"embedding","cost_mode":"per_request"}' | tee qa/.work/evidence/T2-03-admin-create.txt
# Disable, enable, audit on the id from the create
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/admin/capabilities/<id>/disable -H "Authorization: Bearer $PITWALL_API_TOKEN" -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" | tee qa/.work/evidence/T2-03-admin-disable.txt
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/admin/capabilities/<id>/enable -H "Authorization: Bearer $PITWALL_API_TOKEN" -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" | tee qa/.work/evidence/T2-03-admin-enable.txt
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/admin/audit-capability/embedding.demo -H "Authorization: Bearer $PITWALL_API_TOKEN" -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" | tee qa/.work/evidence/T2-03-admin-audit.txt
```

Expected: `201` for the create and `200` for each of disable, enable, audit. A non-`200` is
severity 2.

### Step 4 — Inbound rate limit

Coach: `PITWALL_INBOUND_RATE_LIMIT=3/60s` caps the data plane at three requests per minute.
After the third, the next call returns `429` with a `Retry-After` header.
In terminal 2, stop the API (Ctrl-C) and start it with the limit:

```bash
PITWALL_INBOUND_RATE_LIMIT=3/60s uv run pitwall-api 2>&1 | tee qa/.work/evidence/T2-03-api-rate-limited.txt
```

In terminal 1, send six requests, then check the header:

```bash
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "call=$i code=%{http_code}\n" -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/v1/capabilities; done | tee qa/.work/evidence/T2-03-rate-burst.txt
curl -s -D - -o /dev/null -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/v1/capabilities | grep -i retry-after
```

Afterwards, in terminal 2, stop it (Ctrl-C) and restart it normally with `uv run pitwall-api`.

Expected: the burst shows the first three calls `200` and the rest `429`; a `Retry-After`
line is printed.
If different: every call `200` (limiter off), every call `429` (limiter wrong), or no
`Retry-After` (severity 2, [rate-limit response](../../../docs/sdlc/14-security.md#5-inbound-rate-limiting)).

## What counts as a finding

- A `200` (or `201`) where `401` or `403` is expected — auth or admin bypass (severity 1).
- A `429` missing the `Retry-After` header (severity 2).
- A `200` on `/healthz` only with a bearer token (severity 3, label `documentation`).
- A non-`200` from disable, enable, or audit on the admin path (severity 2).
- Any other status code that differs from the
  [route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory) or
  [security model](../../../docs/sdlc/14-security.md).

## Done when

- All four steps have saved responses in `qa/.work/evidence/T2-03-*.txt`.
- Step 1 returned `401`, `200`, `200`. Step 2 returned `401` or `403` both times.
- Step 3 returned `201` and three `200`s.
- Step 4 returned three `200`s then `429`s, with a `Retry-After` header.
- Any bypass or missing header is filed with the tester using
  [bug reports](../../handbook/bug-reports.md). A severity 1 finding is also messaged to the
  maintainer right away (rule R14).

## Record in progress

Add a `Completed` row for T2-03 with output links to the evidence files and any issue numbers
filed. Set `Current item` to the next item the
[mission list](../../missions/README.md#mission-list) picks. End the session log with what the
tester learned and any open questions.
