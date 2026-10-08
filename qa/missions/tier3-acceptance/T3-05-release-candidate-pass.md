# T3-05 — Release candidate regression pass

**Tier:** 3 | **Mode:** work | **Repeatable:** yes | **Needs:** tier 3 unlocked | **Output:** sign-off report

## Why this matters

Before a release, someone must run every hermetic gate and the journeys and sign off with
evidence. That someone is the tester on tier 3. A release that goes out without this pass can
land a regression that the unit tests missed.

## Sources

- [Release-testing checklist](../../../docs/operator/release-testing-checklist.md).
- [Release candidate pass](../../handbook/regression-and-release.md#release-candidate-pass) and
  [sign-off report](../../handbook/regression-and-release.md#sign-off-report).
- [Full journey harness](../../handbook/regression-and-release.md#full-journey-harness),
  [evidence standard](../../handbook/evidence-standard.md).

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Live tiers
are never part of this pass. Do not run anything that needs a real provider key or a spend
budget; the checklist sections 1–7 are hermetic.

## Setup

- The tester is on `main` at the release commit, with `qa/.work/progress.md` up to date.
- The five `export` lines from the README Quick Start are set in the terminal.
- The local test stack is up (`docker compose -f docker-compose.testinfra.yml up -d --wait`).
- The maintainer has named where the sign-off report goes (release pull request, an issue, or
  the weekly check-in).

## Steps

### Step 1 — Confirm the release commit and prerequisites

Coach: have the tester pin down the exact commit under test and check the prerequisites in
section 0 of the checklist.
Tester does:

```bash
git rev-parse --short HEAD
git status
docker compose -f docker-compose.testinfra.yml up -d --wait
```

Expected: a clean tree at the named commit, and Postgres plus Redis up on the local test stack.
If different: stop and resolve before continuing. The sign-off is only honest if the commit is
the release commit.

### Step 2 — Run sections 1–7 of the checklist, in order

Coach: walk the tester through each section one at a time. Run the exact command from the
checklist, save the output, and note the result. Sections are hermetic and run in order; a
failing section blocks the next one.
Tester does, for each section:

```bash
# the exact command from the section
<section-command> 2>&1 | tee qa/.work/evidence/T3-05-section-<n>.txt
```

Expected: section 1 (lint, format, mypy) is clean. Section 2 (RunPod audit) exits 0. Section 3
(release tiers) uses the exact `-m release` marker and exits 0. Section 4 (kill-switch drill)
exits 0. Section 5 (coverage) reports at or above the 77% floor. Section 6 (security + mutation)
exits 0. Section 7 (the public-alpha harness) exits 0.
If different: a section fails. That is a blocker. Record the section, the command, and the
failing output in the sign-off report. The verdict is "not ready" until it is green or the
maintainer overrides.

### Step 3 — Run the full journey harness

Coach: remind the tester that the harness resets the database it is given, so it runs only
against the local test stack.
Tester does:

```bash
bash scripts/release/run-user-journeys.sh 2>&1 | tee qa/.work/evidence/T3-05-journeys.txt | tail -40
echo "exit=${PIPESTATUS[0]}"
```

Expected: a `journey summary` listing each journey, a final line `<n> passed, 0 failed`, and
`exit=0`. Ports 18080–18090 must be free first; the script stops if one is in use.
If different: a journey fails. Record the journey id, the command, and the failing output in
the sign-off report. The verdict is "not ready" until the maintainer decides what to do.

### Step 4 — Draft the sign-off report

Coach: walk the tester through the sign-off fields: the release commit, one row per section
(command, result, evidence excerpt), the journey harness summary, the open `found-by-qa` issues
by severity, and the verdict (ready or not ready with blockers listed).
Tester does:

```bash
git rev-parse --short HEAD
gh issue list --state open --label found-by-qa
$EDITOR qa/.work/notes/release-<tag-or-commit>-signoff.md
```

Expected: a draft with the commit, every section rowed, the journey summary, the open issues
list, and a verdict that matches the evidence.
If different: a row is missing or the verdict does not match the evidence. Fix it before
posting.

### Step 5 — Tester approval and post

Coach: read the draft aloud. Confirm the verdict matches the evidence row by row.
Tester does: says yes or asks for edits.
Expected: an explicit yes.
If different: edit and re-read. The post is not sent without approval.
Then post where the maintainer asked:

```bash
gh pr comment <release-pr> --body-file qa/.work/notes/release-<tag-or-commit>-signoff.md
# or
gh issue comment <N> --body-file qa/.work/notes/release-<tag-or-commit>-signoff.md
```

Expected: the sign-off is posted in the place the maintainer named.
If different: the post went to the wrong place or the body was edited after approval. Stop,
delete the wrong post if you can, and repost in the right place after re-approval.

## What counts as a finding

- A failed section in the checklist. It blocks the release until green or until the maintainer
  overrides.
- A failed journey in the harness. Same handling.
- A regression in a smoke check while running a section. File it with `found-by-qa`, the matching
  `severity:` label, and either `bug` or `documentation`.

## Done when

- Every section of the checklist ran on the release commit, and the output is saved in
  `qa/.work/evidence/`.
- The journey harness ran and the summary line is saved.
- The sign-off report has the commit, the section rows, the journey summary, the open issues
  list, and a verdict that matches the evidence.
- The report is posted where the maintainer asked, after the tester approved it.

## Record in progress

Add a `Completed` row for T3-05 with the release tag or commit and a link to the posted
sign-off. Set `Current item` to the next item the
[mission list](../../missions/README.md#how-the-coach-picks-the-next-item) picks. End the session
log with the verdict and any open blockers. Then:

```bash
git switch main
uv sync --frozen --extra dev
```
