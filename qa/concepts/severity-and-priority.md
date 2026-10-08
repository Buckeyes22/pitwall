# Severity and priority

## In one sentence

Severity is how bad a problem is, and priority is how soon it gets fixed.

## Why it matters when testing Pitwall

The tester picks the severity, from the [severity scale](../handbook/triage-and-labels.md#severity-scale),
and the maintainer picks the priority during triage. Mixing up the two wastes time. A typo on
the front page can be high priority (urgent to ship) but severity 4 (cosmetic).

## Try it

```bash
gh label list --search severity
```

Expected: four labels, one per severity, named `severity:1-critical`, `severity:2-high`,
`severity:3-medium`, and `severity:4-low`.

## Common confusions

- Severity 1 is about money, secrets, or safety, not about "I really hate this bug".
- Severity and priority are different. Severity is the size of the fire. Priority is the order
  in which fires get put out.
- The same severity can have different priorities. Many small bugs in one area can outrank one
  big bug in another.
- Pick severity with the definitions, not with gut feeling. One sentence in the report says why.

## Check yourself

1. A dry run called a real provider and spent money. Which severity is it?

<details><summary>Answer</summary>

Severity 1, because it spent money or called a paid service without authorization.

</details>

## Go deeper

- [Severity scale](../handbook/triage-and-labels.md#severity-scale)
- [Labels](../handbook/triage-and-labels.md#labels)
