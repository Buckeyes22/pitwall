# B6 — QA fundamentals and your first issues

**Mode:** training | **Needs:** B5 | **Output:** first GitHub issues

## Goal

Turn the B3 raw findings into real, useful GitHub issues.

## You will learn

- [expected-vs-actual](../concepts/expected-vs-actual.md), [reproducing-a-bug](../concepts/reproducing-a-bug.md), [severity-and-priority](../concepts/severity-and-priority.md).

## Before you start

The tester has finished B3 with at least one raw finding under "Findings not yet filed" in the progress file. B3's scratch clone exists under `~/qa-scratch/` for clean-state work. `gh` is signed in ([B2](B2-install-and-clone.md)). `qa/.work/progress.md` exists and `Current item: B6`.

## Steps

### Step 1 — Three cards

Coach: read each card with the tester, then ask the card's check question. The tester answers without looking at the answer key.
Tester does: answers each check question in their own words.
Expected: the three answers match the card's hidden answer in their own words:

- expected-vs-actual: expected comes from docs, the journey catalog, `--help` output, or the source.
- reproducing-a-bug: the starting state comes first: the commit, the test stack status, and the environment.
- severity-and-priority: severity is the size of the fire; priority is the order fires get put out.

If different: re-read the card and try again.

### Step 2 — Pick a finding

Coach: pick the first raw finding from B3. The tester decides whether it is a product bug or a doc bug.
Tester does: reads the [product bug or doc bug rule](../handbook/bug-reports.md#product-bug-or-doc-bug) and decides.
Expected: a sentence that says either "the product does the wrong thing" (label `bug`) or "the product is right and the doc is wrong" (label `documentation`).
If different: add the finding to "Questions for the maintainer" in the progress file (rule R7) and pick a different B3 finding.

### Step 3 — Reproduce it

Coach: repeat the finding's steps in B3's scratch clone (rule R8: the working clone stays clean). Evidence still belongs in the working clone, so set `E` while standing at the working clone's root, before the `cd`:
```bash
E="$PWD/qa/.work/evidence"; echo "$E"
cd ~/qa-scratch/<date>/pitwall
```
The `echo` prints a path ending in `/pitwall/qa/.work/evidence` inside the working clone. Then run the finding's steps and save output to:
```bash
<command> 2>&1 | tee "$E/B6-<n>.txt"
```
Tester does: reproduces the finding in the scratch clone and saves the output.
Expected: the problem happens again with the same error or wrong output.
If different: record "could not reproduce" in the progress file and decide with the coach whether to drop it, with the reason written down.

### Step 4 — Duplicates

Coach: before filing, search past issues for the same words.
Tester does:
```bash
gh issue list --state all --search "<keywords>"
```
where `<keywords>` are two or three words from the finding's title.
Expected: no matching issue. If one matches, comment there instead.
If different: open the matching issue, comment with the new evidence and a link back to the B3 raw finding, then move to the next B3 finding.

### Step 5 — Draft

Coach: copy the bug-report template, then the tester writes the report.
Tester does:
```bash
cp qa/templates/bug-report.md qa/.work/notes/B6-<n>.md
```
Then the tester fills in each section. The coach reviews against the [bug-report checklist](../handbook/bug-reports.md#the-bug-report-checklist) and helps with wording, never with the tester's judgment. The tester picks the severity using the [severity scale](../handbook/triage-and-labels.md#severity-scale) and writes one sentence explaining why.
Expected: every checklist item is satisfied, with no key, token, or model-server address (rule R10). Severity has one sentence.
If different: re-read the checklist and revise.

### Step 6 — Post

Coach: rule R9 says the tester approves the draft before anything is posted.
Tester does: reads the draft, then approves. Then runs:
```bash
gh issue create --title "<title>" --body-file qa/.work/notes/B6-<n>.md --label documentation --label found-by-qa --label severity:3-medium
```
Use `--label bug` instead of `--label documentation` for a product bug, and pick the severity label that matches the scale.
Expected: the command prints the new issue's address, for example `https://github.com/Buckeyes22/pitwall/issues/<N>`.
If different: paste the error to the coach before retrying.
Coach: record the issue's address in the `Completed` row of the progress file.

### Step 7 — Repeat

Coach: do Steps 2–6 for each B3 finding, or drop a finding with a reason in the progress file.
Tester does: continues until every B3 finding is filed or dropped.
Expected: every B3 finding has either an issue address in `Completed` or a "dropped: <reason>" note in the progress file.
If different: stop and ask the coach which finding to handle next.

### Step 8 — Unlock tier 1

Coach: ask the Tier 1 questions from [the tier check](../templates/tier-check.md#tier-1-after-the-bootcamp), one at a time. The tester passes when every answer is right after at most one hint each.
Tester does: answers each question.
Expected: all five answers correct, with at most one hint each.
If different: re-read the linked rule or card for any missed question, then try again next session.
Coach: record the unlock in the progress file: `Unlocked tiers: 0, 1 (<date>)`, set `Current tier: 1`, and set `Current item: T1-01`.

## Checkpoint

Ask the tester:

1. What makes repro steps good?
2. Why search for duplicates before filing?

Expected answers: repro steps are numbered, start from a clean state, use exact commands, and change one thing at a time; duplicate search keeps one problem in one issue, so the maintainer's time goes to fixing, not sorting.

## Done when

Every B3 finding is filed or dropped with a reason, and tier 1 is unlocked.

## Record in progress

Add a `Completed` row for B6 with today's date and the new issue links. Write a short session
log entry that names the number of issues filed and the date tier 1 was unlocked.

## Next

[the mission ladder](../missions/README.md)
