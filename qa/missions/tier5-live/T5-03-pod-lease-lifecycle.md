# T5-03 — Pod lease lifecycle (L3)

**Tier:** 5 | **Mode:** training | **Repeatable:** yes | **Needs:** T5-02 | **Output:** issues, cleanup evidence

## Why this matters

Journey L3 walks a real registry lease through launch, renew, and stop on real GPU capacity.
Hermetic tests fake the provider, so a wrong state, a renew that does not move the expiry, or a
stop that leaves a pod running only shows up here.

## Sources

- [Serve quickstart, with a database configured](../../../docs/operator/serve-quickstart.md#with-a-database-configured)
- [Lease state machine](../../../docs/sdlc/06-leases.md#3-lease-state-machine) and
  [failure modes](../../../docs/sdlc/06-leases.md#6-failure-modes--error-types)
- [Live testing safety](../../handbook/live-testing-safety.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Tier 5 is
always training mode (rule R5): the tester types every command. The TTL, hourly cap, and budget
come from `qa/.work/notes/tier5-ceilings.md`. `pitwall leases renew` adds 60 minutes unless told
otherwise, so always pass `--extends-minutes 5`. Run the
[before every live step](../../handbook/live-testing-safety.md#before-every-live-step) list
before step 2.

## Setup

The local test stack is up and the README Quick Start has run against it, as in tier 2. Run the
five README `export` lines first, then load the real key so it replaces the placeholder:

```bash
set -a; . ~/.config/pitwall-qa/runpod.env; set +a
export PITWALL_MONTHLY_BUDGET_USD=<agreed-budget>
uv run pitwall leases list
```

Expected: a `Columns:` line and no lease rows.
If different: an existing lease row means something is already running; stop and ask the
maintainer before launching anything.

## Steps

### Step 1 — Preview the launch for free

Coach: `--dry-run` checks the launch against the registry without creating a pod. Use the model
and GPU from T5-02, quoted, with the agreed limits. `qa-l3` is the capability name.
Tester does:

```bash
uv run pitwall serve --capability qa-l3 --model <model-id> --gpu-class "<gpu-name>" \
  --ttl-minutes <agreed-ttl> --max-usd-per-hour <agreed-cap> --dry-run
```

Expected: a resolved launch plan and no pod.
If different: a refusal here costs nothing; record the code and ask the maintainer before step 2.

### Step 2 — Launch the lease

Coach: this is the paid step. The same command without `--dry-run` waits until the model answers.
Tester does:

```bash
uv run pitwall serve --capability qa-l3 --model <model-id> --gpu-class "<gpu-name>" \
  --ttl-minutes <agreed-ttl> --max-usd-per-hour <agreed-cap> --json \
  | tee qa/.work/evidence/T5-03-launch.json
```

Expected: JSON with `lease_id`, `expires_at`, `model_id`, and `proxy_base_url`, as the
[serve section](../../../docs/operator/serve-quickstart.md#serve) promises.
If different: a missing field is a finding. An error after a pod was created means run step 5 now.

### Step 3 — Confirm the lease is active

Coach: the state table lists the launch states in order; a finished launch settles on `active`.
Tester does:

```bash
uv run pitwall leases list | tee qa/.work/evidence/T5-03-list-active.txt
```

Expected: one row for the new lease with state `active`.
If different: a state missing from the [state table](../../../docs/sdlc/06-leases.md#3-lease-state-machine)
is a finding.

### Step 4 — Renew by five minutes

Coach: compare the new `expires_at` with the one saved in step 2.
Tester does:

```bash
uv run pitwall leases renew <lease-id> --extends-minutes 5 --json \
  | tee qa/.work/evidence/T5-03-renew.json
```

Expected: the same lease, still `active`, with `expires_at` exactly five minutes later than in
step 2.
If different: an expiry that did not move, or moved by another amount, is a finding.

### Step 5 — Stop the lease and run the cleanup check

Coach: stop, confirm the list is clear, then walk the
[cleanup check](../../handbook/live-testing-safety.md#cleanup-check) with the provider console.
Tester does:

```bash
uv run pitwall leases stop <lease-id> --json | tee qa/.work/evidence/T5-03-stop.json
uv run pitwall leases list | tee qa/.work/evidence/T5-03-list-after.txt
```

Expected: the stop JSON shows state `stopped`, a `terminated_at` time, and `cost_accrued_usd`.
The list has no row for the lease, and the provider console shows no pod from this mission.
If different: a pod still in the console is severity 1: follow
[orphaned pods](../../../docs/operator/troubleshooting.md#orphaned-pods-a-pod-with-no-working-owner)
and message the maintainer.

## What counts as a finding

- A launch result missing `lease_id`, `expires_at`, `model_id`, or `proxy_base_url`.
- A state that is not in the state table, or a lease that never reaches `active`.
- A renew that does not move `expires_at` by exactly the requested minutes.
- A stop without `terminated_at` or `cost_accrued_usd`, or any pod left running (severity 1).

## Done when

- Steps 2 to 5 each saved output under `qa/.work/evidence/T5-03-*`.
- `pitwall leases list` shows no row for the lease, and the console shows no pod.
- Every mismatch is filed through [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T5-03 with the evidence links and any issue numbers. Set
`Current item` to [T5-04 — Live audit](T5-04-live-audit.md). End the session log with the lease
ID, the states seen, and the final `cost_accrued_usd`.
