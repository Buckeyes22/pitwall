# T4-02 — The pre-PR routine and reading CI

**Tier:** 4 | **Mode:** training | **Repeatable:** no | **Needs:** tier 4 unlocked | **Output:** progress entry

## Why this matters

Every pull request must pass the same gates CI runs, and be signed off. Knowing the routine
keeps a tester from pushing something CI would reject. Reading a CI job's log is the same skill
the tester needs when a pull request breaks.

## Sources

- [Quality gates](../../../CONTRIBUTING.md#quality-gates), [PR process](../../../CONTRIBUTING.md#pr-process).
- [Pre-PR routine](../../handbook/test-automation.md#pre-pr-routine), [reading CI failures](../../handbook/test-automation.md#reading-ci-failures).

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). In tier 4 you
may change test files only, on a `test/<short-name>` branch (R8). This mission makes no test
changes; it makes a throwaway `test/practice-*` branch and deletes it at the end. Nothing is
pushed.

## Setup

The tester is on `main` with `qa/.work/progress.md` up to date. Dependencies are installed (`uv
sync --frozen --extra dev`). The five `export` lines from the README Quick Start are set. `gh` is
authenticated. `make test` is hermetic; no stack or Docker is required for the gates in this
mission.

## Steps

### Step 1 — Make a practice branch

Coach: explain that the practice branch exists only to be deleted. Its name contains `$USER`,
which the shell expands to the tester's shell user name.
Tester does:

```bash
git switch -c test/practice-$USER
```

Expected: `Switched to a new branch 'test/practice-<name>'`.
If different: `$USER` did not expand. Use `echo $USER` to check the shell, then run the command
with the actual user name spelled out.

### Step 2 — Lint and format

Coach: ruff is the lint and format tool CI runs. Both commands must come back clean.
Tester does:

```bash
uv run ruff check . 2>&1 | tee qa/.work/evidence/T4-02-ruff-check.txt | tail -5
uv run ruff format --check . 2>&1 | tee qa/.work/evidence/T4-02-ruff-format.txt | tail -5
```

Expected: `All checks passed!` from the first command and `<n> files already formatted` from the
second.
If different: ruff reports a finding. If it is in code the tester did not write, file it with
`found-by-qa` and the matching severity; if it is in code the tester wrote, fix it before
continuing.

### Step 3 — Run the hermetic test gate

Coach: `make test` is the fast hermetic lane. It must pass before any push.
Tester does:

```bash
make test 2>&1 | tee qa/.work/evidence/T4-02-make-test.txt | tail -15
```

Expected: a summary line with `passed` and no `failed`.
If different: a test fails. Read the last twenty lines of the evidence file. If it is a real
defect on `main`, file it; if it looks like an environment problem, fix the env and re-run.

### Step 4 — Read a recent CI run

Coach: the point of this step is to learn what a CI job looks like. Pick the most recent merged
pull request, then a job from one of its runs.
Tester does:

```bash
gh pr list --state merged --limit 3
```

Pick a pull request number, then:

```bash
gh pr checks <N>
gh run list --limit 3
```

Pick a run id, then:

```bash
gh run view <run-id> --log 2>&1 | tee qa/.work/evidence/T4-02-ci-log.txt | tail -30
```

Expected: `gh pr checks` lists the pull request's jobs and their results. `gh run list` shows the
three most recent runs with their workflow, branch, and status. `gh run view --log` shows the
commands each job ran, and the tester can describe one job in plain words.
If different: `gh` is not authenticated or the pull request number is wrong. Re-check with
`gh auth status` and `gh pr view <N>`, then continue.

### Step 5 — Tear the practice branch down

Coach: the branch must be gone, and nothing must have left the local clone.
Tester does:

```bash
git switch main
git branch -D test/practice-$USER
```

Expected: the switch back to `main` succeeds, and `git branch -D` prints `Deleted branch
test/practice-<name>`.
If different: the branch is checked out in another worktree. Run `git worktree list` to find
it, then either remove the worktree or delete the branch from a clean checkout.
Then confirm:

```bash
git branch
git status --short
```

Expected: the practice branch is not listed, and `git status --short` is empty.
If different: an unstaged change remains. Find it with `git diff --name-only` and revert it.

## What counts as a finding

- A failing `make test`, `uv run ruff check`, or `uv run ruff format --check` on `main`. File it.
- A CI gate that disagrees with what [CONTRIBUTING.md](../../../CONTRIBUTING.md#quality-gates)
  calls required (a gate the doc does not list, or a required gate missing from CI). File it.
- A CI job whose commands differ from the local pre-PR routine.

## Done when

- The practice branch was created and deleted.
- Ruff lint, ruff format, and `make test` each passed and the output was saved.
- One CI run's log was read; the tester can describe what one job ran.
- `git status --short` is clean at the end.

## Record in progress

Add a `Completed` row for T4-02 with output links to the four evidence files. Set `Current item`
to [T4-03 — Your first regression test](T4-03-first-regression-test.md). End the session log
with what the tester learned about CI and any open questions.
