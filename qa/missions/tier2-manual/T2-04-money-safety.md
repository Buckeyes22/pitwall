# T2-04 — Money safety: budget gate, proxy, kill switch, guardrails

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T2-03 | **Output:** issues

## Why this matters

Four gates stand between a request and a paid provider call. The
[budget gate](../../../docs/sdlc/05-cost-budget.md) must refuse an overspending request before any
provider call. The [OpenAI proxy](../../../docs/sdlc/02-api-rest.md#3-route-inventory) must block
URL-injection paths before any upstream call. The [kill switch](../../concepts/kill-switch.md) must
accept an admin request only with the admin secret.
[Guardrails](../../concepts/cost-budget-and-guardrails.md) must allow, redact, or block payloads
without writing anywhere.

## Sources

- [User journeys J15, J16, J21](../../../docs/operator/user-journey-catalog.md), coded in
  [the journey harness](../../../scripts/release/run-user-journeys.sh);
  [API failure modes](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types)
- [`guardrails status` / `guardrails preview`](../../../docs/sdlc/18-cli.md#guardrails-status--guardrails-preview)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Only the README
Quick Start placeholder values. The API stays on loopback. Steps 1 and 2 send non-dry-run requests
on purpose with a near-zero budget; this is the only mission in tiers 1 through 4 that does this.
The kill-switch drill always sends `"terminate_compute": false`. Step 3's `169.254.169.254` is
never contacted: Pitwall must refuse the path first.

## Setup

Run the tier 2 standard setup, with the five `export` lines from the README Quick Start in every
terminal. Do not start the API yet.

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
uv run pitwall db migrate
uv run pitwall init --non-interactive
mkdir -p qa/.work/evidence
```

Expected: no other clone's Postgres on `5444` or Redis on `6380`; both services started; both
`uv run` commands finished without errors.

## Steps

### Step 1 — Budget gate refuses the inference call

Coach: terminal 2 runs the API with a one-millionth-of-a-dollar monthly budget and stays busy.
Tester does, in terminal 2:

```bash
PITWALL_MONTHLY_BUDGET_USD=0.000001 uv run pitwall-api 2>&1 | tee qa/.work/evidence/T2-04-budget-api.txt
```

Then in terminal 1:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz
curl -s -i -X POST http://127.0.0.1:8080/v1/inference -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{"capability":"embedding.demo","texts":["hello"]}' | tee qa/.work/evidence/T2-04-budget-inference.txt
```

Expected: `200` (`000` means the API is still starting: wait and rerun), then `HTTP/1.1 402
Payment Required` and a body with `"error":"budget_rejected"`, `"reason":"monthly_budget"`, and a
`snapshot` whose `estimate_usd` is larger than its `monthly_budget_usd` of `"0.000001"`. Any other
status, especially a provider error, is severity 1.

### Step 2 — Budget gate refuses the OpenAI proxy too

Tester does, in terminal 1:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/openai/embedding.demo/v1/chat/completions -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{"model":"m","messages":[{"role":"user","content":"hi"}]}' | tee qa/.work/evidence/T2-04-budget-proxy-code.txt
```

Expected: `402` on its own line. Any other code is severity 1.

### Step 3 — Proxy rejects the URL-injection path

Coach: J16 asks the proxy to forward to a full URL hidden inside the path. `169.254.169.254` is
the cloud metadata address that hands out machine credentials; a proxy that follows the path there
leaks them. This is a server-side request forgery (SSRF) test.
Tester does, in terminal 1:

```bash
grep -n -A4 '^j16()' scripts/release/run-user-journeys.sh | tee qa/.work/evidence/T2-04-j16-source.txt
curl -s -i -X POST "http://127.0.0.1:8080/v1/openai/embedding.demo/v1/https://169.254.169.254/latest/meta-data" -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{}' | tee qa/.work/evidence/T2-04-proxy-ssrf.txt
```

Expected: the grep shows the same path, then `HTTP/1.1 400 Bad Request` with `invalid_proxy_path`.

### Step 4 — Guardrails: status, allow, block, redact, malformed JSON

Coach: guardrails inspect a payload without writing anywhere. The secret here is made up and
matches the shape of an OpenAI key; it lives only in `qa/.work/notes/`.
Tester does, in terminal 1:

```bash
mkdir -p qa/.work/notes && printf '%s' '{"texts":["sk-made-up-fake-value-for-qa-only"]}' > qa/.work/notes/guardrail-secret.json
uv run pitwall guardrails status 2>&1 | tee qa/.work/evidence/T2-04-guardrails-status.txt
uv run pitwall guardrails preview --payload '{"texts":["hello"]}' 2>&1 | tee qa/.work/evidence/T2-04-guardrails-allow.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall guardrails preview --payload "$(cat qa/.work/notes/guardrail-secret.json)" 2>&1 | tee qa/.work/evidence/T2-04-guardrails-secret.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall guardrails preview --payload '{"texts":["jane.doe@example.com"]}' 2>&1 | tee qa/.work/evidence/T2-04-guardrails-email.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall guardrails preview --payload '{bad' 2>&1 | tee qa/.work/evidence/T2-04-guardrails-bad-json.txt; echo "exit=${PIPESTATUS[0]}"
```

Expected: `Guardrails: balanced | ...` and a `Guardrail rules` table. Then, each with `exit=0`:
`Guardrail preview: allow | findings 0`; `block | findings 1` from rule `openai_style_token`;
`redact | findings 1` from rule `email`. Last, `payload must be valid JSON` and `exit=2`. `allow`,
`redact`, and `block` are successful previews, not CLI failures
([concept card](../../concepts/cost-budget-and-guardrails.md)).

### Step 5 — Kill-switch drill, last

Coach: the drill records a kill in the database, and the kill switch stays engaged until the
database is wiped. That is why it runs last and ends with `down -v`.
Tester does: in terminal 2, press Ctrl-C, then restart the API with the normal budget by running
`uv run pitwall-api 2>&1 | tee qa/.work/evidence/T2-04-kill-api.txt`. Then in terminal 1:

```bash
curl -s -i -X POST http://127.0.0.1:8080/v1/admin/kill-switch -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'Content-Type: application/json' -d '{"reason":"qa drill"}' | tee qa/.work/evidence/T2-04-kill-no-secret.txt; echo
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/v1/admin/kill-switch -H "Authorization: Bearer $PITWALL_API_TOKEN" -H 'X-Pitwall-Secret: not-the-secret' -H 'Content-Type: application/json' -d '{"reason":"qa drill"}' | tee qa/.work/evidence/T2-04-kill-wrong-secret.txt
curl -s -X POST http://127.0.0.1:8080/v1/admin/kill-switch -H "Authorization: Bearer $PITWALL_API_TOKEN" -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" -H 'Content-Type: application/json' -d '{"reason":"qa drill","terminate_compute":false}' | tee qa/.work/evidence/T2-04-kill-report.json; echo
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz | tee qa/.work/evidence/T2-04-kill-healthz.txt
```

Expected: `HTTP/1.1 401 Unauthorized` with `invalid or missing X-Pitwall-Secret`; `401` for the
wrong secret; a report with `triggered_at`, `"reason":"qa drill"`, `"pods_terminated":0`,
`total_duration_ms`, and `"errors":[]`; `/healthz` `200`. Last, press Ctrl-C in terminal 2, then in
terminal 1 run `docker compose -f docker-compose.testinfra.yml down -v`; the `-v` wipes the kill
record. The next mission's setup starts the stack again.

## What counts as a finding

- A status or exit code that differs from these steps; any `500` (severity 2).
- The proxy returning `2xx` or `5xx` to the URL-injection path (severity 1).
- The kill switch accepting a call with a missing or wrong secret (severity 1).
- A secret payload that previews as `allow` (severity 1).

## Done when

- Every step's evidence is saved under `qa/.work/evidence/T2-04-*`, and the stack is down with `-v`.
- Every mismatch is filed using [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-04 with output links to the evidence files and any issues filed. Set
`Current item` to [T2-05 — The CLI off the happy path](T2-05-cli-off-the-happy-path.md). End the
session log with what the tester learned and any open questions.
