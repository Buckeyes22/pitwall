<!--
QA report for a pull request. Copy to qa/.work/notes/pr-<N>-qa.md and fill it in.
After the tester approves, delete this comment and post with one of:

  gh pr review <N> --approve --body-file qa/.work/notes/pr-<N>-qa.md          (pass)
  gh pr review <N> --request-changes --body-file qa/.work/notes/pr-<N>-qa.md  (fail)
  gh pr review <N> --comment --body-file qa/.work/notes/pr-<N>-qa.md          (pass with issues)

Then swap the label:
  gh pr edit <N> --remove-label needs-qa --add-label qa-passed   (or qa-failed)
-->

## QA report — pull request #<N>

**Verdict:** pass | fail | pass with issues
**Commit tested:** (from `git rev-parse --short HEAD`)
**Environment:** (OS), Python (version), pitwall (from `uv run pitwall --version`)

### Acceptance criteria

| # | Criterion | Command | Result | Evidence |
| --- | --- | --- | --- | --- |
| 1 | | | | |

### Exploratory notes

### Regression smoke

| Check | Result |
| --- | --- |
| `make test` | |
| Integration lane (`make test-int` with the test stack up) | |
| README Quick Start dry-run inference | |
| `/docs` loads | |
| TUI opens and every view is reachable | |
| MCP stdio lists tools | |

### Issues filed

### Questions for the maintainer

### Skipped, and why
