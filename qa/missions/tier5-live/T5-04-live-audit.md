# T5-04 — Live audit (L4)

**Tier:** 5 | **Mode:** training | **Repeatable:** yes | **Needs:** T5-01 | **Output:** audit report

## Why this matters

Journey L4 runs the 19-check audit, the pre-spend gate the maintainer relies on before any
non-trivial pod spend. The [19-check audit procedure](../../../docs/operator/16-check-audit-procedure.md)
names every check and three ways to run it. A report built from all three is evidence the
maintainer can act on.

## Sources

- [19-check audit procedure](../../../docs/operator/16-check-audit-procedure.md): the
  [execution modes](../../../docs/operator/16-check-audit-procedure.md#execution-modes) and
  [the 19 checks](../../../docs/operator/16-check-audit-procedure.md#the-19-checks)
- [Severity scale](../../handbook/triage-and-labels.md#severity-scale)
- [Live testing safety](../../handbook/live-testing-safety.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Tier 5 is
always training mode (rule R5): the tester types every command. Nothing in this mission launches
a pod. Secrets travel only inside `$VARIABLE` references and are never printed.

## Setup

The local test stack is up and the README Quick Start has run against it. In terminal 1, run the
five README `export` lines, load the real key, and start the API:

```bash
set -a; . ~/.config/pitwall-qa/runpod.env; set +a
uv run pitwall-api
```

In terminal 2, run the same `export` lines and key load, then check the admin secret without
printing it:

```bash
set -a; . ~/.config/pitwall-qa/runpod.env; set +a
test -n "$PITWALL_ADMIN_SECRET" && echo set
```

Expected: terminal 1 shows the API running on `127.0.0.1:8080`; terminal 2 prints `set`.

## Steps

### Step 1 — Mode 1, the audit harness

Coach: Mode 1 checks Pitwall's configuration against all 19 checks. The first line is the verdict.
Tester does, in terminal 2:

```bash
uv run python -m pitwall.audit.checks --strict 2>&1 | tee qa/.work/evidence/T5-04-mode1.txt | head -3
echo "exit=${PIPESTATUS[0]}"
```

Expected: `Pitwall 19-check audit: 19/19 passed`, then numbered `PASS` lines, then `exit=0`.
If different: any `FAIL` line or a nonzero exit is severity 1. Stop and message the maintainer.

### Step 2 — Mode 2, the hermetic tests

Coach: Mode 2 proves the audit code itself works, using fake provider responses.
Tester does:

```bash
uv run pytest -q tests/test_audit_checks.py 2>&1 | tee qa/.work/evidence/T5-04-mode2.txt | tail -3
echo "exit=${PIPESTATUS[0]}"
```

Expected: a summary with `passed`, no `failed`, and `exit=0`.
If different: a failing test is a finding against the audit code.

### Step 3 — Mode 3, the capability audit endpoint

Coach: use the capability the maintainer names; `embedding.demo` from the README seed works when
nothing else is registered. The API needs the bearer token and the admin secret.
Tester does:

```bash
curl -s -X POST -H "Authorization: Bearer $PITWALL_API_TOKEN" \
  -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" \
  http://127.0.0.1:8080/v1/admin/audit-capability/<capability> \
  | uv run python -m json.tool | tee qa/.work/evidence/T5-04-mode3.json
```

Expected: JSON with `ready_to_invoke`, a `checks` list, and a `runpod_audit_passed` check whose
`pass` is `true` and which has a `checked_at` time.
If different: a `runpod_audit_passed` with `pass: false` is severity 1. A `ready_to_invoke: false`
names its failing check; ask the maintainer whether that capability was expected to be ready.

### Step 4 — Write and post the report

Coach: the report lists the three commands, the three verdicts, the date, and a one-line
conclusion. The tester approves the draft before it goes anywhere.
Tester does:

```bash
$EDITOR qa/.work/notes/tier5-audit-<date>.md
```

Expected: a short report that matches the evidence. After the tester approves it, it is posted
where the maintainer asked.

## What counts as a finding

- Any `FAIL` in Mode 1, or a check list that differs from
  [the 19 checks](../../../docs/operator/16-check-audit-procedure.md#the-19-checks).
- A failing test in Mode 2.
- A `runpod_audit_passed` failure in Mode 3 (severity 1), or a response shape that differs from
  the procedure.
- A procedure step that does not work as written.

## Done when

- Steps 1 to 3 each saved output under `qa/.work/evidence/T5-04-*`.
- The report is approved by the tester and posted where the maintainer asked.
- Every mismatch is filed through [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T5-04 with the evidence links and where the report was posted. Set
`Current item` to [T5-05 — Serverless endpoints](T5-05-serverless-endpoints.md). Stop the API in
terminal 1 with Ctrl-C. End the session log with the three verdicts.
