# T4-04 — CLI contract tests

**Tier:** 4 | **Mode:** work | **Repeatable:** yes | **Needs:** T4-03 | **Output:** pull request

## Why this matters

T2-05 walked the CLI off the happy path and found error paths no test protects. Each one is a
small regression risk: a flag rename, exit-code change, or parser tweak could silently break
the contract. This mission turns one finding into a contract test, so the next person who
breaks it sees the failure in CI.

## Sources

- [Command inventory](../../../docs/sdlc/18-cli.md#3-command-inventory),
  [exit codes](../../../docs/sdlc/18-cli.md#exit-codes), and
  [failure modes](../../../docs/sdlc/18-cli.md#6-failure-modes--error-types)
- The existing suite in `tests/cli/`, for example `test_cli_dispatch.py` and `test_cli_output.py`
- [Find then fix](../../handbook/test-automation.md#find-then-fix) and
  [pre-PR routine](../../handbook/test-automation.md#pre-pr-routine)
- The tester's T2-05 notes in `qa/.work/notes/`

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). In tier 4
change test files only, on a `test/<short-name>` branch (rule R8). The temporary break in step 3
is never committed. Use only the placeholder values in the README Quick Start.

## Setup

CLI tests are hermetic, so the test stack is not required. Start the branch from current `main`.

```bash
git switch main && git pull
git switch -c test/<short-name>
ls tests/cli
```

Expected: a new branch `test/<short-name>`, and `tests/cli` lists `test_cli_dispatch.py` among
other CLI test files. The short name says what is protected, for example `cli-init-missing-flag`.

## Steps

### Step 1 — Pick one uncovered error path

Coach: open the T2-05 notes and pick one error path small enough for one test function, for
example `pitwall init` with a missing seed path. Confirm no test already asserts it:

```bash
grep -rn '<flag or message>' tests/cli | tee qa/.work/evidence/T4-04-grep.txt
```

Expected: no existing test asserts this exact flag or message. Note the choice in the notes file.
If different: a test already covers it. Pick another path and grep again.

### Step 2 — Write the test in the file's style

Coach: open the test file the new function will live in. Pick one existing test that covers a
nearby error path and use it as a template: copy it, rename it, then change the arguments and
the assertion. Keep the file's imports, fixtures, and assert style.
Tester does: `$EDITOR tests/cli/<file>.py`.
Expected: one new `test_*` function that drives `pitwall.cli.main` or a `cmd_*` function and
asserts on the exit code, the error message, or both. It touches no network and no database.

### Step 3 — Prove the test fails when the contract is broken

Coach: follow the [find then fix](../../handbook/test-automation.md#find-then-fix) recipe. Break
the contract by hand in the source file (change the exit code or message the test checks), run
the test, watch it fail, then restore the file.
Tester does:

```bash
$EDITOR src/pitwall/<source-file>.py
uv run pytest -q tests/cli/<file>.py::test_<new-test-name> 2>&1 \
  | tee qa/.work/evidence/T4-04-fail.txt | tail -15
echo "exit=${PIPESTATUS[0]}"
git checkout -- src/pitwall/<source-file>.py
uv run pytest -q tests/cli/<file>.py::test_<new-test-name> 2>&1 \
  | tee qa/.work/evidence/T4-04-pass.txt | tail -3
echo "exit=${PIPESTATUS[0]}"
git status --short
```

Expected: the first run fails with `exit=1`, the second shows `1 passed` and `exit=0`, and
`git status --short` lists only the test file.
If different: a test that passes while the contract is broken has too loose an assertion;
tighten it. A test that fails with the contract intact is wrong; re-read the contract.

### Step 4 — Pre-PR routine and pull request

Coach: run the [pre-PR routine](../../handbook/test-automation.md#pre-pr-routine), then draft
the description.
Tester does:

```bash
uv run ruff check .
uv run ruff format --check .
make test 2>&1 | tee qa/.work/evidence/T4-04-make-test.txt | tail -15
git add tests/cli/<file>.py
git commit -s -m "test: <what the test protects>"
git log -1 --format='%(trailers:key=Signed-off-by)'
```

Expected: ruff is clean, `make test` has no failures, and the last command prints `Signed-off-by:`.
If different: fix a failure before pushing. A commit without `-s` is a blocker.

Draft the description in `qa/.work/notes/<short-name>-pr.md` from
`.github/pull_request_template.md`, with `Refs #<N>` for the T2-05 finding if one was filed.
After the tester approves the draft:

```bash
git push -u origin test/<short-name>
gh pr create --title "test: <what the test protects>" \
  --body-file qa/.work/notes/<short-name>-pr.md
```

Expected: the pull request URL is printed and CI starts.
If different: usually an authentication problem; check `gh auth status` and retry.

### Step 5 — Watch CI and address feedback

Coach: read failures the way the
[handbook](../../handbook/test-automation.md#reading-ci-failures) describes.
Tester does:

```bash
gh pr checks
gh run view <run-id> --log-failed | tail -60 | tee qa/.work/evidence/T4-04-ci.txt
```

Expected: all checks green, or a first failure that is in the new test and fixable.
If different: a failure in code the tester did not touch is a finding: file it and tell the
maintainer.

## What counts as a finding

- A test that passes when the contract is broken (too loose an assertion).
- A flaky test that fails one run and passes the next, with no code change between.
- A CI gate that disagrees with `CONTRIBUTING.md`.

## Done when

- The new test failed with the contract broken and passed with it restored.
- The pre-PR routine passed locally and the pull request is open with green CI.
- Every review comment has a reply.

## Record in progress

Add a `Completed` row for T4-04 with the pull request URL and the T2-05 finding it covers. Set
`Current item` to the next uncovered T2-05 path or the next repeatable mission. End the session
log with what the test protects.
