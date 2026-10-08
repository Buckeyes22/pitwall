# T5-05 — Serverless endpoints (L1, L2)

**Tier:** 5 | **Mode:** training | **Repeatable:** yes | **Needs:** T5-04 | **Output:** issues, cleanup evidence

## Why this matters

Journeys L1 and L2 register real serverless endpoints with Pitwall and send a real request
through them. L1 is a load-balancer (LB) endpoint and L2 is a vLLM queue endpoint. These two
operator docs are only ever exercised by hand, so every step that does not work as written is a
finding.

## Sources

- [Create an LB endpoint](../../../docs/operator/create-lb-endpoint.md): L1
- [Create a vLLM endpoint](../../../docs/operator/create-vllm-endpoint.md): L2
- [Live testing safety](../../handbook/live-testing-safety.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Tier 5 is
always training mode (rule R5): the tester types every command. Endpoints bill while workers
run: keep `workers_min` at `0` throughout. The maintainer names the images; the tester never
builds or pushes one. Both endpoints are deleted before the session ends.

## Setup

Same two terminals as T5-04: the README `export` lines and the real key in both, and
`uv run pitwall-api` running in terminal 1. The maintainer has named the capability (its name
and ID), the two images, and a canonical RunPod GPU name. The tester writes them into
`qa/.work/notes/tier5-endpoints.md` and adds each ID there as it appears.

## Steps

### Step 1 — L1: create and register the LB endpoint

Coach: walk [create an LB endpoint](../../../docs/operator/create-lb-endpoint.md) from Step 2,
one doc step per message. The maintainer did Step 1 (the image). In the console, set workers
minimum to `0`. In the Step 4 request, use `"workers_min": 0` and the canonical GPU name.
Tester does: the doc's Steps 2 and 3 in the console, then its Step 4 `curl` in terminal 2 with
`http://127.0.0.1:8080` in place of the example host, adding
`| tee qa/.work/evidence/T5-05-l1-register.json` at the end.
Expected: HTTP 201 with the new provider's `id`, which goes into the notes file.
If different: a 401 or 422 is a finding against the doc, with the exact body in the evidence.

### Step 2 — L1: audit the capability

Coach: the doc's Step 5 audit takes the capability name, not its ID.
Tester does: the doc's Step 5 `curl`, ending in `| tee qa/.work/evidence/T5-05-l1-audit.json`.
Expected: HTTP 200 with a `checks` list that includes the new provider.
If different: a failed check names its reason; record it and ask the maintainer.

### Step 3 — L2: register the template, free check first

Coach: walk [create a vLLM endpoint](../../../docs/operator/create-vllm-endpoint.md) from its
Step 1. `--dry-run` checks the image reference with no network calls.
Tester does:

```bash
uv run pitwall register-template --image <l2-image> --dry-run
uv run pitwall register-template --image <l2-image> | tee qa/.work/evidence/T5-05-l2-template.txt
```

Expected: the dry run passes, then the real run prints a template ID for the notes file.
If different: a dry-run failure costs nothing; record it and stop before the real run.

### Step 4 — L2: create and register the vLLM endpoint

Coach: the doc's Steps 2 and 3 happen in the console, with FlashBoot on and workers minimum
`0`. Its Step 4 points to `--help` for the flags; read it together.
Tester does:

```bash
uv run pitwall register-endpoint --help
uv run pitwall register-endpoint --endpoint-id <l2-endpoint-id> --provider-type serverless_queue \
  --capability-id <capability-id> --name <l2-name> --gpu-class "<gpu-name>" --workers-min 0 \
  | tee qa/.work/evidence/T5-05-l2-endpoint.txt
```

Expected: a success message with the new provider ID for the notes file.
If different: a flag the doc or help describes differently from the error is a finding.

### Step 5 — L2: one real round trip

Coach: run the doc's Step 5
[direct RunPod URL](../../../docs/operator/create-vllm-endpoint.md#direct-runpod-url) test,
then its Pitwall `/v1/inference` test against `http://127.0.0.1:8080`. The first request may
wait for a cold start.
Expected: the direct test answers exactly `OK`; the Pitwall test returns HTTP 200 with model
output. Save both replies as `qa/.work/evidence/T5-05-l2-direct.json` and `-l2-pitwall.json`.
If different: a wrong answer or a non-200 reply is a finding.

### Step 6 — Clean up both endpoints

Coach: disable both providers in Pitwall, then delete both endpoints in the provider console.
Tester does, once per provider ID:

```bash
curl -s -X POST -H "Authorization: Bearer $PITWALL_API_TOKEN" \
  -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" \
  http://127.0.0.1:8080/v1/admin/providers/<provider-id>/disable
```

Expected: each reply shows `"enabled":false`, and the console lists neither endpoint.
If different: an endpoint still in the console is severity 1: message the maintainer.

## What counts as a finding

- Any doc step that does not work as written, with the exact command and reply.
- A registration, audit, or round trip that fails with valid inputs.
- A provider that cannot be disabled, or an endpoint left in the console (severity 1).

## Done when

- Steps 1 to 5 saved output under `qa/.work/evidence/T5-05-*`.
- Both providers are disabled and both endpoints are gone from the console.
- Every mismatch is filed through [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T5-05 with the evidence links and any issue numbers. Set
`Current item` to the next repeatable item the
[mission list](../README.md#how-the-coach-picks-the-next-item) picks. Stop the API with Ctrl-C.
End the session log with the two endpoint IDs and what the session cost.
