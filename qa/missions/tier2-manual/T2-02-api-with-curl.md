# T2-02 — The REST API with curl

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T2-01 | **Output:** issues

## Why this matters

Scripts and programs use the API from the command line and depend on the exact status codes.
A wrong code, or a body that drifted from the docs, makes automation fail silently or crash.

## Sources

- [User journeys J01, J08, J17](../../../docs/operator/user-journey-catalog.md)
- [Route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory) and [failure modes](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types)
- [HTTP and status codes](../../concepts/http-and-status-codes.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). The API runs
on loopback with the bearer token on. Use only the README Quick Start placeholder values.

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
uv run pitwall-api 2>&1 | tee qa/.work/evidence/T2-02-api.txt
```

Expected: no other stack on `5444` or `6380`; setup finishes without errors; the API runs on
`127.0.0.1:8080`. Non-health routes need `Authorization: Bearer $PITWALL_API_TOKEN`.

## Steps

### Step 1 — README Quick Start curl (happy path)

Coach: the plan fields prove routing worked; read aloud `dry_run` and `selected_provider_id`.
Tester does:

```bash
curl -sX POST http://127.0.0.1:8080/v1/inference \
  -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}' \
  | tee qa/.work/evidence/T2-02-inference-happy.txt
```

Expected: the body contains `"dry_run":true` and `"selected_provider_id":"prov_demo_runpod_lb"`
(the API returns compact JSON, with no spaces).
If different: file a finding. A wrong provider id means the route plan picked the wrong
provider; a missing `dry_run` field means the envelope has drifted from the docs.

### Step 2 — Health check without a bearer

Coach: `/healthz` must stay reachable without a bearer token even when bearer auth is on
([public health paths](../../../docs/sdlc/02-api-rest.md#3-route-inventory)).
Tester does:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz \
  | tee qa/.work/evidence/T2-02-healthz.txt
```

Expected: `200`. No token is needed.
If different: the health path now requires a token, contradicting the route inventory. File a
finding.

### Step 3 — Unknown capability

Coach: an unknown capability must come back as `404` with a structured JSON error.
Tester does:

```bash
curl -siX POST http://127.0.0.1:8080/v1/inference \
  -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"capability":"does.not.exist","texts":["x"],"dry_run":true}' \
  | tee qa/.work/evidence/T2-02-inference-404.txt
```

Expected: `HTTP/1.1 404` and a JSON body whose `error` field matches the failure-modes section
([failure modes](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types) lists
`CapabilityNotFound` → 404).
If different: a different status code, or a body without the `error` envelope, is a finding.

### Step 4 — Malformed body

Coach: a body that misses the required `capability` field must come back as `422`.
Tester does:

```bash
curl -siX POST http://127.0.0.1:8080/v1/inference \
  -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"nope":1}' \
  | tee qa/.work/evidence/T2-02-inference-422.txt
```

Expected: `HTTP/1.1 422`. The body has a structured validation error, not a `500`.
If different: a `500` instead of `422` is severity 2.

### Step 5 — Unknown workload id on each job route

Coach: the three job routes must each return `404` with a structured error for an unknown workload.
Tester does, three times:

```bash
curl -siX GET http://127.0.0.1:8080/v1/jobs/wkl_doesnotexist/status -H "Authorization: Bearer $PITWALL_API_TOKEN" | tee qa/.work/evidence/T2-02-jobs-status-404.txt
curl -siX GET http://127.0.0.1:8080/v1/jobs/wkl_doesnotexist/result -H "Authorization: Bearer $PITWALL_API_TOKEN" | tee qa/.work/evidence/T2-02-jobs-result-404.txt
curl -siX POST http://127.0.0.1:8080/v1/jobs/wkl_doesnotexist/cancel -H "Authorization: Bearer $PITWALL_API_TOKEN" | tee qa/.work/evidence/T2-02-jobs-cancel-404.txt
```

Expected: `HTTP/1.1 404` for each call, with a JSON error body that matches the failure-modes
section (`WorkloadNotFound` → 404).
If different: any call returning a status other than `404`, or a body without the `error`
envelope, is a finding.

### Step 6 — Compare error bodies to the failure-modes table

Coach: read the failure-modes table aloud
([failure modes](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types)). For each error body from steps 3, 4, and 5, ask: does the `error` field match the table?
Tester does: paste the body and the matching row from the table into the notes file.
Expected: every body matches the table. The envelope is `{"error": "<code>", ...}`.
If different: a wrong status code, an extra or missing field, or a non-JSON body is a finding.

## What counts as a finding

- A status code that differs from the [route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory)
  or [failure modes](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types).
- An error body whose shape differs from `{"error": "<code>", ...}`.
- Any `500` (severity 2).
- `/healthz` (or another public health path) reachable only with a bearer token (severity 3,
  label `documentation`).

## Done when

- All six steps have a saved response in `qa/.work/evidence/T2-02-*.txt`.
- Every error body matches the failure-modes table.
- Any drift is filed with the tester using [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-02 with evidence links and issue numbers. Set `Current item` to
[T2-03 — The API's locks](T2-03-api-locks.md). End the session log with what the tester learned.
