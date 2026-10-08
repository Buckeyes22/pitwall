# The QA role

The tester is Pitwall's QA function. QA finds problems before users do and proves that things
work, with evidence.

## What QA owns

- Finding defects and reporting them with evidence ([bug reports](bug-reports.md)).
- Choosing each finding's severity ([severity scale](triage-and-labels.md#severity-scale)).
- Acceptance verdicts on pull requests labeled `needs-qa` ([acceptance testing](acceptance-testing.md)).
- Verifying fixes for issues QA filed, then adding `qa-verified` or reopening.
- Regression passes and release-candidate passes ([regression and release](regression-and-release.md)).
- Test contributions in tier 4 ([test automation](test-automation.md)).
- Reporting defects in this packet with the `qa-packet` label.

## What the maintainer owns

- Priority: which issues get fixed first.
- Fixes, merges, and releases.
- Answers to `question` issues and to "Questions for the maintainer".
- Tier unlocks, and the live key for tier 5.

## Weekly check-in

The tester fills in the weekly check-in template (`qa/templates/weekly-checkin.md`) and brings it.
The agenda:

1. New `found-by-qa` issues: the maintainer triages each one (keep, or close with a reason).
2. QA reports since the last check-in.
3. Questions for the maintainer.
4. Tier progress: does the tester meet the next unlock ([unlocking a tier](../missions/README.md#unlocking-a-tier))?
5. Blockers.
