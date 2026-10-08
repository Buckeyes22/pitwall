# T3-02 — QA a dependency update pull request

**Tier:** 3 | **Mode:** work | **Repeatable:** yes | **Needs:** tier 3 unlocked | **Output:** posted QA review

## Why this matters

Dependency updates break things quietly. The maintainer opens them as `chore(deps)` pull
requests (there is no Dependabot on this repository). A bump in the TUI
library or the API framework can pass unit tests and still break a real screen or a real route.
This mission is the tester's routine check on that risk class.

## Sources

- The dependency pull request list: `gh pr list --search "chore(deps) in:title"`.
- [Outcomes and labels](../../handbook/acceptance-testing.md#outcomes-and-labels).
- [The QA smoke set](../../handbook/regression-and-release.md#qa-smoke-set).
- [Evidence standard](../../handbook/evidence-standard.md).
- The relevant manual mission for the surface: [T2-02 — The REST API with
  curl](../tier2-manual/T2-02-api-with-curl.md#steps) for `fastapi`,
  [T2-07 — TUI tour and exploratory pass](../tier2-manual/T2-07-tui-tour.md#steps) for `textual`.

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Run the targeted
smoke test in work mode. The tester approves the review before it is posted (rule R9). Do not
approve or merge the pull request itself; QA only reports.

## Setup

- The tester is on `main` with `qa/.work/progress.md` up to date.
- The five `export` lines from the README Quick Start are set in the terminal.
- `gh` is authenticated to the project.

## Steps

### Step 1 — Pick a dependency-update pull request

Coach: ask the tester which surface they know best (TUI or API), then list the open
dependency-update pull requests.
Tester does:

```bash
gh pr list --search "chore(deps) in:title"
```

Expected: at least one open pull request, and at least one of them changes `textual` or `fastapi`.
If different: no dependency-update pull requests are open. Record "none open" in the progress file,
stop, and check again next session. If one is open but none touch a known surface, pick it anyway
and run the closest manual mission's steps.

### Step 2 — Check out and run the hermetic suite

Coach: remind the tester that the suite is the floor. A passing suite does not prove the surface
works; a failing suite blocks everything else.
Tester does:

```bash
gh pr checkout <N>
uv sync --frozen --extra dev
make test 2>&1 | tee qa/.work/evidence/T3-02-<N>-suite.txt | tail -15
```

Expected: a summary with `passed` and no `failed`.
If different: the suite fails on the dependency-update commit. That is a finding. File it and decide
with the tester whether to keep going. The end label should be `qa-failed`, not `qa-passed`.

### Step 3 — Targeted smoke for the changed surface

Coach: pick the smoke set that matches the surface. For `textual`, run T2-07 steps 1–3. For
`fastapi`, run T2-02 steps 1–5 plus the OpenAPI check.
Tester does for `textual`:

```bash
# T2-07 steps 1–3 in a terminal with the five export lines set
uv run pitwall dashboard
```

Expected: the dashboard starts, the footer lists ten views, and the first three view keys
navigate without a traceback.
If different: a view crashes or the footer disagrees with the README. That is a finding.
Then for `fastapi`:

```bash
make openapi-check 2>&1 | tee qa/.work/evidence/T3-02-<N>-openapi.txt | tail -10
```

Expected: the diff against the checked-in OpenAPI document is empty, or only the additive
changes listed in the pull request appear.
If different: a route changed shape. That is a finding.
Then rerun T2-02 steps 1–5 against the API, with the API started the way T2-02's setup says.
Save evidence to `qa/.work/evidence/T3-02-<N>-api.txt`.
Expected: the same results as before the bump. Any new failure is a finding.

### Step 4 — Draft, approve, and post

Coach: ask the tester for a verdict (pass, pass with issues, or fail). Build the draft from the
QA report template, with one row per smoke check and one row per finding.
Tester does:

```bash
cp qa/templates/qa-report.md qa/.work/notes/pr-<N>-qa.md
$EDITOR qa/.work/notes/pr-<N>-qa.md
```

Expected: the draft has the verdict, the commit tested, the environment, the suite summary, the
smoke results, and any issues filed.
If different: a row or a piece of evidence is missing. Fix it before posting.
After the tester approves the draft, post and label:

```bash
gh pr review <N> --approve --body-file qa/.work/notes/pr-<N>-qa.md           # pass
gh pr review <N> --comment --body-file qa/.work/notes/pr-<N>-qa.md           # pass with issues
gh pr review <N> --request-changes --body-file qa/.work/notes/pr-<N>-qa.md   # fail
gh pr edit <N> --add-label qa-passed    # or qa-failed
```

Expected: the review is posted and the outcome label is set. A dependency-update pull request does not
carry `needs-qa`, so only the outcome label changes
([outcomes and labels](../../handbook/acceptance-testing.md#outcomes-and-labels)).
If different: the review command and the label disagree, or the body was edited after approval.
Stop, repost with the correct command, and confirm the label.

## What counts as a finding

- A failed acceptance criterion in the pull request's own description.
- A regression in the smoke set compared to the previous run on `main`.
- Behavior that contradicts the pull request's own description.
- A route shape change in `openapi.json` for `fastapi` that the pull request does not mention.

## Done when

- `make test` was run on the dependency-update commit and the output is saved.
- The targeted smoke test was run and the output is saved.
- The QA report is posted as a pull request review.
- The outcome label is set (`qa-passed` or `qa-failed`), matching the verdict.

## Record in progress

Add a `Completed` row for T3-02 with the pull request number and the label set. Link the report
path. Set `Current item` to the next item the
[mission list](../../missions/README.md#how-the-coach-picks-the-next-item) picks. End the session
log with what changed in this dependency and any new questions for the maintainer. Then:

```bash
git switch main
uv sync --frozen --extra dev
```
