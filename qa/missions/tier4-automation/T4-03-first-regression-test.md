# T4-03 — Your first regression test

**Tier:** 4 | **Mode:** training | **Repeatable:** no | **Needs:** tier 4 unlocked | **Output:** pull request

## Why this matters

Find, then fix: every production fix needs a test that fails without it. That test guards the code from the same bug coming back, and it makes the fix verifiable. This mission opens the tester's first regression pull request.

## Sources

- [Find then fix](../../handbook/test-automation.md#find-then-fix), [pre-PR routine](../../handbook/test-automation.md#pre-pr-routine), [opening the pull request](../../handbook/test-automation.md#opening-the-pull-request), and a closed `qa-verified` issue whose fix was a code change.

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Change test files only, on a `test/<short-name>` branch (R8). The temporary un-fix in step 5 is **never** committed: every revert runs before the next command, and `git status --short` shows only the new test file when step 5 ends. The tester approves the pull request description before it goes out (rule R9).

## Setup

Tester is on `main` with `qa/.work/progress.md` up to date. Dependencies installed (`uv sync --frozen --extra dev`). The five `export` lines from the README Quick Start are set. `gh` is authenticated.

## Steps

### Step 1 — Pick a closed `found-by-qa` issue with a code fix

Coach: a `documentation` fix is not the right pick; the fix must change product code.
Tester does:

```bash
gh issue list --state closed --label qa-verified --limit 10
```

Pick one whose fix changed code. Then:

```bash
gh issue view <N>
gh pr list --state merged --search "<N>" --limit 5
gh pr view <P> --json mergeCommit --jq .mergeCommit.oid
```

Expected: the fixing pull request `<P>` is in the list, and the last command prints a 40-character commit hash.
If different: no merged pull request mentions the issue. Pick another, or ask the maintainer for a starting issue.

### Step 2 — Branch from current `main`

Coach: a fresh branch from current `main` keeps the regression test independent of other in-flight work.
Tester does:

```bash
git switch main && git pull
git switch -c test/regression-<N>
```

Expected: on a fresh branch `test/regression-<N>`, with `main` pulled.
If different: `git pull` failed. If it complains about uncommitted changes, run `git status --short`, stash or commit them, and re-run.

### Step 3 — Write the hermetic regression test

Coach: the new test must live next to the existing tests for that surface and reuse the same helpers. It must fail when the bug is present and pass when the fix is present.
Tester does:

```bash
ls tests/<area>/
$EDITOR tests/<area>/<existing-file>.py     # or a new file in that folder
```

Expected: a new test added to the area's tests, reusing the surface's helpers and fakes; it does not call out to a real provider.
If different: the new test needs a real RunPod call. Wrap it in a fake from `tests/fakes/` or use the surface's helper.

### Step 4 — Confirm the new test passes with the fix in place

Coach: on the current branch the fix is in `main`. The test must pass.
Tester does:

```bash
uv run pytest -q <test-file>::<test-name> 2>&1 | tee qa/.work/evidence/T4-03-with-fix.txt | tail -3
```

Expected: `1 passed`.
If different: the test fails against the fix. Fix the assertion, re-run, and do not continue until it passes.

### Step 5 — Prove it catches the bug (the un-fix is never committed)

Coach: temporarily revert the fix in the source file, run the test, see it fail, then restore the source file. Mission invariant: `git status --short` shows only the new test file.
Tester does:

```bash
git diff <merge-commit-oid>^ <merge-commit-oid> -- <source-file> | git apply -R
uv run pytest -q <test-file>::<test-name> 2>&1 | tee qa/.work/evidence/T4-03-without-fix.txt | tail -10
```

Expected: `1 failed`, with the test name in the failure. If `git apply` reports a conflict, the file changed after the fix; ask the maintainer which lines to revert by hand.
If different: the test passed against the un-fixed source. Rewrite the assertion to use the same code path the bug touches, and re-run. Restore the source file:

```bash
git checkout -- <source-file>
uv run pytest -q <test-file>::<test-name> 2>&1 | tee qa/.work/evidence/T4-03-restored.txt | tail -3
git status --short
```

Expected: `1 passed`, then `git status --short` lists only the new test file.
If different: another file is dirty. Find it with `git diff --name-only`; if it is the source file, run `git checkout -- <source-file>` again; otherwise revert before continuing.

### Step 6 — Pre-PR routine, draft the PR, and post

Coach: run the gates from T4-02, then draft a description that links the issue with `Refs #<N>`. The tester approves before posting.
Tester does:

```bash
uv run ruff check .
uv run ruff format --check .
make test 2>&1 | tee qa/.work/evidence/T4-03-make-test.txt | tail -15
git add <test-file>
git commit -s -m "test: regression for #<N>"
```

Expected: ruff clean, `make test` shows `passed` and no `failed`, the commit prints a `Signed-off-by:` line.
If different: a gate fails. Fix it on this branch; do not amend a previous commit.
Draft and post:

```bash
cp .github/pull_request_template.md qa/.work/notes/regression-<N>-pr.md
$EDITOR qa/.work/notes/regression-<N>-pr.md    # title and body, including Refs #<N>
git push -u origin test/regression-<N>
gh pr create --title "test: regression for #<N>" --body-file qa/.work/notes/regression-<N>-pr.md
```

Expected: the push succeeds, the pull request URL is printed, and CI starts running.
If different: `git push` rejected the branch. Pull current `main` and rebase; re-run `make test`; push again. Watch CI and answer review comments:

```bash
gh pr checks <pr-number>
gh run view <run-id> --log-failed | tail -60
```

Expected: every required check is green, and any review comment gets a reply with evidence.
If different: a check fails. Reproduce the failure locally with the same command, fix it, commit with `-s`, push again.

## What counts as a finding

- A test that passes when the bug is present (the test does not actually guard the code).
- A flaky result on any of the gates.
- A pre-PR gate that disagrees with [CONTRIBUTING.md quality gates](../../../CONTRIBUTING.md#quality-gates).

## Done when

The test passes with the fix and fails when the fix is temporarily reverted. `git status --short` shows only the new test file after step 5. Ruff lint, ruff format, and `make test` all passed on the branch. The pull request is open, CI is green, and any review comments have an answer with evidence. The pull request description contains `Refs #<N>`.

## Record in progress

Add a `Completed` row for T4-03 with the pull request URL and a one-line summary. Set `Current item` to the next item the [mission list](../../missions/README.md#how-the-coach-picks-the-next-item) picks. End the session log with what the test caught, what stayed open, and the next mission.
