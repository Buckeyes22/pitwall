# Routing and route plans

## In one sentence

Routing filters providers by hard requirements and health, scores the rest, and produces a plan
with a selected provider and fallbacks.

## Why it matters when testing Pitwall

J01 expects `selected_provider_id` to be `prov_demo_runpod_lb`, and wrong routing sends work,
and money, to the wrong place.

## Try it

With the test stack up, `pitwall init --non-interactive` done, the README `export` lines set,
and the API running, run the README Quick Start `curl`:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/inference \
  -H "Authorization: Bearer $PITWALL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}'
```

Expected: the route plan in the response includes `selected_provider_id`.

## Common confusions

- An unhealthy provider is skipped.
- A plan is made per request.
- The fallback chain is the list of next choices.

## Check yourself

1. Which field names the chosen provider?

<details><summary>Answer</summary>

`selected_provider_id`.

</details>

## Go deeper

- [Routing and resolution](../../docs/sdlc/04-routing.md)
