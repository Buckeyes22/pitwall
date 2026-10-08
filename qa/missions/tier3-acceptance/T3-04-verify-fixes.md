# T3-04 — Verify fixes for issues you filed

**Tier:** 3 | **Mode:** work | **Repeatable:** yes | **Needs:** tier 3 unlocked | **Output:** issue comments

## Why this matters

An issue is not truly done until QA confirms the fix on `main`. The maintainer closes the issue
with a fix pull request, but the tester is the one who proves the bug is gone. Without that
proof the issue can come back, and the label trail lies.

## Sources

- [Issue lifecycle](../../handbook/triage-and-labels.md#issue-lifecycle).
- [Triage and labels](../../handbook/triage-and-labels.md).
- [Evidence standard](../../handbook/evidence-standard.md).

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). The tester
approves every comment and label change before posting (rule R9). Run the original repro on the
current `main`, not on the fix branch: the goal is to prove the fix is on `main`.

## Setup

- The tester is on `main` with `qa/.work/progress.md` up to date.
- The five `export` lines from the README Quick Start are set in the terminal.
- `gh` is authenticated.

## Steps

### Step 1 — List candidates

Coach: explain that this list is everything the maintainer has closed but QA has not yet
verified. It is the tester's queue for this mission.
Tester does:

```bash
gh issue list --state closed --search "label:found-by-qa -label:qa-verified"
```

Expected: a list, possibly empty.
If different: nothing to verify. Record "none open" in the progress file and stop.

### Step 2 — Re-run each issue's repro on current `main`

Coach: walk the tester through the issue's repro one at a time. For each one, the tester reads
the original repro, switches to a clean `main`, syncs, and runs the same command. Save the
output.
Tester does, for each issue:

```bash
gh issue view <N>
git switch main
git pull
uv sync --frozen --extra dev
# the exact command from the issue body
<repro-command> 2>&1 | tee qa/.work/evidence/T3-04-<N>.txt
```

Expected: the repro either no longer reproduces the defect (fixed) or still reproduces it
(not fixed).
If different: the tester's run diverges from the original repro. Re-read the issue, fix the
command, and re-run before recording a verdict.

### Step 3 — Post the verdict and update the label

Coach: if the bug is gone, draft a comment with the command, the output, and the `main` commit.
Have the tester approve it, then post and add `qa-verified`. If the bug is still there, draft a
comment with the evidence, reopen the issue, and let the maintainer decide.
Tester does, when fixed:

```bash
$EDITOR qa/.work/notes/verify-<N>.md    # command, output, commit
gh issue comment <N> --body-file qa/.work/notes/verify-<N>.md
gh issue edit <N> --add-label qa-verified
```

Expected: the comment is posted and the label is set. The issue is now closed and verified.
If different: the body was edited after approval, or the label is wrong. Stop, repost, confirm.
Tester does, when not fixed:

```bash
$EDITOR qa/.work/notes/verify-<N>.md    # command, output, evidence the bug is still there
gh issue comment <N> --body-file qa/.work/notes/verify-<N>.md
gh issue reopen <N>
```

Expected: the comment is posted, the issue is reopened, and the evidence is in the comment so
the maintainer can act.
If different: the body was edited after approval, or the evidence is missing. Stop, repost, fix.

## What counts as a finding

- A closed `found-by-qa` issue whose repro still reproduces on `main`. Reopen with evidence.
- A closed issue whose fix landed on a branch but is not on `main`. Wait, then re-run.
- A `found-by-qa` issue the maintainer closed without a fix, labeled `duplicate`, `invalid`, or
  `wontfix`. Record the closure in the progress file under "Questions for the maintainer".

## Done when

- Every candidate from step 1 has either a posted `qa-verified` comment or a posted reopen
  comment, with the command, the output, and the `main` commit quoted.
- The tester approved each post before it went out.

## Record in progress

Add a `Completed` row for T3-04 with each issue number and the verdict (verified or reopened).
Set `Current item` to the next item the
[mission list](../../missions/README.md#how-the-coach-picks-the-next-item) picks. End the session
log with any fixes the tester wants to follow up on. Then:

```bash
git switch main
uv sync --frozen --extra dev
```
