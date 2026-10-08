# T4-06 — Journey harness assertions

**Tier:** 4 | **Mode:** work | **Repeatable:** yes | **Needs:** T4-03 | **Output:** pull request

## Why this matters

The user-journey catalog says what each hermetic journey proves, and the harness script turns
each row into a `jNN()` shell function. T1-05 found rows whose function checks less than the
catalog claims. This mission adds the missing check, so the harness catches that regression the
next time anyone runs it.

## Sources

- [User-journey catalog](../../../docs/operator/user-journey-catalog.md)
- The harness: `scripts/release/run-user-journeys.sh`
- [Full journey harness](../../handbook/regression-and-release.md#full-journey-harness) and
  [pre-PR routine](../../handbook/test-automation.md#pre-pr-routine)
- The tester's T1-05 journal in `qa/.work/notes/t1-05-journal.md`

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). In tier 4
change test files only, on a `test/<short-name>` branch (rule R8). The harness is test code; the
catalog is a doc, so loose catalog wording is filed as a `documentation` issue, not edited. The
harness resets the database it is given, so it runs only against the local test stack.

## Setup

Start the branch, bring up the local test stack, and set the five README `export` lines in this
terminal. The harness stops if one of its ports is already taken.

```bash
git switch main && git pull
git switch -c test/<short-name>
docker compose -f docker-compose.testinfra.yml up -d --wait
ss -tln | grep -E ':(18080|1808[2-5]|18090) '
```

Expected: a new branch, both stack services healthy, and no output from `ss`. The short name
says what is protected, for example `j09-tui-views`.
If different: a listed port belongs to a leftover process; stop it before continuing.

## Steps

### Step 1 — Pick one row from the T1-05 journal

Coach: pick one row where `checked: no` because the function skips a catalog claim, small enough
for one extra check.
Tester does:

```bash
grep -n 'checked: no' qa/.work/notes/t1-05-journal.md | tee qa/.work/evidence/T4-06-pick.txt
```

Expected: at least one row. The tester notes the journey ID and the claim the check will prove.
If different: every row says `yes`; pick another repeatable mission.

### Step 2 — Read the function

Coach: read the chosen function together. Point out the helpers it uses (`http_code`,
`json_assert`, `db_query`, `db_expect`, `wait_for_url`) and the `ok=0` flag that
`[ ${ok} -eq 1 ] && journey_pass` checks at the end.
Tester does:

```bash
sed -n '/^j<nn>() {/,/^}/p' scripts/release/run-user-journeys.sh | tee qa/.work/evidence/T4-06-style.txt
```

Expected: the whole function, ending in the `journey_pass` line.

### Step 3 — Add the check

Coach: add one check just before the `journey_pass` line, with the same helpers and quoting,
setting `ok=0` when the claim is false. Change nothing else.
Tester does: `$EDITOR scripts/release/run-user-journeys.sh`, then `bash -n scripts/release/run-user-journeys.sh`.
Expected: the function grew by a few lines, and `bash -n` prints nothing (no syntax errors).

### Step 4 — Prove the check can fail

Coach: the harness runs every journey; only J23, J24, J25, and J27 run on their own. So change
the new check's expected value to something wrong, run the whole harness, and look for this
journey's `FAIL` line. A run takes several minutes.
Tester does:

```bash
bash scripts/release/run-user-journeys.sh 2>&1 | tee qa/.work/evidence/T4-06-fail.txt | tail -40
echo "exit=${PIPESTATUS[0]}"
```

Expected: a `FAIL` line for the touched journey, every other journey `PASS`, and a nonzero exit.
If different: a touched journey that passes with the wrong value is not running the new check;
re-read where it sits in the function.

### Step 5 — Put the right value back and run again

Coach: the second full run proves the check passes and that the edit broke no other journey.
Tester does: restore the right expected value, then the same command, saving to
`qa/.work/evidence/T4-06-journeys.txt`.
Expected: a summary ending in `<n> passed, 0 failed`, then `exit=0`.
If different: a `FAIL` anywhere means stop and read that journey's log before pushing.

### Step 6 — Pre-PR routine and pull request

Coach: run the [pre-PR routine](../../handbook/test-automation.md#pre-pr-routine). CI does not run
the journey harness, so the pull request carries the local summary line as evidence.
Tester does:

```bash
make test 2>&1 | tee qa/.work/evidence/T4-06-make-test.txt | tail -15
git add scripts/release/run-user-journeys.sh
git commit -s -m "test: <journey> asserts <claim>"
git log -1 --format='%(trailers:key=Signed-off-by)'
```

Expected: `make test` shows no failures, and the last command prints a `Signed-off-by:` line.
Then draft `qa/.work/notes/<short-name>-pr.md` from `.github/pull_request_template.md`, with the
journey ID, the step 5 summary line, and `Refs #<N>` for the T1-05 finding. After the tester
approves it:

```bash
git push -u origin test/<short-name>
gh pr create --title "test: <journey> asserts <claim>" --body-file qa/.work/notes/<short-name>-pr.md
gh pr checks
```

Expected: the pull request URL, then the checks starting. Answer every review comment with
evidence.

## What counts as a finding

- A catalog claim that the product does not meet, found when the new check fails on `main`.
- Catalog wording that is loose or wrong: a `documentation` issue, not an edit.
- A harness summary of `0 failed` with a nonzero exit, or the reverse.

## Done when

- The new check failed with a wrong expected value and passed with the right one.
- The full harness ended with `0 failed` and `exit=0`, and that line is in the pull request.
- The pull request is open with green checks and every review comment answered.

## Record in progress

Add a `Completed` row for T4-06 with the pull request URL and the journal row it covers. Set
`Current item` to the next `checked: no` row or the next repeatable mission. Stop the stack with
`docker compose -f docker-compose.testinfra.yml down`. End the session log with the new check.
