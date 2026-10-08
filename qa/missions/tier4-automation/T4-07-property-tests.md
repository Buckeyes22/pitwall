# T4-07 — Property tests with Hypothesis

**Tier:** 4 | **Mode:** work | **Repeatable:** yes | **Needs:** T4-03 | **Output:** pull request

## Why this matters

A unit test checks one hand-picked input. A property test checks a rule across many generated
inputs. When the rule is "the bucket never holds more than capacity" or "refill never goes
backwards in time", a property test finds the corner cases no human would write. This mission
adds one property test for a pure-logic rule the tester picks with the maintainer.

## Sources

- `tests/property/conftest.py`, plus `tests/property/test_token_bucket_properties.py` and
  `tests/property/test_arbitrage_properties.py` as models
- [Verification tracks](../../../docs/sdlc/17-testing-strategy.md#2-verification-tracks)
- [Where tests go](../../handbook/test-automation.md#where-tests-go) and
  [find then fix](../../handbook/test-automation.md#find-then-fix)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). In tier 4
change test files only, on a `test/<short-name>` branch (rule R8). The temporary break in step 4
is never committed. Property tests are hermetic; never set `RUNPOD_LIVE` or `PITWALL_RUN_LIVE`.

## Setup

Property tests need no test stack. Start the branch from current `main`.

```bash
git switch main && git pull
git switch -c test/<short-name>
ls tests/property
```

Expected: a new branch, and `tests/property` lists `conftest.py` and several
`test_*_properties.py` files. The short name names the rule, for example `property-budget-floor`.

## Steps

### Step 1 — Read the conftest and one property test

Coach: the conftest defines three Hypothesis profiles: `dev` (50 examples, the default), `ci`
(1000 examples), and `debug` (10 examples, verbose). `HYPOTHESIS_PROFILE` picks one. Then read a
model test together.
Tester does:

```bash
cat tests/property/conftest.py
sed -n '1,60p' tests/property/test_token_bucket_properties.py | tee qa/.work/evidence/T4-07-style.txt
```

Expected: the three `register_profile` calls, then a test file with
`pytestmark = pytest.mark.property`, some `st.*` strategies, and `@given(...)` tests.

### Step 2 — Pick one pure-logic rule with the maintainer

Coach: pure logic means no network, no database, no clock, and no randomness inside the function
under test. Examples: a budget gate that floors a ceiling, or a selector that never returns a
disabled item. Once the maintainer names the rule, find the function:

```bash
grep -rn 'def <function-name>' src/pitwall | tee qa/.work/evidence/T4-07-source.txt
```

Expected: the definition with its line number. The tester writes the rule as one plain sentence
in the notes, for example "`cooldown_duration_for_trip` never returns less than `initial_cooldown`".

### Step 3 — Write the property test

Coach: create `tests/property/test_<rule>_properties.py`. Copy the imports and the `pytestmark`
line from a model file. Build the strategies the rule needs (for example
`st.integers(min_value=0)` or `st.floats(allow_nan=False)`) and write one `@given(...)` test that
calls the function and asserts the rule with a clear message.
Tester does: `$EDITOR tests/property/test_<rule>_properties.py`.
Expected: one new file with `pytestmark = pytest.mark.property` and at least one `@given(...)`
test, with no network, database, or clock calls.

### Step 4 — Prove the test fails when the rule is broken

Coach: follow the [find then fix](../../handbook/test-automation.md#find-then-fix) recipe. Break
the rule by hand in the source file, run the new test with the strict `ci` profile, watch
Hypothesis find a counterexample, then restore the file.
Tester does:

```bash
$EDITOR src/pitwall/<source-file>.py
HYPOTHESIS_PROFILE=ci uv run pytest -q -p no:randomly tests/property/test_<rule>_properties.py \
  2>&1 | tee qa/.work/evidence/T4-07-fail.txt | tail -15
echo "exit=${PIPESTATUS[0]}"
git checkout -- src/pitwall/<source-file>.py
HYPOTHESIS_PROFILE=ci uv run pytest -q -p no:randomly tests/property/test_<rule>_properties.py \
  2>&1 | tee qa/.work/evidence/T4-07-pass.txt | tail -3
echo "exit=${PIPESTATUS[0]}"
git status --short
```

Expected: the first run shows a `Falsifying example` and `exit=1`; the second shows `passed` and
`exit=0`; `git status --short` lists only the new test file.
If different: a test that passes with the rule broken has too loose a strategy or assertion;
tighten it. A test that fails with the rule intact means the rule or the test is wrong.

### Step 5 — Pre-PR routine and pull request

Coach: run the [pre-PR routine](../../handbook/test-automation.md#pre-pr-routine), plus the whole
property lane the way CI runs it.
Tester does:

```bash
uv run ruff check .
uv run ruff format --check .
make test 2>&1 | tee qa/.work/evidence/T4-07-make-test.txt | tail -15
HYPOTHESIS_PROFILE=ci uv run pytest -m "property and not live" -p no:randomly 2>&1 | tail -3
git add tests/property/test_<rule>_properties.py
git commit -s -m "test: <what the property protects>"
git log -1 --format='%(trailers:key=Signed-off-by)'
```

Expected: ruff is clean, both test runs pass, and the last command prints `Signed-off-by:`.
Then draft `qa/.work/notes/<short-name>-pr.md` from `.github/pull_request_template.md`, quoting
the step 2 sentence as the rule under test. After the tester approves it:

```bash
git push -u origin test/<short-name>
gh pr create --title "test: <what the property protects>" --body-file qa/.work/notes/<short-name>-pr.md
gh pr checks
```

Expected: the pull request URL, then the checks starting. CI runs the property lane with the `ci`
profile; read any failure with `gh run view <run-id> --log-failed | tail -60`, the way the
[handbook](../../handbook/test-automation.md#reading-ci-failures) describes. A counterexample CI
finds in the function under test is a finding: file it and tell the maintainer.

## What counts as a finding

- A property test that passes when the rule is broken.
- A counterexample in the function under test, from CI or locally.
- A counterexample that does not reproduce with the seed Hypothesis prints.

## Done when

- The new test failed with the rule broken and passed with it restored.
- The pre-PR routine and the property lane passed locally, and the pull request has green CI.
- Every review comment has a reply.

## Record in progress

Add a `Completed` row for T4-07 with the pull request URL and the rule sentence. Set
`Current item` to the next rule the maintainer wants covered or the next repeatable mission. End
the session log with what the property protects.
