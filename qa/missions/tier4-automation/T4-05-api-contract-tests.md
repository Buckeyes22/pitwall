# T4-05 — API contract tests

**Tier:** 4 | **Mode:** work | **Repeatable:** yes | **Needs:** T4-03 | **Output:** pull request

## Why this matters

T2-02 and T2-03 walked the REST API through its error shapes and locks. Each one is a small
contract: a status code, an error envelope, a scope check. If the contract drifts, every script
that talks to the API breaks quietly. This mission turns one of those findings into a test that
fails in CI the moment someone changes the shape.

## Sources

- [Route inventory](../../../docs/sdlc/02-api-rest.md#3-route-inventory) and
  [failure modes and error types](../../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types)
- Helpers in `tests/api/_contract_helpers.py` (`build_app`, `client_for`, `override`); models
  such as `test_error_envelope.py` and `test_admin_auth_matrix.py` in `tests/api/`
- [Where tests go](../../handbook/test-automation.md#where-tests-go) and
  [find then fix](../../handbook/test-automation.md#find-then-fix)
- The tester's T2-02 and T2-03 notes in `qa/.work/notes/`

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). In tier 4
change test files only, on a `test/<short-name>` branch (rule R8). The temporary break in step 3
is never committed. Use only the placeholder values in the README Quick Start.

## Setup

The contract tests build the app in memory through `build_app`, so the test stack is not needed.

```bash
git switch main && git pull
git switch -c test/<short-name>
ls tests/api
```

Expected: a new branch `test/<short-name>`, and `tests/api` lists `_contract_helpers.py` and the
contract test files. The short name says what is protected, for example `api-lease-404`.

## Steps

### Step 1 — Pick one uncovered error shape or authorization case

Coach: open the T2-02 and T2-03 notes and pick one case small enough for one test function.
Examples: `lease_not_found` from `GET /v1/leases/{id}`, `budget_exhausted` from
`POST /v1/serve`, or `401` from an admin route with the wrong secret. Confirm no test asserts it:

```bash
grep -rn '<status code or error code>' tests/api | tee qa/.work/evidence/T4-05-grep.txt
```

Expected: no existing test asserts this exact status and error code. Note the choice.
If different: a test already covers it. Pick another case and grep again.

### Step 2 — Write the test from a template

Coach: list the tests in the file the new test will join and read one that covers a similar
route: how it builds the app, overrides a dependency, and asserts. Copy it, rename it after the
new contract, and change the path, body, override, and assertions. Keep the file's imports,
fixtures, and `clear_app_module` use.
Tester does: `grep -n 'def test_' tests/api/<file>.py`, then `$EDITOR tests/api/<file>.py`.
Expected: one new `test_*` function that asserts the status (for example
`assert resp.status_code == 404`) and the envelope (for example
`assert resp.json()["error"] == "lease_not_found"`), with no real network or database calls.

### Step 3 — Prove the test fails when the contract is broken

Coach: follow the [find then fix](../../handbook/test-automation.md#find-then-fix) recipe. Break
the contract by hand in the source file (change the status or error code the test checks), run
the test, watch it fail, then restore the file.
Tester does:

```bash
$EDITOR src/pitwall/<source-file>.py
uv run pytest -q tests/api/<file>.py::test_<new-test-name> 2>&1 \
  | tee qa/.work/evidence/T4-05-fail.txt | tail -15
echo "exit=${PIPESTATUS[0]}"
git checkout -- src/pitwall/<source-file>.py
uv run pytest -q tests/api/<file>.py::test_<new-test-name> 2>&1 \
  | tee qa/.work/evidence/T4-05-pass.txt | tail -3
echo "exit=${PIPESTATUS[0]}"
git status --short
```

Expected: the first run fails with `exit=1`, the second shows `1 passed` and `exit=0`, and
`git status --short` lists only the test file.
If different: a test that passes while the contract is broken has too loose an assertion;
tighten it. A test that fails with the contract intact is wrong; re-read the failure-modes table.

### Step 4 — Pre-PR routine and pull request

Coach: run the [pre-PR routine](../../handbook/test-automation.md#pre-pr-routine), then draft
the description.
Tester does:

```bash
uv run ruff check .
uv run ruff format --check .
make test 2>&1 | tee qa/.work/evidence/T4-05-make-test.txt | tail -15
git add tests/api/<file>.py
git commit -s -m "test: <what the test protects>"
git log -1 --format='%(trailers:key=Signed-off-by)'
```

Expected: ruff is clean, `make test` has no failures, and the last command prints `Signed-off-by:`.
If different: fix a failure before pushing. A commit without `-s` is a blocker.

Draft the description in `qa/.work/notes/<short-name>-pr.md` from
`.github/pull_request_template.md`, with `Refs #<N>` for the T2 finding if one was filed. After
the tester approves the draft:

```bash
git push -u origin test/<short-name>
gh pr create --title "test: <what the test protects>" --body-file qa/.work/notes/<short-name>-pr.md
```

Expected: the pull request URL is printed and CI starts.

### Step 5 — Watch CI and address feedback

Coach: read failures the way the
[handbook](../../handbook/test-automation.md#reading-ci-failures) describes.
Tester does: `gh pr checks`, then `gh run view <run-id> --log-failed | tail -60`.
Expected: all checks green, or a first failure that is in the new test and fixable. A failure in
a route or envelope the tester did not touch is a finding: file it and tell the maintainer.

## What counts as a finding

- A test that passes when the contract is broken (too loose an assertion).
- A flaky test that fails one run and passes the next, with no code change between.
- A route whose real status or envelope differs from the failure-modes table.

## Done when

- The new test failed with the contract broken and passed with it restored.
- The pre-PR routine passed locally and the pull request is open with green CI.
- Every review comment has a reply.

## Record in progress

Add a `Completed` row for T4-05 with the pull request URL and the T2 finding it covers. Set
`Current item` to the next uncovered case or the next repeatable mission. End the session log
with what the test protects.
