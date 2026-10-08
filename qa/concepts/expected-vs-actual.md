# Expected vs actual

## In one sentence

Every test compares what should happen (the expected result) with what did happen (the actual
result), and a difference between them is a finding.

## Why it matters when testing Pitwall

A report without a clear expected result is just an opinion. The maintainer cannot decide if the
behavior is wrong without knowing what was supposed to happen. The expected result comes from a
document, the journey catalog, a command's `--help` output, or the source code itself.

## Try it

```bash
uv run pitwall --version
grep -m1 '^version' pyproject.toml
```

Expected: both lines contain the same version string, for example `0.1.0a2`. If the two strings
disagree, that is a finding.

## Common confusions

- "Expected" means what the system should do, not what you would like it to do.
- When no doc says what should happen, ask the maintainer before filing a bug.
- "Actual" is the observed output, with the exact command and environment.
- A finding is the difference between expected and actual, plus the evidence that shows it.

## Check yourself

1. Where do expected results come from?

<details><summary>Answer</summary>

From docs, the journey catalog, `--help` output, and the source code itself.

</details>

## Go deeper

- [Bug reports](../handbook/bug-reports.md)
