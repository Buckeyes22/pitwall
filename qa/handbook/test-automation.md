# Test automation

Tier 4 work: turning findings into automated tests and opening test pull requests. Rule R8 still
applies. Change test files only, on a feature branch.

## Where tests go

- Root project tests live in `tests/`, grouped by area to match `src/pitwall/`: for example
  `tests/cli/`, `tests/api/`, `tests/property/`, `tests/security/`.
- Put a new test next to the existing tests for the same surface and copy their style and helpers
  (for example `tests/api/_contract_helpers.py`).
- Tests are hermetic by default. `tests/conftest.py` and `tests/_hermetic_env.py` set placeholder
  values and block real provider hosts. Fakes live in `tests/fakes/`.
- Markers choose the lane (see `docs/sdlc/17-testing-strategy.md`,
  [markers and isolation](../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation)). A test that
  needs PostgreSQL or Redis is marked `integration`.
- `tests/agents/` covers Agent Routing and runs with the rest of the suite; run just it with
  `uv run pytest tests/agents -q -p no:randomly -p no:cov | tail -40`.

## Find then fix

Every production bug fix ships with a regression test (`CONTRIBUTING.md`, Definition of Done). A
regression test must fail when the bug is present. To prove it, temporarily undo the fix in the
source file, run the test, and watch it fail:

```bash
git diff <fix-commit>^ <fix-commit> -- <source-file> | git apply -R
uv run pytest -q <test-file>::<test-name>
git checkout -- <source-file>
uv run pytest -q <test-file>::<test-name>
```

Expected: the first run fails, and the second run passes. The source file is never committed.
With no fix commit to undo, as for a contract test, break the checked behavior by hand instead,
then restore the file with the same `git checkout`.

## Pre-PR routine

```bash
git switch main && git pull
git switch -c test/<short-name>
uv run ruff check .
uv run ruff format --check .
make test 2>&1 | tail -15
git add <test-files>
git commit -s -m "test: <what the test protects>"
git log -1 --format='%(trailers:key=Signed-off-by)'
```

Expected: ruff reports no errors, `make test` shows no failures, and the last command prints a
`Signed-off-by:` line. If you touched an integration test, run the integration lane too.

## Opening the pull request

1. Draft the description in `qa/.work/notes/<short-name>-pr.md` using the sections of
   `.github/pull_request_template.md`. Link the issue with `Refs #N`.
2. After the tester approves the draft:

   ```bash
   git push -u origin test/<short-name>
   gh pr create --title "test: <what the test protects>" --body-file qa/.work/notes/<short-name>-pr.md
   ```

3. Watch CI with `gh pr checks <N>` and answer review comments.

## Reading CI failures

```bash
gh pr checks <N>
gh run view <run-id> --log-failed | tail -60
```

Find the first real error, reproduce it locally with the same command, fix it, commit with `-s`,
and push again.
