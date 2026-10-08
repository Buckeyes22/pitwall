# Pods, leases, and serving

## In one sentence

A pod is a rented GPU machine; a lease is Pitwall's record of a pod it launched, with an expiry;
serving runs a model on a pod behind an OpenAI-compatible address.

## Why it matters when testing Pitwall

These are the paid parts (tier 5), but their read-only views (`pitwall leases list`, the TUI
Leases view) are testable now.

## Try it

With the test stack up and the README `export` lines set:

```bash
uv run pitwall leases list
```

Expected: an empty result, because there are no pods locally.

## Common confusions

- `pitwall serve`, `status`, and `stop` are on the never-run list in tiers 0–4.
- A TTL is the pod's self-stop deadline.
- An orphaned pod has no working owner.

## Check yourself

1. Why does every launch get a TTL?

<details><summary>Answer</summary>

So a forgotten pod stops itself and stops costing money.

</details>

## Go deeper

- [Pod leases](../../docs/sdlc/06-leases.md)
- [Personal serving](../../docs/operator/personal-serving.md)
