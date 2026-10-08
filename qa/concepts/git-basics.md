# Git basics

## In one sentence

Git records the project's history as commits, and a branch is a named line of commits.

## Why it matters when testing Pitwall

Every test result names the commit tested, and acceptance testing checks out a pull request's
branch. Without the commit id, a report cannot be reproduced.

## Try it

```bash
git log --oneline -3
git rev-parse --short HEAD
```

Expected: three lines, each a short commit id and a message, then one short id on its own.

## Common confusions

- A commit is saved locally. A push sends it to GitHub. A local commit does not appear on the
  remote until you push it.
- `git status` shows what changed in your working tree, including untracked files.
- Switching branches changes the files on disk. Open editors can show stale content until you
  reload them.
- `git log` shows commits. `git diff` shows uncommitted changes between the working tree and the
  last commit.

## Check yourself

1. How do you find the commit you are testing?

<details><summary>Answer</summary>

Run `git rev-parse --short HEAD`. Paste the output into the report.

</details>

## Go deeper

- [B4 — Git and GitHub](../bootcamp/B4-git-and-github.md)
- [PR process](../../CONTRIBUTING.md#pr-process)
