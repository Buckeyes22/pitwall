# Acceptance testing

Acceptance testing checks a pull request before it merges: does it do what it says, and did it
break anything nearby? The tester's review informs the maintainer. It never blocks or allows a
merge by itself.

## Maintainer side: requesting QA

1. Fill in the pull request's `## QA notes` section:
   - what changed for users
   - the acceptance criteria, or the plan and task numbers that list them
   - how to exercise it
   - risk areas worth exploring
   - anything that needs live credentials (the tester skips these)
2. Add the `needs-qa` label: `gh pr edit <N> --add-label needs-qa`.

## Tester side: the acceptance run

1. Find work: `gh pr list --label needs-qa`.
2. Read it: `gh pr view <N>`. Read the QA notes and the linked plan tasks.
3. Get the code: `git switch main && git pull`, then `gh pr checkout <N>`.
4. Sync: `uv sync --frozen --extra dev`.
5. Baseline: `make test 2>&1 | tail -15`. If it fails, stop and report the failure first.
6. For each acceptance criterion, run the check and save evidence to `qa/.work/evidence/`.
7. Explore around the changed area for a while ([exploratory testing](exploratory-testing.md)).
8. Run the smoke checks relevant to the change ([QA smoke set](regression-and-release.md#qa-smoke-set)).
9. Draft the report from `qa/templates/qa-report.md` in `qa/.work/notes/pr-<N>-qa.md`.
10. The tester approves the draft (rule R9), then posts it and swaps the label ([outcomes and labels](#outcomes-and-labels)).
11. File an issue for each defect ([bug reports](bug-reports.md)) and list the issues in the report.
12. Go back: `git switch main && uv sync --frozen --extra dev`.

## The QA report

The report uses `qa/templates/qa-report.md`:

- the verdict
- the commit tested
- the environment
- one row per acceptance criterion, each with a command, a result, and evidence
- exploratory notes
- the regression smoke results
- the issues filed
- questions for the maintainer
- anything skipped, and why

## Outcomes and labels

| Verdict | Review command | Label change |
| --- | --- | --- |
| Pass | `gh pr review <N> --approve --body-file <report>` | `gh pr edit <N> --remove-label needs-qa --add-label qa-passed` |
| Pass with issues | `gh pr review <N> --comment --body-file <report>` | `gh pr edit <N> --remove-label needs-qa --add-label qa-passed` |
| Fail | `gh pr review <N> --request-changes --body-file <report>` | `gh pr edit <N> --remove-label needs-qa --add-label qa-failed` |

A pull request without `needs-qa` (for example a dependency update) gets only the outcome label.

## Agent Routing pull requests

Agent Routing (`pitwall agents`) lives in the same project and environment. For a pull request that
changes it (`src/pitwall/agents/`, `tests/agents/`, `plugins/`), run its checks the way its contributing
guide says (`docs/agents/CONTRIBUTING.md`):

```bash
uv sync --frozen --extra dev --python 3.14.7
uv run pytest tests/agents -q -p no:randomly -p no:cov 2>&1 | tail -4
```

Expected: the last line reports passed tests with no `failed` (skipped tests are fine). Run `doctor` only with a temporary
home, as in mission T2-11.
