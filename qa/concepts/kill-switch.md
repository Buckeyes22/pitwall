# The kill switch

## In one sentence

The kill switch is an emergency stop that blocks new work and can terminate running compute.

## Why it matters when testing Pitwall

It must finish quickly and in order (block access, remove devices, terminate compute). The
[release testing checklist](../../docs/operator/release-testing-checklist.md) section 4 covers it.
Journey J21 drills it safely with `"terminate_compute": false`.

## Try it

```bash
grep -n '^| J21' docs/operator/user-journey-catalog.md
```

Expected: the J21 row.

## Common confusions

- It needs the admin secret header; locally there are no pods, so the drill is safe.
- A kill switch that fails to stop things is severity 1.

## Check yourself

1. Which header carries the admin secret?

<details><summary>Answer</summary>

`X-Pitwall-Secret`, plus the bearer token when API auth is on.

</details>

## Go deeper

- [REST API route inventory](../../docs/sdlc/02-api-rest.md#3-route-inventory)
- [Release testing checklist](../../docs/operator/release-testing-checklist.md)
