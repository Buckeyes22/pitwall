# Triage and labels

## Severity scale

| Severity | Meaning | Pitwall examples |
| --- | --- | --- |
| 1 — critical | Money, secrets, or safety | Spend without passing the budget gate; a key or token in output, logs, or an error; a dry run that calls a real provider; the kill switch fails; an auth bypass |
| 2 — high | A documented core journey fails with no workaround | README Quick Start breaks; the API returns 500; the TUI crashes on start; a migration fails on a clean database |
| 3 — medium | Wrong behavior or wrong docs, with a workaround | A documented command fails but a nearby variant works; a wrong exit code; a misleading error message; a doc that disagrees with the code |
| 4 — low | Cosmetic | Typos, formatting, confusing wording, TUI alignment |

Severity is the tester's call, made with these definitions. Priority is the maintainer's call
during triage.

## Labels

| Label | Use |
| --- | --- |
| `bug` | The product does the wrong thing |
| `documentation` | A doc is wrong or unclear |
| `question` | Not sure whether it is a defect; asking the maintainer |
| `found-by-qa` | Every issue the tester files |
| `severity:1-critical`, `severity:2-high`, `severity:3-medium`, `severity:4-low` | Exactly one on every `found-by-qa` bug or documentation issue |
| `needs-qa` | A pull request is ready for acceptance testing |
| `qa-passed`, `qa-failed` | The acceptance outcome; replaces `needs-qa` |
| `qa-verified` | A closed `found-by-qa` issue whose fix the tester confirmed on `main` |
| `qa-packet` | A defect in this `qa/` folder |
| `duplicate`, `invalid`, `wontfix` | The maintainer closes an issue with one of these and a reason |

## Issue lifecycle

1. The tester files the issue with `bug` or `documentation`, plus `found-by-qa` and one severity.
2. The maintainer triages it:
   - keeps it, or closes it as `duplicate`, `invalid`, or `wontfix` with a reason
   - answers `question` issues
3. A fix pull request closes it with `Closes #N`.
4. The tester verifies the fix on `main`
   (mission T3-04, `qa/missions/tier3-acceptance/T3-04-verify-fixes.md`):
   - fixed: add `qa-verified`
   - not fixed: reopen with evidence

## Queries the coach uses

```bash
gh issue list --state open --label severity:1-critical
gh pr list --label needs-qa
gh issue list --state closed --search "label:found-by-qa -label:qa-verified"
```
