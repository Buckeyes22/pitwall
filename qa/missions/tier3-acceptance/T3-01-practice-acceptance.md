# T3-01 — Practice acceptance on a merged pull request

**Tier:** 3 | **Mode:** work | **Repeatable:** no | **Needs:** tier 3 unlocked | **Output:** local QA report

## Why this matters

Tier 3 is the tester's real job: check pull requests before they merge. This mission practices the
whole loop on a pull request that is already merged and has nothing at stake. The tester learns
what an acceptance run feels like end to end, and the report becomes a private checklist for the
missions that come later.

## Sources

- The merged pull request whose title contains "QA onboarding" (find it with `gh pr list
  --state merged --search "QA onboarding in:title" --limit 5`).
- [The tester side of the acceptance run](../../handbook/acceptance-testing.md#tester-side-the-acceptance-run).
- [The QA report](../../handbook/acceptance-testing.md#the-qa-report).
- [Triage and labels](../../handbook/triage-and-labels.md).
- [Evidence standard](../../handbook/evidence-standard.md).

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Nothing is
posted in this mission. The report stays in `qa/.work/notes/` and is never pushed, never commented,
never reviewed. The tester's approval is still required for the local file: the tester decides
when the draft is final.

## Setup

- The tester is on `main` of the local clone, with `qa/.work/progress.md` up to date.
- The five `export` lines from the README Quick Start are set in the terminal.
- The local test stack is not required. The F-criteria use `uv run` directly with the dry-run
  flags the QA notes give.

## Steps

### Step 1 — Find the practice pull request

Coach: explain that this mission is rehearsal. The pull request is already merged, so any defect
the tester finds is a real bug on `main`, not a hypothetical one.
Tester does:

```bash
gh pr list --state merged --search "QA onboarding in:title" --limit 5
```

Expected: a list with one row whose title mentions "QA onboarding".
If different: stop. The pull request that added `qa/` may not be merged yet. Ask the maintainer
which merged pull request to practice on, then continue.
Then:

```bash
gh pr view <N>
```

Expected: the pull request description includes a `## QA notes` section that lists criteria F1
through F9, with the exact command to run for each.

### Step 2 — Work through every F-criterion on `main`

Coach: read the F-criteria aloud, one at a time. For each, ask the tester what the criterion
claims, then have them run the command the QA notes give. Save the output.
Tester does, for each criterion:

```bash
git switch main
git pull
uv sync --frozen --extra dev
# run the exact command from QA notes, capture output
<command-from-QA-notes> 2>&1 | tee qa/.work/evidence/T3-01-F<n>.txt
```

Expected: every criterion passes. The output matches what the QA notes describe.
If different: the criterion fails on `main`. That is a real defect. File it with the tester using
[bug reports](../../handbook/bug-reports.md) and record the issue number in the notes file. Do not
try to fix it; QA does not fix product code or docs (rule R8).

### Step 3 — Copy and fill the QA report template

Coach: tell the tester that the report is private and lives only on disk. They fill in the verdict,
the commit, the environment, and one row per F-criterion with the command, the result, and the
evidence path.
Tester does:

```bash
cp qa/templates/qa-report.md qa/.work/notes/T3-01-qa-report.md
$EDITOR qa/.work/notes/T3-01-qa-report.md
```

Expected: a filled report that matches the QA report fields in
[the handbook](../../handbook/acceptance-testing.md#the-qa-report).
If different: the report is missing a row, or a row has no command, no result, or no evidence.
Fix it before moving on.

### Step 4 — Coach review against the handbook

Coach: read the draft aloud, row by row. Check that every F-criterion has a command, a result, and
an evidence link. Check that the verdict matches the evidence. Note any "questions for the
maintainer" or "skipped, and why" rows the tester filled in.
Tester does: answers questions and updates the draft until the coach is satisfied.
Expected: every F-criterion has a command, a result, and evidence. The verdict is consistent with
the evidence. Nothing in the draft is invented.
If different: point to the missing piece. The tester edits the file and asks the coach to look
again. The draft is not done until every row passes the check.

## What counts as a finding

- An F-criterion that fails on `main`. File it with `found-by-qa`, the matching `severity:` label,
  and either `bug` or `documentation`.
- A regression in the smoke set while checking a criterion. Same handling.
- Behavior that contradicts what the QA notes claim. Same handling, plus a note in "Questions for
  the maintainer" in the report.

## Done when

- Every F-criterion has been run on `main` with the exact command from the QA notes, and the
  output is saved in `qa/.work/evidence/`.
- `qa/.work/notes/T3-01-qa-report.md` is filled in and the coach has confirmed every row.
- Any failure found during the run has a filed issue, and the issue number appears in the report.

## Record in progress

Add a `Completed` row for T3-01 with output links to the evidence files and the report. Set
`Current item` to the next item the maintainer or the
[mission list](../../missions/README.md#mission-list) picks. End the session log with what the
tester learned and any open questions. Then:

```bash
git switch main
uv sync --frozen --extra dev
```
