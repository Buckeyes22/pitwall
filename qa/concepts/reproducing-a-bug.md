# Reproducing a bug

## In one sentence

Reproducing a bug means making it happen again on purpose, from a clean starting state, with steps
that someone else can follow.

## Why it matters when testing Pitwall

A bug that nobody can reproduce rarely gets fixed. The maintainer needs the same starting state,
the same commands, and the same result. If you can reproduce it, write down the steps before you
forget them.

## Try it

```bash
cat does-not-exist.txt; echo "exit=$?"
```

Expected: a "No such file or directory" message and `exit=1`. Run the same line from a fresh
terminal and confirm the same message and `exit=1`. A reproducible bug returns the same result
every time.

## Common confusions

- Start from a known state: fresh terminal, known commit, test stack restarted.
- Change one thing at a time. If two things change, you cannot tell which one caused the bug.
- A bug that happens only sometimes is still a bug. Report how often, for example "3 of 10 runs".
- A bug that needs a strange test stack state is still your job to reproduce. Note that state in
  the report so the maintainer can match it.

## Check yourself

1. What comes first in repro steps?

<details><summary>Answer</summary>

The starting state: the commit, the test stack status, and the environment.

</details>

## Go deeper

- [Bug-report checklist](../handbook/bug-reports.md#the-bug-report-checklist)
- [Evidence standard](../handbook/evidence-standard.md)
