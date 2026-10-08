# Regression and smoke tests

## In one sentence

A regression is something that used to work and broke, and a smoke test is a quick check that the
main things still work.

## Why it matters when testing Pitwall

QA runs the [smoke set](../handbook/regression-and-release.md#qa-smoke-set) after every change to
catch regressions early. A regression test is a test written for one specific bug, so the bug
cannot come back without the test failing first.

## Try it

```bash
make test 2>&1 | tail -3
```

Expected: a summary line containing `passed`, for example `1234 passed in 12.34s`. The run can
take several minutes. If any test fails, copy the summary line and the failure into the report.

## Common confusions

- A smoke test is not the full suite. It is the smallest set that catches most regressions.
- A regression test is written for one specific bug. It must fail when the bug is present and
  pass when the bug is fixed.
- `make test` runs the fast hermetic lane. Integration, security, and release lanes need their
  own commands.
- A passing smoke run is evidence. A failing smoke run is a finding, not a personal failure.

## Check yourself

1. Which smoke item always runs?

<details><summary>Answer</summary>

`make test`. The handbook lists it as item 1 of the smoke set, and it always runs.

</details>

## Go deeper

- [QA smoke set](../handbook/regression-and-release.md#qa-smoke-set)
- [Find then fix](../handbook/test-automation.md#find-then-fix)
