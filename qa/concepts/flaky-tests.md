# Flaky tests

## In one sentence

A flaky test sometimes passes and sometimes fails with no code change.

## Why it matters when testing Pitwall

Flaky tests hide real bugs and waste time chasing ghosts. Pitwall's suite runs in random order
through `pytest-randomly`, so a flaky test is often a test that depends on the order of other
tests or on shared state.

## Try it

```bash
uv run pytest -q tests/cli/test_cli_output.py 2>&1 | tail -1
```

Expected: `24 passed in 0.3xs` or similar. Run it twice. Both runs should report `passed`. If a
test in this file sometimes fails, copy the command, the seed, and the failure into a report.

## Common confusions

- A flaky test is still a finding. Re-running until green is not a fix.
- A flaky-test report includes the runs attempted, the runs that failed, the commit, and the
  random seed pytest prints near the top of its output, for example
  `Using --randomly-seed=295386271`.
- Set the seed with `--randomly-seed=N` to reproduce a failing run.
- Fix the test, not the suite. The fix is usually about shared state, file paths, or timing.

## Check yourself

1. What does a flaky-test report include?

<details><summary>Answer</summary>

Runs attempted, runs that failed, the commit, and the random seed pytest printed.

</details>

## Go deeper

- [Markers and isolation](../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation)
- [Test automation](../handbook/test-automation.md)
