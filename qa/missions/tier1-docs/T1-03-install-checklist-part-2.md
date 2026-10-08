# T1-03 — Install checklist, part 2

**Tier:** 1 | **Mode:** training | **Repeatable:** no | **Needs:** T1-02 | **Output:** issues

## Why this matters

The same runbook covers the money and safety gates (cost, budget, kill switch, alerts,
leak markers). Walking steps 7 to 11 against the public artifact catches every command
that no longer matches the code, including the ones that protect users from accidental
spend.

## Sources

- [Step 7 — Cost estimation and budget gate](../../../docs/operator/install-acceptance-checklist.md#step-7--cost-estimation-and-budget-gate)
- [Step 8 — Kill-switch fires with NoOpNetworkSever](../../../docs/operator/install-acceptance-checklist.md#step-8--kill-switch-fires-with-noopnetworksever)
- [Step 9 — Alerts fire via LogNotifier; tracing is inert](../../../docs/operator/install-acceptance-checklist.md#step-9--alerts-fire-via-lognotifier-tracing-is-inert)
- [Step 10 — No internal markers leak](../../../docs/operator/install-acceptance-checklist.md#step-10--no-internal-markers-leak)
- [Step 11 — Teardown](../../../docs/operator/install-acceptance-checklist.md#step-11--teardown)
- [Launch gate](../../../docs/operator/install-acceptance-checklist.md#launch-gate)
- [Coach safety](../../coach/rules.md#safety)
- [Bug reports](../../handbook/bug-reports.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)).
Step 8's kill switch runs with a placeholder key and no pods. If any step would need a real `RUNPOD_API_KEY`, stop (rule R1) and tell the maintainer. The teardown at step 11 must leave `docker ps` clear of 5444 and 6380; if it does not, file a finding and run `make down` again.

## Setup

Continue in the part 1 terminal if it is still open: it has `E`, the scratch clone as the
current folder, the checklist's `export` lines, and the API running. In a new session, set `E`
from your checkout root again (`E="$PWD/qa/.work/evidence"`), `cd` into the scratch clone, run
the checklist's five `export` lines, bring the stack up with
`docker compose -f docker-compose.testinfra.yml up -d --wait`, and start the API with
`uv run pitwall-api &`.

## Steps

### Step 7 — Cost estimation and budget gate

Tester does:

```bash
curl -s -X POST "http://127.0.0.1:8080/v1/admin/audit-capability/embedding.demo" \
  -H "X-Pitwall-Secret: local-admin-secret" | uv run python -m json.tool | tee "$E/T1-03-step7a.txt"
```

Expected: `"ready_to_invoke": true`, and a `checks` list in which `cost_estimate_under_cap` has
`"pass": true` and a non-null `estimated_usd`.

The next two commands run the Python snippets the checklist lists, in order. For each, save the output to a separate file (`"$E/T1-03-step7b.txt"` and `"$E/T1-03-step7c.txt"`). The expected lines are `cost_exporter_refresh_ok` and `admitted=True`. Any exception or missing line is a finding.

### Step 8 — Kill-switch fires with NoOpNetworkSever

Coach: stop if any snippet needs a real key.

Tester does: run the two Python snippets the checklist lists, in order, after
confirming the three `TAILSCALE_*` variables are unset. Save the outputs to
`"$E/T1-03-step8a.txt"` and `"$E/T1-03-step8b.txt"`.

Expected: the first snippet prints `NoOpNetworkSever`; the second prints
`kill_switch activated` with `pods_terminated=0` and `tailscale_acl_updated=False`. No
exception is raised.

### Step 9 — Alerts fire via LogNotifier; tracing is inert

Tester does: run the three Python snippets the checklist lists, in order. Save the
outputs to `"$E/T1-03-step9a.txt"`, `"$E/T1-03-step9b.txt"`, and `"$E/T1-03-step9c.txt"`.

Expected: the first prints `LogNotifier`; the second prints the alert subject and body
in the captured log and does not import the `resend` module; the third prints
`trace started: True` and `trace emitted without error` without raising.

### Step 10 — No internal markers leak

Tester does:

```bash
git ls-files -z | xargs -0 uv run python tools/guards/repo_text_policy.py
echo "exit code: $?"
```

Expected: zero matches; exit code `0`. Save the output to `"$E/T1-03-step10.txt"`.

Then run the checklist's final-scrub grep:

```bash
uv run python tools/guards/repo_text_policy.py $(git ls-files)
```

Expected: the guard exits `0`. Save the output to `"$E/T1-03-step10-scrub.txt"`.

### Step 11 — Teardown

Coach: ask the tester to stop the background API and the test stack, then verify teardown
is idempotent.

Tester does:

```bash
kill %1 %2 %3 %4 2>/dev/null || true
docker compose -f docker-compose.testinfra.yml down
docker compose -f docker-compose.testinfra.yml down
```

Expected: the API process is gone; the first `down` exits cleanly; the second `down`
also exits `0` with no error about missing containers or networks. Save the output to
`"$E/T1-03-step11.txt"`.

```bash
docker ps
```

Expected: no row showing 5444 or 6380 in the `PORTS` column.

### Launch gate

Coach: open the [Launch gate](../../../docs/operator/install-acceptance-checklist.md#launch-gate)
section and confirm every checklist step was actually run.

Tester does:

```bash
ls "$E/T1-02-step1.txt" "$E/T1-02-step6.txt" "$E/T1-03-step7a.txt" "$E/T1-03-step11.txt"
```

Expected: every file exists. Record in the progress file whether every item in the
launch gate was covered by steps 1 to 11. A gap is a finding.

## What counts as a finding

- A command that fails as written against the public artifact.
- A missing prerequisite the checklist did not list.
- A vague instruction that does not say how to know the step worked.
- Output that contradicts the step's **Expected** line.
- A checklist row that points at something that does not exist.
- A launch-gate item that no step covered.

Every finding is filed through [bug reports](../../handbook/bug-reports.md) with
`found-by-qa` and a severity label.

## Done when

Steps 7 through 11 are each ticked in a note or filed as an issue; the teardown left
`docker ps` clear of 5444 and 6380; the launch-gate mapping is recorded.

## Record in progress

Add a `Completed` row with links to the step evidence files and to every filed issue.
Set `Current item: T1-04`. Write a short session log entry.
