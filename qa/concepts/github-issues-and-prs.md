# GitHub issues and pull requests

## In one sentence

An issue reports a problem or asks a question; a pull request proposes a change and runs CI
checks before it merges.

## Why it matters when testing Pitwall

All QA output lands on GitHub: issues, QA reviews on pull requests, and test pull requests. The
QA verdict goes on the pull request, not on the issue.

## Try it

```bash
gh issue list --limit 5
gh pr list --limit 5
```

Expected: two lists, which may be empty. The first lists open issues, the second lists open
pull requests.

## Common confusions

- `Closes #N` in a pull request closes the issue when the pull request merges. Without that
  keyword, the issue stays open.
- A review can approve, request changes, or comment. On this project a QA review informs the
  maintainer, who decides the merge.
- Labels sort work. The QA verdict label (`qa-passed` or `qa-failed`) is what tells the
  maintainer the result.
- A draft pull request cannot merge until you mark it ready for review.

## Check yourself

1. Where does a QA verdict go?

<details><summary>Answer</summary>

A review on the pull request, plus a `qa-passed` or `qa-failed` label. The issue stays open
until the maintainer merges the fix.

</details>

## Go deeper

- [Triage and labels](../handbook/triage-and-labels.md)
- [Acceptance testing](../handbook/acceptance-testing.md)
