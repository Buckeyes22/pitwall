# Test lanes and markers

## In one sentence

Pitwall's tests are grouped into lanes by pytest markers: fast hermetic tests, integration tests
that need a real database, and special lanes such as security, property, and release.

## Why it matters when testing Pitwall

The marker on a test tells you what the test needs and how to run it. Hermetic tests need
nothing. Integration tests need the test stack. Live tests need real credentials. Mixing the
lanes makes runs slow, flaky, or unsafe.

## Try it

```bash
grep -n -A14 'markers = \[' pyproject.toml
```

Expected: a `markers = [` line, then the registered markers, including `integration`, `property`,
`security`, `release`, and `live`, each with a one-line description.

## Common confusions

- `make test` runs `-m "not integration and not slow"`. Integration tests do not run by default.
- `-m release` must be written exactly that way. Other expressions skip the release lane
  silently.
- `live` tests are on the never-run list until tier 5 (rule R2).
- A test without a marker is hermetic. Mark it if it needs the stack, credentials, or extra time.

## Check yourself

1. Which make target runs the integration lane?

<details><summary>Answer</summary>

`make test-int`, with the local test stack up (`make up` first).

</details>

## Go deeper

- [Markers and isolation](../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation)
- [QA smoke set](../handbook/regression-and-release.md#qa-smoke-set)
