# pytest, fixtures, and fakes

## In one sentence

pytest runs test functions, fixtures hand tests ready-made setup, and fakes stand in for real
services so tests stay hermetic.

## Why it matters when testing Pitwall

Tier 4 tests reuse the project's existing fixtures (`tests/conftest.py`) and fakes
(`tests/fakes/`) instead of calling real providers. This keeps tests fast, free, and reproducible
for the tester.

## Try it

```bash
ls tests/fakes
```

Expected: a short list of Python files including `runpod.py` and `mcp.py`. Each file defines a
fake that behaves like the real service but lives entirely inside the test process.

## Common confusions

- A test requests a fixture by naming it as an argument. `def test_x(db):` gets the `db` fixture.
- A fake behaves like the real service in a controlled way. A mock only records calls. Fakes are
  better for state and behavior. Mocks are better for verifying a call happened.
- Fakes live in `tests/fakes/`. Fixtures that use them live in `tests/conftest.py` or in
  per-area `conftest.py` files.
- A test that needs the real network or real credentials is not hermetic, even if a fake lives
  in the same file.

## Check yourself

1. Why does a hermetic test use the RunPod fake?

<details><summary>Answer</summary>

No network, no credentials, no spend, and the same result every time.

</details>

## Go deeper

- [Where tests go](../handbook/test-automation.md#where-tests-go)
- [Markers and isolation](../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation)
