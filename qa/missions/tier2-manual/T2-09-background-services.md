# T2-09 — Background services

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T2-03 | **Output:** issues

## Why this matters

The webhook receiver, reconciler, and cost exporter run alongside the API. They fail quietly
unless someone checks them. A webhook that accepts unsigned deliveries, a reconciler that
crashes on a bad DSN, or a cost exporter with missing metrics are findings — some severity 1.

## Sources

- [User journeys J18, J19, J20](../../../docs/operator/user-journey-catalog.md)
- [Webhooks](../../../docs/sdlc/09-webhooks.md), [reconciler](../../../docs/sdlc/10-reconciler-lifecycle.md), and [observability](../../../docs/sdlc/13-observability.md)
- [Background services](../../concepts/background-services.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). The webhook
secret is a made-up value generated for this test; do not reuse a real one. The reconciler
worker is launched with `timeout` so the test always observes the documented exit code.

## Setup

Run the tier 2 standard setup. Check the test stack is free, start it, set the five `export`
lines from the README Quick Start in every terminal, and run migrations plus the seed loader.

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

Expected: no other clone's Postgres on `5444` or Redis on `6380`; both compose services started;
both `uv run` commands finished without errors.

## Steps

### Step 1 — Generate a webhook secret

The receiver will not start without `PITWALL_WEBHOOK_SECRET`, and it rejects every unsigned delivery. In terminal 1, once:

```bash
openssl rand -hex 16 > qa/.work/notes/webhook-secret.txt
```

Then in both terminals:

```bash
export PITWALL_WEBHOOK_SECRET="$(cat qa/.work/notes/webhook-secret.txt)"
```

Expected: a 32-character hex string; both terminals share the same secret.

### Step 2 — Receiver and /healthz

Terminal 2 runs the receiver; terminal 1 probes it.

```bash
# terminal 2
PITWALL_WEBHOOK_RECEIVER_PORT=18082 uv run pitwall-webhook 2>&1 | tee qa/.work/evidence/T2-09-webhook-receiver.txt
```

```bash
# terminal 1
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18082/healthz | tee qa/.work/evidence/T2-09-webhook-healthz.txt
```

Expected: `200`.

### Step 3 — Unsigned rejected

Terminal 1:

```bash
printf '%s' '{"id":"job-qa-1","status":"COMPLETED"}' > qa/.work/notes/webhook.json
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18082/webhooks/runpod -H 'Content-Type: application/json' --data-binary @qa/.work/notes/webhook.json | tee qa/.work/evidence/T2-09-webhook-unsigned.txt
```

Expected: `401`.

### Step 4 — Signed, then replay

The same `(runpod_job_id, attempt)` pair is deduplicated; the replay must come back `200`
with `"duplicate":true`. Terminal 1:

```bash
SIG="$(uv run python -c 'import os, pathlib; from pitwall.webhook_dispatcher.signer import sign; print(sign(pathlib.Path("qa/.work/notes/webhook.json").read_bytes(), os.environ["PITWALL_WEBHOOK_SECRET"]))')"
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:18082/webhooks/runpod -H 'Content-Type: application/json' -H "X-Pitwall-Webhook-Signature: $SIG" --data-binary @qa/.work/notes/webhook.json | tee qa/.work/evidence/T2-09-webhook-signed.txt
curl -s -X POST http://127.0.0.1:18082/webhooks/runpod -H 'Content-Type: application/json' -H "X-Pitwall-Webhook-Signature: $SIG" --data-binary @qa/.work/notes/webhook.json | tee qa/.work/evidence/T2-09-webhook-replay.json
```

Expected: `200`, then `200` with `"duplicate":true`.

### Step 5 — Reconciler check, bad DSN, worker boot

Terminal 1:

```bash
uv run python -m pitwall.reconciler check; echo "exit=$?" | tee qa/.work/evidence/T2-09-reconciler-check.txt
REDIS_URL='not-a-dsn' uv run python -m pitwall.reconciler check; echo "exit=$?" | tee qa/.work/evidence/T2-09-reconciler-bad-dsn.txt
timeout 8 uv run pitwall-reconciler; echo "exit=$?" | tee qa/.work/evidence/T2-09-reconciler-worker.txt
```

Expected: `exit=0`; a clear error and non-zero exit; `exit=124` with no `Traceback`. Stop the
receiver (Ctrl-C) in terminal 2 once Step 4 is done.

### Step 6 — Cost exporter metrics

Terminal 2 starts the exporter; terminal 1 scrapes `/metrics` after a few seconds.

```bash
# terminal 2
PITWALL_COST_EXPORTER_PORT=18090 uv run pitwall-cost-exporter 2>&1 | tee qa/.work/evidence/T2-09-cost-exporter.txt
```

```bash
# terminal 1
curl -s http://127.0.0.1:18090/metrics | grep '^pitwall_' | head -5 | tee qa/.work/evidence/T2-09-cost-metrics.txt
```

Expected: lines starting with `pitwall_`. Cross-check names with the
[metrics surface table](../../../docs/sdlc/13-observability.md#prometheus-metrics-surface).
Missing expected metrics are a finding (label `documentation`). Stop the exporter (Ctrl-C)
when done.

## What counts as a finding

- A `200` for an unsigned delivery (severity 1).
- A replay processed twice, no `"duplicate":true` (severity 2).
- A reconciler `check` that crashes with a `Traceback` on a bad DSN (severity 2).
- A reconciler worker that prints a `Traceback` before `timeout` (severity 2).
- A `/metrics` response with no `pitwall_` lines or a missing documented metric (severity 3,
  label `documentation`).
- Any other behavior that differs from the docs listed under Sources.

## Done when

- All six steps have evidence in `qa/.work/evidence/T2-09-*.txt`.
- The webhook receiver, reconciler, and cost exporter each behaved as expected.
- Any disagreement with the docs is filed with the tester using
  [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-09 with output links to the evidence files and any issue numbers
filed. Set `Current item` to
[T2-10 — Journey harness vs your manual results](T2-10-journey-harness.md). End the session
log with what the tester learned and any open questions.
