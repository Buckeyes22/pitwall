# T2-10 — Journey harness vs your manual results

**Tier:** 2 | **Mode:** work | **Repeatable:** yes | **Needs:** T2-01 to T2-09 | **Output:** issues

## Why this matters

Automation and manual testing should agree. When they don't, one of them is wrong. The journey
harness in `scripts/release/run-user-journeys.sh` runs every hermetic journey end to end. Your
hand-run results in T2-01 to T2-09 should match its results. A disagreement means the harness has
drifted, the docs have drifted, or your hand-run missed something.

## Sources

- [Full journey harness](../../handbook/regression-and-release.md#full-journey-harness)
- [User journey catalog](../../../docs/operator/user-journey-catalog.md)
- [Journey harness source](../../../scripts/release/run-user-journeys.sh)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). The harness
resets the database it is given, so run it only against the local stack. Stop any other server
holding `8080`, `18080`–`18089`, or `18090` first; the harness exits if any of those ports is
already bound.

## Setup

Run the tier 2 standard setup, then make sure nothing else is bound to the journey ports.

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
ss -tln | grep -E ':(8080|1808[0-9]|18090) '
```

Then set the five `export` lines from the README Quick Start in the terminal that will run the
harness.

Expected: no other clone's Postgres on `5444` or Redis on `6380`; both compose services started;
the `ss` line produces no output (every listed port is free).

## Steps

### Step 1 — Run the full journey harness

Coach: the harness resets the database for the journeys that need a clean state. The output is verbose;
the last 40 lines carry the per-journey summary and the final pass/fail counts. The tail captures
both the summary and the exit code.

Tester does:

```bash
bash scripts/release/run-user-journeys.sh 2>&1 \
  | tee qa/.work/evidence/T2-10-journeys.txt | tail -40
echo "exit=${PIPESTATUS[0]}" | tee -a qa/.work/evidence/T2-10-journeys.txt
```

Expected: a `journey summary` block with one line per journey id, a final `<n> passed, 0 failed`
line, and `exit=0` on the last line.
If different: any journey marked `FAIL`, a final line that says anything other than `0 failed`, or
`exit` not equal to `0` is a finding. Save the full evidence file before fixing anything.

### Step 2 — Compare harness results with your manual notes

Coach: open `qa/.work/evidence/T2-01-*.txt` through `T2-09-*.txt`. For every journey you ran by
hand, compare the harness verdict with your notes.
Tester does: for each disagreement, write one line into `qa/.work/notes/T2-10-diff.txt` with the
journey id, what the harness said, what you saw, and which doc supports which side.
Expected: every journey you covered manually agrees with the harness verdict, or appears in the
diff notes.
If different: open a finding with [bug reports](../../handbook/bug-reports.md). A wrong verdict is
a doc bug, a product bug, or a harness bug — the diff notes decide which.

### Step 3 — Restore clean state and stop the stack

Coach: the harness leaves the database and the seed in whatever state the last journey left them.
Re-run the standard setup so the next mission starts clean.

Tester does:

```bash
uv run pitwall db migrate
uv run pitwall init --non-interactive
make down 2>&1 | tee qa/.work/evidence/T2-10-make-down.txt
```

Expected: both `uv run` commands finish without errors; `make down` stops the compose services.

## What counts as a finding

- A journey marked `FAIL` in the harness summary (severity depends on the journey; treat a `500`
  as severity 2).
- A hand-run verdict that disagrees with the harness verdict on the same journey.
- A harness exit code other than `0` when the summary reports `0 failed`.
- A missing journey in the summary that the catalog lists as hermetic (J01–J27).
- A journey that the docs say must be hermetic but the harness runs with real network calls.

## Done when

- `qa/.work/evidence/T2-10-journeys.txt` ends with `0 failed` and `exit=0`.
- `qa/.work/notes/T2-10-diff.txt` records every disagreement, or every covered journey agrees.
- Every disagreement is filed with the tester using [bug reports](../../handbook/bug-reports.md).
- The compose stack is torn down (`make down`) before the session ends.

## Record in progress

Add a `Completed` row for T2-10 with output links to `qa/.work/evidence/T2-10-journeys.txt` and
`qa/.work/notes/T2-10-diff.txt`, plus any issue numbers filed. Set `Current item` to
[T2-11 — Agent Routing component checks](T2-11-agent-routing-checks.md). End the session log with
what the tester learned and any open questions.
