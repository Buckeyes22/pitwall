# Pitwall in one page

## In one sentence

Pitwall is a control plane that takes requests for AI work, picks a provider, checks safety and
cost before anything is spent, runs the work on rented GPUs, and records what happened.

## Why it matters when testing Pitwall

Knowing the flow shows where a bug can hide. The README reproduces it as one line: clients hit a
surface, the surface runs pre-spend inspection, then production routing, then cost admission,
then the static provider adapter, and finally the audit, Postgres, Redis, and reconciler layers.

## Try it

```bash
uv run pitwall --help 2>&1 | head -20
```

Expected: a usage line and the start of the command list.

## Common confusions

- Pitwall rents GPUs from providers such as RunPod and owns none.
- It is built for one operator, not many tenants.
- Most local testing never touches a provider.

## Check yourself

1. Put these in order: routing, cost admission, pre-spend inspection.

<details><summary>Answer</summary>

Pre-spend inspection, routing, cost admission.

</details>

## Go deeper

- [README architecture](../../README.md#architecture)
- [System overview](../../docs/sdlc/00-overview.md)
