# B4 — Git and GitHub

**Mode:** training | **Needs:** B3 | **Output:** none

## Goal

Read the project's history and use issues and pull requests with git and `gh`.

## You will learn

- [repository](../concepts/git-basics.md), [commit](../concepts/git-basics.md), [branch](../concepts/git-basics.md), [pull request](../concepts/github-issues-and-prs.md), [review](../concepts/github-issues-and-prs.md), [CI check](../concepts/github-issues-and-prs.md), [issue](../concepts/github-issues-and-prs.md), [label](../concepts/github-issues-and-prs.md), and DCO sign-off.

## Before you start

The tester is at the repository root on `main`, with `gh` installed and signed in ([B2](B2-install-and-clone.md)). `qa/.work/progress.md` exists and `Current item: B4`.

## Steps

### Step 1 — Commits

Coach: every commit has an id and a message. The message starts with a short prefix that says what kind of change it is. Common prefixes are `feat` (new feature), `fix` (bug fix), `docs` (documentation), and `test` (tests). CONTRIBUTING's PR Process lists them.
Tester does:
```bash
git log --oneline -5
```
Expected: five lines, each a short commit id and a message. Most messages start with a prefix such as `feat`, `fix`, `docs`, or `test`, sometimes with a scope in parentheses, like `fix(cli):`.
If different: ask the coach to read one message aloud and name its kind of change.

### Step 2 — Inside a commit

Tester does:
```bash
git show --stat HEAD | head -20
```
Expected: the author, date, message, and a list of files the commit changed with lines added and removed.
If different: ask the coach which part changed (message, files, or both).

### Step 3 — Branches

Coach: a branch is a named line of commits. The main one here is `main`. The tester's scratch branch follows `practice/<name>`.
Tester does:
```bash
git branch --show-current
git switch -c practice/$USER
git switch main
git branch -d practice/$USER
```
Expected: the first line is `main`; the second prints `Switched to a new branch 'practice/<name>'`; the third prints `Switched to branch 'main'`; the fourth prints `Deleted branch practice/<name>`.
If different: an older branch with the same name exists; pick a different name with the coach.

### Step 4 — Status

Coach: `git status --short` shows the working tree in two-character codes per file. `??` means untracked. No output means a clean tree. `qa/.work/` never shows because git ignores it. Untracked means git does not know the file; modified means the contents changed from the last commit; staged means the tester marked it for the next commit.
Tester does:
```bash
echo scratch > scratch-test.txt
git status --short
rm scratch-test.txt
git status --short
```
Expected: the first `git status --short` line is `?? scratch-test.txt`; the second prints nothing.
If different: stop and ask the coach before deleting anything.

### Step 5 — Issues and labels

Coach: an issue reports a problem or asks a question. Labels sort work. QA uses `found-by-qa` and one of the four `severity:` labels.
Tester does:
```bash
gh issue list --limit 10
gh label list
```
Expected: the first command may print nothing (no open issues yet) and exit 0; the second prints labels including `found-by-qa`, `needs-qa`, `severity:1-critical`, `severity:2-high`, `severity:3-medium`, `severity:4-low`.
If different: ask the coach which label is missing before filing anything.

### Step 6 — Duplicate search

Coach: before filing a new issue, search for the same words in past issues.
Tester does:
```bash
gh issue list --state all --search "README in:title,body"
```
Expected: a list, possibly empty, of past and present issues that match.
If different: search narrower with one keyword and a quoted phrase, for example `gh issue list --state all --search "quick start"`.

### Step 7 — Pull requests

Coach: a pull request proposes a change. It has a description, a list of changed files, and runs CI checks before it merges. The description includes a `## QA notes` section when the pull request asks for QA.
Tester does:
```bash
gh pr list --state merged --search "QA onboarding in:title" --limit 5
```
Expected: the merged pull request that added this QA packet, with its number first.
If different: search wider with `gh pr list --state merged --search "qa in:title" --limit 5` and pick a pull request that touches `qa/` files.

With that number in place of `<N>`, run:
```bash
gh pr view <N>
gh pr diff <N> --name-only | grep '^qa/' | head -5
gh pr checks <N>
```
Expected: `gh pr view` prints the description, including its `## QA notes` section; the `grep` prints `qa/` files such as `qa/README.md`; `gh pr checks` prints the CI checks, with the `CI` check marked `pass`.
If different: pick a different pull request or stop and ask the coach.

### Step 8 — Sign-offs

Coach: every commit must be signed off. The sign-off is a `Signed-off-by:` line at the end of the commit message. From tier 4 the tester's test commits also need it.
Tester does:
```bash
git log -5 --format='%h %s%n  %(trailers:key=Signed-off-by)'
```
Expected: each commit has a `Signed-off-by:` line below its message.
If different: an old commit predates the DCO requirement; ask the coach which commits are fine.
Explain: the DCO certifies the contributor has the right to submit the change under the project's license. The command is `git commit -s`.

### Step 9 — The website

Coach: open the project's GitHub page in a browser. Find the **Issues**, **Pull requests**, and **Actions** tabs. On a recent pull request, the right sidebar shows the CI checks from Step 7.
Tester does:
```bash
gh repo view --web
```
Expected: a browser opens on the repository home page. No credentials are printed.
If different: copy the URL the command prints and open it manually.

## Checkpoint

Ask the tester:

1. What is the difference between an issue and a pull request?
2. What does a `Signed-off-by:` line certify?

Expected answers: an issue reports a problem or asks a question; a pull request proposes a change and runs CI checks before it merges. The sign-off certifies the contributor has the right to contribute the change under the project's license.

## Done when

The tester found the pull request's changed files and CI result with `gh`, and explained the sign-off in their own words.

## Record in progress

Add a `Completed` row for B4 with today's date and a short note. Set `Current item: B5`.

## Next

[B5 — Pitwall tour](B5-pitwall-tour.md)
