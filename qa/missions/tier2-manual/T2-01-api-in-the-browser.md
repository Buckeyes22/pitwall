# T2-01 — The REST API in a browser

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T1-02 | **Output:** issues

## Why this matters

The Swagger page is how people explore the API. It must list the real routes and let a tester
click "Try it out" to send requests. A page that hides routes, or returns the wrong status code
on a request, hides bugs from anyone who only uses the browser.

## Sources

- [User journey catalog, J07](../../../docs/operator/user-journey-catalog.md)
- [REST API surface, route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory)
- [Failure modes and error types](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types)
- [HTTP and status codes](../../concepts/http-and-status-codes.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). The API runs
on loopback only with bearer auth off and logs a warning. Never expose it to a non-loopback
address. Use only the README Quick Start placeholder values.

## Setup

Run the tier 2 standard setup in terminal 1, with the five `export` lines from the README Quick
Start set:

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

Expected: no other clone's stack on `5444` or `6380`; both services started; both `uv run`
commands finish without errors. In terminal 2 (same five `export` lines), start the API with the
token removed, because with a token set even the docs page needs one (step 5 shows this):

```bash
env -u PITWALL_API_TOKEN uv run pitwall-api 2>&1 | tee qa/.work/evidence/T2-01-api-no-auth.txt
```

Expected: the API runs on `127.0.0.1:8080` and logs a warning that bearer auth is off on loopback.

## Steps

### Step 1 — Open the docs page

Coach: tell the tester that `/docs` is the Swagger UI. It must list every J07 route group.
Tester does: open `http://127.0.0.1:8080/docs` in a browser.
Expected: the Swagger page loads and lists the API's routes, grouped by area.
If different: the page is blank, returns `401`, or is missing groups. The most common cause is
starting the API without `env -u PITWALL_API_TOKEN`; restart as in Setup.

### Step 2 — Try each J07 route from the browser

Coach: walk the tester through the J07 list one path at a time. Click "Try it out" then
"Execute". Save each response.
Tester does, for each path: GET the path in Swagger and copy the status code into the notes
file — `/healthz`, `/health`, `/v1/health`, `/v1/capabilities`, `/v1/capabilities/embedding.demo`,
`/v1/providers`, `/v1/providers/prov_demo_runpod_lb`, `/v1/providers/prov_demo_runpod_lb/health`,
and `/openapi.json`.
Expected: `200` on every call. The bodies match the
[route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory); `/v1/health` reports
postgres and redis state, `/v1/capabilities` lists at least `embedding.demo`, and `/v1/providers`
includes `prov_demo_runpod_lb`.
If different: a path is missing from Swagger, returns a non-`200` status, or the body lacks a
field the inventory names. File it with the tester using
[bug reports](../../handbook/bug-reports.md).

### Step 3 — POST /v1/inference (happy path)

Coach: the `dry_run` flag stops the request from spending money. The plan fields prove routing
worked.
Tester does, in Swagger on `POST /v1/inference`, with body
`{"capability":"embedding.demo","texts":["hello"],"dry_run":true}`:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/inference \
  -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}' \
  | tee qa/.work/evidence/T2-01-inference-happy.txt
```

Expected: `200`. The response contains `result.dry_run` equal to `true`, and
`result.plan.selected_provider_id` equal to `prov_demo_runpod_lb` (what journey J01 checks).
If different: file a finding. A wrong provider id means the route plan picked the wrong provider.

### Step 4 — POST /v1/inference (unknown capability)

Coach: an unknown capability must come back as `404` with a structured JSON error.
Tester does, in Swagger on `POST /v1/inference`, with body `{"capability":"does.not.exist"}`:

```bash
curl -s -i -X POST http://127.0.0.1:8080/v1/inference \
  -H 'Content-Type: application/json' \
  -d '{"capability":"does.not.exist","texts":["x"],"dry_run":true}' \
  | tee qa/.work/evidence/T2-01-inference-404.txt
```

Expected: `HTTP/1.1 404` and a JSON body whose `error` field matches the failure-modes section
([failure modes](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types) lists
`CapabilityNotFound` → 404).
If different: a different status code, or a body without the `error` envelope, is a finding.

### Step 5 — Reload /docs with bearer auth on

Coach: this is the docs-page trap. When `PITWALL_API_TOKEN` is set, every non-health route needs
a bearer token, including `/docs` and `/openapi.json`. A README follower would be confused.
Tester does: stop the API in the second terminal (Ctrl-C), then start it again normally:

```bash
uv run pitwall-api 2>&1 | tee qa/.work/evidence/T2-01-api-with-auth.txt
```

Then reload `http://127.0.0.1:8080/docs` in the browser.
Expected: `401` with `{"detail":"invalid or missing bearer token"}`. The docs page is behind
the bearer gate.
If different: the page loads without a token, contradicting
[the docs-page trap](../../coach/rules.md#safety). File a finding with `found-by-qa` and
`severity:1-critical`. Then confirm the README Quick Start's note after "start the API" still
tells a README follower that `/docs` needs the token once it is exported.

## What counts as a finding

- A status code that differs from the
  [route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory) or
  [failure modes](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types).
- An error body whose shape differs from `{"error": "<code>", ...}`.
- Any `500` (severity 2).
- `/docs` or `/openapi.json` reachable without a bearer token when `PITWALL_API_TOKEN` is set
  (severity 1).
- A README or doc that tells the tester to open `/docs` after exporting `PITWALL_API_TOKEN`
  without mentioning the token gate (severity 3, label `documentation`).

## Done when

- Every J07 route returned `200` from Swagger, with bodies that match the route inventory.
- The two `POST /v1/inference` calls returned the expected status codes and bodies.
- With `PITWALL_API_TOKEN` set, `/docs` returned `401`, and any README-gap finding was filed.
- Every output is saved in `qa/.work/evidence/T2-01-*.txt`.

## Record in progress

Add a `Completed` row for T2-01 with output links to the evidence files and any issue numbers
filed. Set `Current item` to [T2-02 — The REST API with curl](T2-02-api-with-curl.md). End the
session log with what the tester learned and any open questions.
