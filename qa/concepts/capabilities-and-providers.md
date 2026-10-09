# Capabilities and providers

## In one sentence

A capability is a kind of work, such as `embedding.demo`; a provider is a registered place that
can do it, such as `prov_demo_runpod_lb`.

## Why it matters when testing Pitwall

Routing picks a healthy provider for the requested capability, and journey J01's expected result
names both. A capability with no healthy provider fails with `no_healthy_provider`, and an unknown
capability returns 404.

## Try it

With the test stack up, `pitwall init --non-interactive` done, and the README `export` lines set:

```bash
curl -s -H "Authorization: Bearer $PITWALL_API_TOKEN" http://127.0.0.1:8080/v1/capabilities
```

Expected: JSON that includes `embedding.demo`.

## Common confusions

- The seed files in `seed/` hold fake demo values on purpose.
- Provider health (`healthy`, `unhealthy`, `hibernated`) changes routing.
- An unknown capability returns 404.

## Check yourself

1. What does `pitwall init --non-interactive` create?

<details><summary>Answer</summary>

The demo capability and provider, with the provider marked healthy.

</details>

## Go deeper

- [README local broker setup](../../README.md#run-the-broker-locally)
- [Routing and resolution](../../docs/sdlc/04-routing.md)
