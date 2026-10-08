# Background services

## In one sentence

Besides the API, Pitwall runs a reconciler (keeps state in sync), a webhook receiver (accepts
provider callbacks), and a cost exporter (publishes metrics).

## Why it matters when testing Pitwall

They fail quietly; mission T2-09 checks each one (J18–J20).

## Try it

With the test stack up and the README `export` lines set:

```bash
uv run python -m pitwall.reconciler check; echo "exit=$?"
```

Expected: `exit=0`.

## Common confusions

- The receiver always requires a signature: it will not start without `PITWALL_WEBHOOK_SECRET`.
- A replayed delivery is flagged as a duplicate, not processed twice.
- Metric names start with `pitwall_`.

## Check yourself

1. What should a replayed webhook get back?

<details><summary>Answer</summary>

200 with `"duplicate":true`.

</details>

## Go deeper

- [Webhooks](../../docs/sdlc/09-webhooks.md)
- [Reconciler and lifecycle](../../docs/sdlc/10-reconciler-lifecycle.md)
- [Observability](../../docs/sdlc/13-observability.md)
