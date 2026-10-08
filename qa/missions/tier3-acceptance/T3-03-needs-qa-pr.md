# T3-03 — Acceptance-test a needs-qa pull request

**Tier:** 3 | **Mode:** work | **Repeatable:** yes | **Needs:** tier 3 unlocked | **Output:** posted QA review and issues

## Why this matters

This is the tester's main job in tier 3. The maintainer asks for QA by adding the `needs-qa`
label and filling in `## QA notes`. The tester's verdict, with evidence, tells the maintainer
whether the change is ready to merge. A wrong "pass" lets a defect land. A wrong "fail" blocks
good work.

## Sources

- The pull request's `## QA notes` and any plan it links.
- [Tester side: the acceptance run](../../handbook/acceptance-testing.md#tester-side-the-acceptance-run),
  [the QA report](../../handbook/acceptance-testing.md#the-qa-report),
  [outcomes and labels](../../handbook/acceptance-testing.md#outcomes-and-labels).
- [Bug reports](../../handbook/bug-reports.md),
  [the QA smoke set](../../handbook/regression-and-release.md#qa-smoke-set).

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). The tester
approves the review and every issue body before posting (rule R9). Live credentials are never
used in tier 3; any live-only criterion goes in "Skipped, and why". QA does not merge.

## Setup

- The tester is on `main` with `qa/.work/progress.md` up to date.
- The five `export` lines from the README Quick Start are set in the terminal.
- `gh` is authenticated; the local test stack is up if the smoke set needs it.

## Steps

Follow the twelve steps of [the tester-side acceptance run](../../handbook/acceptance-testing.md#tester-side-the-acceptance-run), one per message. The phases below group them so the mission stays short.

### Step 1 — Find the pull request and read its QA notes

Tester does:

```bash
gh pr list --label needs-qa
gh pr view <N>
```

Expected: one or more open pull requests. Pick the oldest, unless the maintainer named one in
the weekly check-in. Each one has a `## QA notes` section with numbered criteria, the commands
that exercise them, risk areas, and a live-credentials line if any criterion needs them.
If different: stop and ask the maintainer to fill in the section before continuing.

### Step 2 — Get the code and baseline the suite

Tester does:

```bash
git switch main
git pull
gh pr checkout <N>
uv sync --frozen --extra dev
make test 2>&1 | tee qa/.work/evidence/T3-03-<N>-suite.txt | tail -15
```

Expected: clean checkout, dependencies installed, suite shows `passed` and no `failed`.
If different: the suite fails on the pull request commit. Stop and report the failure first.
The end label will be `qa-failed` in that case.

### Step 3 — Run every criterion and explore around the change

Coach: for each criterion, run the exact command and save the output. If the criterion needs live
credentials, skip the run and put it in "Skipped, and why". After the criteria, spend ten minutes
in the area the pull request touched.
Tester does, for each criterion:

```bash
<command-from-QA-notes> 2>&1 | tee qa/.work/evidence/T3-03-<N>-F<m>.txt
```

Then a short charter from [exploratory testing](../../handbook/exploratory-testing.md), one
paragraph of notes.
Expected: every criterion passes; the note is one paragraph. Any failure is a finding.

### Step 4 — Run smoke checks, draft the report, and get approval

Coach: pick the smoke items that fit the change (item 1 ran in step 2; start at item 2). Then
walk the tester through the QA report template: one row per criterion (command, result,
evidence), the smoke table, the issues, the questions, and the "Skipped, and why" rows.
Tester does:

```bash
make test-int 2>&1 | tee qa/.work/evidence/T3-03-<N>-int.txt | tail -15
cp qa/templates/qa-report.md qa/.work/notes/pr-<N>-qa.md
$EDITOR qa/.work/notes/pr-<N>-qa.md
```

Coach: read the draft aloud, row by row. Confirm the verdict matches the evidence.
Tester does: says yes or asks for edits.
Expected: each chosen smoke check passes; the draft matches
[the QA report](../../handbook/acceptance-testing.md#the-qa-report); the tester says yes. A row
without a command, a result, or evidence is fixed before posting.

### Step 5 — Post, file issues, and return to `main`

Coach: confirm the verdict and the matching review command. Have the tester file one issue per
finding with `found-by-qa`, one `severity:` label, and either `bug` or `documentation`. Severity
1 is urgent (rule R14).
Tester does:

```bash
gh pr review <N> --approve --body-file qa/.work/notes/pr-<N>-qa.md           # pass
gh pr review <N> --comment --body-file qa/.work/notes/pr-<N>-qa.md           # pass with issues
gh pr review <N> --request-changes --body-file qa/.work/notes/pr-<N>-qa.md   # fail
gh pr edit <N> --remove-label needs-qa --add-label qa-passed    # or qa-failed
gh issue create --title "<title>" --body-file qa/.work/notes/<short-name>.md --label found-by-qa --label severity:<n>-<name> --label bug
git switch main
uv sync --frozen --extra dev
```

Expected: the review is posted, the label swap matches the verdict
([outcomes and labels](../../handbook/acceptance-testing.md#outcomes-and-labels)), one issue per
defect with a repro and an evidence link, and the tester is back on `main` with dependencies
restored.
If different: the review command and the label disagree. Stop, repost with the correct command,
and confirm the label. A missing label or evidence on an issue is fixed before the session ends.

## What counts as a finding

- A failed acceptance criterion in the pull request's own description.
- A regression in the smoke set compared to the previous run on `main`.
- Behavior that contradicts the pull request's own description.
- A defect found during the exploratory pass that touches the same surface.

## Done when

- Every acceptance criterion has a command, a result, and evidence, or a "Skipped, and why" row.
- The smoke checks relevant to the change were run.
- The QA report was posted as a pull request review.
- The label was swapped from `needs-qa` to `qa-passed` or `qa-failed`, matching the verdict.
- Every defect has a filed issue, and the issue numbers are listed in the report.

## Record in progress

Add a `Completed` row for T3-03 with the pull request number, the verdict, and links to the
report and the issues. Set `Current item` to the next item the
[mission list](../../missions/README.md#how-the-coach-picks-the-next-item) picks. End the session
log with what the tester learned and any open questions for the maintainer. Then:

```bash
git switch main
uv sync --frozen --extra dev
```
