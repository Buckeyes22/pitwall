# Cost, budget, and guardrails

## In one sentence

Before any spend, Pitwall inspects the payload for secrets and personal data (guardrails),
estimates the cost, and refuses requests over the budget.

## Why it matters when testing Pitwall

These are the money and safety gates, and a failure here is severity 1.

## Try it

With the README `export` lines set:

```bash
uv run pitwall guardrails status
```

Expected: the guardrail mode and a `Guardrail rules` table; exit 0.

## Common confusions

- `guardrails preview` exits 0 whether it allows, redacts, or blocks, and exits 2 only for bad
  or oversized JSON.
- 402 is the budget refusal.
- Prices are exact decimals written as strings in JSON.

## Check yourself

1. What exit code does `guardrails preview` return when it blocks a payload?

<details><summary>Answer</summary>

0. Block is a successful preview.

</details>

## Go deeper

- [Cost and budget](../../docs/sdlc/05-cost-budget.md)
- [guardrails status / guardrails preview](../../docs/sdlc/18-cli.md#guardrails-status--guardrails-preview)
