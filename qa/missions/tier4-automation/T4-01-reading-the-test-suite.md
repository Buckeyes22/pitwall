# T4-01 — Reading and running the test suite

**Tier:** 4 | **Mode:** training | **Repeatable:** no | **Needs:** tier 4 unlocked | **Output:** progress entry

## Why this matters

Before writing tests, learn how this suite is laid out and how to run one piece of it. The tests
folder is large, markers control what runs, and hermetic helpers keep real credentials out. A
tester who knows these three things can answer "did this break?" without guessing.

## Sources

- [Markers and isolation](../../../docs/sdlc/17-testing-strategy.md#1-markers-and-isolation), [verification tracks](../../../docs/sdlc/17-testing-strategy.md#2-verification-tracks), [local infrastructure](../../../docs/sdlc/17-testing-strategy.md#3-local-infrastructure).
- Shared fixtures: `tests/conftest.py`, `tests/_hermetic_env.py`, and `tests/fakes/`.
- [Test lanes and markers](../../concepts/test-lanes-and-markers.md).

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). In tier 4 you
may change test files only, on a `test/<short-name>` branch (R8). The edits in this mission are
temporary: any change to an existing test is reverted before the mission ends. Nothing is pushed.

## Setup

The tester is on `main` with `qa/.work/progress.md` up to date. Dependencies are installed (`uv
sync --frozen --extra dev`). The five `export` lines from the README Quick Start are set in the
terminal.

## Steps

### Step 1 — Tour the tests folder

Coach: `tests/` is grouped by area, mirroring `src/pitwall/`. Folders and a few top-level files sit side by side.
Tester does:

```bash
ls tests | head -40
```

Expected: folders named after areas (`api`, `cli`, `property`, `security`, ...) and several
top-level `test_*.py` files.
If different: stop and check `pwd`. The command runs from the repository root.

### Step 2 — Read the markers list

Coach: markers gate which tests run on which command. Reading the list is faster than guessing.
Tester does:

```bash
grep -n -A14 'markers = \[' pyproject.toml
```

Expected: the marker list with `live`, `integration`, `security`, `fuzz`, `property`, `chaos`, `slow`, `benchmark`, `release`, and `asyncio`, each with a one-line purpose.
If different: a custom marker the tester has not seen. Add it to "Questions for the maintainer" and continue.

### Step 3 — Read the hermetic environment module

Coach: this module strips real credentials and sets placeholder values before any test imports `pitwall`. That is why the suite never accidentally calls RunPod.
Tester does:

```bash
sed -n '1,30p' tests/_hermetic_env.py
```

Expected: `HERMETIC_REQUEST_BEHAVIOR_ENV_VARS` (auth, rate-limit, webhook, budget-kill variables) and defaults for `RUNPOD_API_KEY`, `DATABASE_URL`, `REDIS_URL`. Auth variables are popped; connection variables fall back to the local test DSN.
If different: read the rest with `wc -l` and `sed -n '31,$p'`, then continue.

### Step 4 — Run one file

Coach: CLI output tests are pure unit tests, no database, no network. Start there.
Tester does:

```bash
uv run pytest -q tests/cli/test_cli_output.py 2>&1 | tee qa/.work/evidence/T4-01-cli-output.txt | tail -3
```

Expected: a summary with `passed` and no `failed`.
If different: read `tail -20 qa/.work/evidence/T4-01-cli-output.txt`. Real defect → file it;
environment problem → fix the env and re-run.

### Step 5 — Collect and run one test id

Coach: `--collect-only` lists ids without running. Pick one, run it by id, and prove the result came from that single test.
Tester does:

```bash
uv run pytest --collect-only -q tests/cli/test_cli_output.py | head -5
```

Pick one id, then:

```bash
uv run pytest -q "<chosen-id>" 2>&1 | tee qa/.work/evidence/T4-01-single-id.txt | tail -3
```

Expected: a summary with `1 passed`.
If different: keep the whole id in quotes, brackets included, or pass part of the test name with
`pytest -k <substring>`.

### Step 6 — Run the security lane

Coach: the security lane is hermetic. The marker expression excludes the fuzz tests that take longer.
Tester does:

```bash
uv run pytest -q -m "security and not fuzz" tests/security 2>&1 | tee qa/.work/evidence/T4-01-security.txt | tail -3
```

Expected: a summary with `passed` and no `failed`.
If different: read `tail -30 qa/.work/evidence/T4-01-security.txt`. Real defect → file it with `found-by-qa`, the matching `severity:` label, and `bug`. Environment problem → fix and re-run.

### Step 7 — Break a test, then restore the tree

Coach: prove the suite actually catches a wrong value. Edit one assertion, run it, see the diff, revert with `git checkout`. The reverted tree is the mission's exit state.
Tester does:

```bash
$EDITOR tests/cli/test_cli_output.py     # change one expected value
uv run pytest -q tests/cli/test_cli_output.py 2>&1 | tee qa/.work/evidence/T4-01-broken.txt | tail -20
```

Expected: a failure with an assertion diff (an `assert` line and the actual value).
If different: nothing failed. Make a smaller, surgical edit and re-run.
Revert and confirm:

```bash
git checkout -- tests/cli/test_cli_output.py
uv run pytest -q tests/cli/test_cli_output.py 2>&1 | tee qa/.work/evidence/T4-01-restored.txt | tail -3
git status --short
```

Expected: `passed`, then `git status --short` prints nothing.
If different: an unstaged change remains. Find it with `git diff --name-only`, revert it, and re-check `git status --short`.

## What counts as a finding

- A passing test that should fail (the suite is silently wrong).
- A flaky result from any step above (a re-run with the same code produces a different verdict).
- A marker, file path, or behavior that contradicts [`17-testing-strategy.md`](../../../docs/sdlc/17-testing-strategy.md) or the [`pyproject.toml`](../../../pyproject.toml) markers list.

## Done when

`ls tests | head -40` ran and the folder grouping is in the notes. The markers list is in the notes,
one line per marker. `tests/_hermetic_env.py` was read; the auth variable list and DSN defaults are
noted. Steps 4, 5, 6, and 7 each saved output to `qa/.work/evidence/T4-01-*.txt`.
`git status --short` is clean at the end of step 7.

## Record in progress

Add a `Completed` row for T4-01 with output links to the five evidence files. Set `Current item` to [T4-02 — The pre-PR routine and reading CI](T4-02-pre-pr-routine.md). End the session log with what the tester found easy and what still feels unclear.
