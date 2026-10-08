# Dry runs and hermetic testing

## In one sentence

A dry run does the planning (routing and cost) but sends nothing to a paid provider; hermetic means
a test uses no real outside services at all.

## Why it matters when testing Pitwall

Tiers 0–4 are hermetic. The placeholder key makes every paid path fail at authentication. A dry
run that contacts a real provider is severity 1.

## Try it

With the test stack up, `pitwall init --non-interactive` done, the README `export` lines set, and
the API running, run the README Quick Start `curl`:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/inference \
  -H "Authorization: Bearer $PITWALL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}'
```

Expected: the response contains `"dry_run":true`.

## Common confusions

- Dry run is an option on one request, while hermetic describes a whole test or environment.
- `-m live` tests are the opposite of hermetic and are on the never-run list.

## Check yourself

1. Why is tier 5 locked?

<details><summary>Answer</summary>

It needs a real key and spends real money.

</details>

## Go deeper

- [Markers and isolation](../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation)
