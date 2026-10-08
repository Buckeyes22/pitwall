# Subscription usage

## In one sentence

`pitwall usage` lists each coding subscription on the machine with how much of it
has been used and when it resets.

## Why it matters when testing Pitwall

The routing skill reads these rows before it picks a model, so a wrong status sends work to a
plan that is used up, or away from one that is fine. A row that could not be read must say
`error`, `stale`, or `unknown`; it must never look exhausted.

## Try it

```bash
pitwall usage --json
```

Expected: JSON with `observed_at` and `plans`. On a machine with no subscriptions `plans` is `[]`
and the exit code is still 0.

## Common confusions

- `limit` means the plan is used up. `error` means the read failed. They are different.
- `stale` shows old numbers on purpose, with the reason in `detail`.
- The command never signs in, refreshes a login, or prints a key.

## Check yourself

1. A row shows `status: unknown`. Is that plan used up?

<details><summary>Answer</summary>

No. `unknown` means usage cannot be measured for that plan. It carries no information about how
much is left.

</details>

## Go deeper

- [Subscription usage](../../docs/agents/usage.md)
- [Agent Routing](../../docs/sdlc/23-agent-routing.md)
