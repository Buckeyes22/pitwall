# T5-02 — Personal serving smoke

**Tier:** 5 | **Mode:** training | **Repeatable:** yes | **Needs:** T5-01 | **Output:** issues, cleanup evidence

## Why this matters

Personal serving is the fastest path to real spend. A smoke test on the cheapest suitable GPU,
with the agreed TTL and hourly cap, proves that `pitwall serve` launches, the model answers, and
`pitwall stop` tears the pod down. A refusal or a stuck pod found here is cheap; found later, it
bills by the hour.

## Sources

- [What `pitwall setup` changes on your machine](../../../docs/operator/personal-serving.md#what-pitwall-setup-changes-on-your-machine)
- [Serving a model](../../../docs/operator/personal-serving.md#serving-a-model),
  [checking status](../../../docs/operator/personal-serving.md#checking-status-pitwall-status),
  [stopping](../../../docs/operator/personal-serving.md#stopping)
- [Operator quickstart, personal serving](../../../docs/operator/serve-quickstart.md#personal-serving)
- [Live testing safety](../../handbook/live-testing-safety.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Tier 5 is
always training mode (rule R5): the tester types every command. The TTL and
`--max-usd-per-hour` come from `qa/.work/notes/tier5-ceilings.md`; do not pick new numbers
without the maintainer. Run the [before every live step](../../handbook/live-testing-safety.md#before-every-live-step)
list before step 3.

## Setup

Use a new terminal without the README `export` lines: with `DATABASE_URL` set, `pitwall serve`
switches to the registry backend. Load the key, then confirm `pitwall agents` is installed.

```bash
set -a; . ~/.config/pitwall-qa/runpod.env; set +a
echo "DATABASE_URL=${DATABASE_URL:-unset}"
uv run pitwall agents harnesses > /dev/null 2>&1 && echo "agents ok" || echo "routing cli missing"
```

Expected: `DATABASE_URL=unset`, then `agents ok`.
If different: a set `DATABASE_URL` means an old terminal; open a new one. `routing cli missing`
means stop here: the maintainer and tester install it together from the Agent Routing
[install](../../../docs/agents/routing-readme.md#install) section (`pitwall agents install`). Without it, `pitwall serve` refuses with `routing_cli_missing` before any pod exists.

## Steps

### Step 1 — Run `pitwall setup` once

Coach: read [what `pitwall setup` changes](../../../docs/operator/personal-serving.md#what-pitwall-setup-changes-on-your-machine)
together first. Answer `n` to the shell-profile question, then run the `export` line it prints,
in this terminal only.
Tester does:

```bash
uv run pitwall setup
```

Expected: it finds the key from the environment, creates the local endpoint key, prints the
`export` line for `PITWALL_ENDPOINT_KEY`, and reports the `personal` backend.
If different: a `registry` backend means `DATABASE_URL` is set; go back to Setup.

### Step 2 — Pick the smallest model and the cheapest suitable GPU

Coach: `COLUMNS=200` stops the table from cutting off model IDs. With the key loaded, the
`$/hr` column shows live prices. Pick the lowest-priced row that lists a `Best single-GPU`.
Tester does:

```bash
COLUMNS=200 uv run pitwall models list | tee qa/.work/evidence/T5-02-models.txt
```

Expected: a `Models` table with `Model`, `Vendor`, `Variants`, `Best single-GPU`, and `$/hr`
columns. The tester writes the chosen model ID and GPU into `qa/.work/notes/`.

### Step 3 — Serve with the agreed limits

Coach: fill in the agreed values from `tier5-ceilings.md`. Quote the GPU name; it has spaces.
The command waits until the model answers, then returns.
Tester does:

```bash
uv run pitwall serve --model <model-id> --gpu-class "<gpu-name>" \
  --ttl-minutes <agreed-ttl> --max-usd-per-hour <agreed-cap> --route qa-smoke \
  2>&1 | tee qa/.work/evidence/T5-02-serve.txt
```

Expected: progress lines, then `route qa-smoke is ready: <served-model-id> at <endpoint-url>`,
a `deadline ...` line, and a `try it:` line.
If different: a `refused: <code>` line means no pod was created; look the code up in
[refusals and failures](../../../docs/operator/personal-serving.md#refusals-and-failures-you-might-see).
A `failed after launch: <code>` line means a pod existed: run the cleanup check in step 5 now.

### Step 4 — Check status and send one request

Coach: `pitwall status` reads local state and the provider's pod record; it spends nothing.
The request goes straight to the pod with the endpoint key, which is never printed.
Tester does:

```bash
uv run pitwall status
curl -s "<endpoint-url>/chat/completions" -H "Authorization: Bearer $PITWALL_ENDPOINT_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"<served-model-id>","messages":[{"role":"user","content":"Say hi"}],"max_tokens":16}' \
  | tee qa/.work/evidence/T5-02-request.txt
```

Expected: a `qa-smoke` row in state `ready` with the model, the GPU, and `ends <deadline>`; then
a JSON reply with a `choices` list.
If different: a `401` means the `export` line from step 1 was not run in this terminal. Any other
error is a finding.

### Step 5 — Stop and run the cleanup check

Coach: stop first, then walk the [cleanup check](../../handbook/live-testing-safety.md#cleanup-check).
`stopped` is local state, so the provider console check still matters.
Tester does:

```bash
uv run pitwall stop qa-smoke 2>&1 | tee qa/.work/evidence/T5-02-stop.txt
uv run pitwall status | tee -a qa/.work/evidence/T5-02-stop.txt
```

Expected: the `qa-smoke` row now shows `stopped`, and no row shows `launching` or `ready`. The
provider console shows no pod from this mission.
If different: a pod still in the console is severity 1: follow
[orphaned pods](../../../docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner)
and message the maintainer.

## What counts as a finding

- A `refused:` code that the docs do not explain, or a refusal when the inputs were valid.
- Any `failed after launch:` line.
- A `status` row stuck in `launching`, or a request error other than a missing `export`.
- A `stop` that leaves `ready` in `status`, or any pod left in the provider console (severity 1).

## Done when

- Steps 2 to 5 each saved output under `qa/.work/evidence/T5-02-*.txt`.
- `pitwall status` shows no `launching` or `ready` row, and the console shows no pod.
- Every mismatch is filed through [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T5-02 with the evidence links and any issue numbers. Set
`Current item` to [T5-03 — Pod lease lifecycle](T5-03-pod-lease-lifecycle.md). End the session
log with the model and GPU used and what the smoke cost.
