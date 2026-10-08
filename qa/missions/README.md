# Missions

Missions are real QA work. Each one ends with something real: an issue, a QA report on a pull
request, a test pull request, or a recorded result.

## The tiers

| Tier | What you do | Recommended mode |
| --- | --- | --- |
| 0 | [Bootcamp](../bootcamp/B0-welcome.md): foundations | training |
| 1 | Docs QA: follow the docs exactly and report where they are wrong | training |
| 2 | Manual and exploratory testing of the API, CLI, TUI, and MCP | training, then work |
| 3 | Acceptance testing: check pull requests before they merge | work |
| 4 | Automation: write tests and open test pull requests | training for T4-01 to T4-03, then work |
| 5 | Live testing against real providers (locked) | training, always |

## Unlocking a tier

| Tier | Unlocks when |
| --- | --- |
| 1 | B0–B6 are complete |
| 2 | T1-01 and T1-02 are complete; at least three issues filed that pass [the bug-report checklist](../handbook/bug-reports.md#the-bug-report-checklist); the maintainer agrees |
| 3 | T2-01 to T2-07 are complete; at least one exploratory session (T2-12) is done; the maintainer agrees |
| 4 | At least two QA reports are posted (T3-02 or T3-03); at least one fix is verified (T3-04); the maintainer agrees |
| 5 | The maintainer has given the tester a spend-capped key; T5-00 is passed; the maintainer agrees |

Before recording an unlock, run the questions for that tier from
[the tier check](../templates/tier-check.md). The maintainer's agreement comes from the weekly
check-in; ask the tester whether the maintainer agreed. Record it in the progress file as
`Unlocked tiers: ... N (<date>, confirmed by the maintainer)`.

Unlocked tiers stay open, and repeatable missions from lower tiers are always available.

## How the coach picks the next item

1. Severity 1 work (rule R14).
2. Pull requests labeled `needs-qa` (tier 3 and up).
3. Closed `found-by-qa` issues without `qa-verified` (tier 3 and up).
4. The lowest-numbered incomplete mission in the highest unlocked tier.
5. An [exploratory session](tier2-manual/T2-12-exploratory-session.md).

## Mission list

| Id | Mission | Repeatable | Output |
| --- | --- | --- | --- |
| T1-01 | [CONTRIBUTING walkthrough](tier1-docs/T1-01-contributing-walkthrough.md) | no | issues |
| T1-02 | [Install checklist, part 1](tier1-docs/T1-02-install-checklist-part-1.md) | no | issues |
| T1-03 | [Install checklist, part 2](tier1-docs/T1-03-install-checklist-part-2.md) | no | issues |
| T1-04 | [CLI help vs CLI reference](tier1-docs/T1-04-cli-help-vs-reference.md) | no | issues |
| T1-05 | [Journey catalog vs harness](tier1-docs/T1-05-journey-catalog-vs-harness.md) | no | issues |
| T1-06 | [Configuration docs cross-check](tier1-docs/T1-06-configuration-docs.md) | no | issues |
| T1-07 | [Troubleshooting guide messages](tier1-docs/T1-07-troubleshooting-messages.md) | no | issues |
| T2-01 | [The REST API in a browser](tier2-manual/T2-01-api-in-the-browser.md) | no | issues |
| T2-02 | [The REST API with curl](tier2-manual/T2-02-api-with-curl.md) | no | issues |
| T2-03 | [The API's locks](tier2-manual/T2-03-api-locks.md) | no | issues |
| T2-04 | [Money safety](tier2-manual/T2-04-money-safety.md) | no | issues |
| T2-05 | [The CLI off the happy path](tier2-manual/T2-05-cli-off-the-happy-path.md) | no | issues |
| T2-06 | [Database lifecycle guards](tier2-manual/T2-06-database-guards.md) | no | issues |
| T2-07 | [TUI tour and exploratory pass](tier2-manual/T2-07-tui-tour.md) | no | issues |
| T2-08 | [Pitwall as an agent tool (MCP)](tier2-manual/T2-08-mcp-agent-client.md) | no | issues |
| T2-09 | [Background services](tier2-manual/T2-09-background-services.md) | no | issues |
| T2-10 | [Journey harness vs your manual results](tier2-manual/T2-10-journey-harness.md) | yes | issues |
| T2-11 | [Agent Routing component checks](tier2-manual/T2-11-agent-routing-checks.md) | no | issues |
| T2-12 | [Exploratory session](tier2-manual/T2-12-exploratory-session.md) | yes | notes, issues |
| T3-01 | [Practice acceptance on a merged pull request](tier3-acceptance/T3-01-practice-acceptance.md) | no | local QA report |
| T3-02 | [QA a dependency update pull request](tier3-acceptance/T3-02-dependency-update-pr.md) | yes | QA review |
| T3-03 | [Acceptance-test a needs-qa pull request](tier3-acceptance/T3-03-needs-qa-pr.md) | yes | QA review, issues |
| T3-04 | [Verify fixes for issues you filed](tier3-acceptance/T3-04-verify-fixes.md) | yes | issue comments |
| T3-05 | [Release candidate regression pass](tier3-acceptance/T3-05-release-candidate-pass.md) | yes | sign-off report |
| T4-01 | [Reading and running the test suite](tier4-automation/T4-01-reading-the-test-suite.md) | no | progress entry |
| T4-02 | [The pre-PR routine and reading CI](tier4-automation/T4-02-pre-pr-routine.md) | no | progress entry |
| T4-03 | [Your first regression test](tier4-automation/T4-03-first-regression-test.md) | no | pull request |
| T4-04 | [CLI contract tests](tier4-automation/T4-04-cli-contract-tests.md) | yes | pull request |
| T4-05 | [API contract tests](tier4-automation/T4-05-api-contract-tests.md) | yes | pull request |
| T4-06 | [Journey harness assertions](tier4-automation/T4-06-journey-harness-assertions.md) | yes | pull request |
| T4-07 | [Property tests with Hypothesis](tier4-automation/T4-07-property-tests.md) | yes | pull request |
| T5-00 | [Live safety briefing and key handling](tier5-live/T5-00-live-safety-briefing.md) | no | tier check |
| T5-01 | [Read-only live checks](tier5-live/T5-01-read-only-live-checks.md) | yes | issues |
| T5-02 | [Personal serving smoke](tier5-live/T5-02-personal-serving-smoke.md) | yes | issues, cleanup evidence |
| T5-03 | [Pod lease lifecycle (L3)](tier5-live/T5-03-pod-lease-lifecycle.md) | yes | issues, cleanup evidence |
| T5-04 | [Live audit (L4)](tier5-live/T5-04-live-audit.md) | yes | audit report |
| T5-05 | [Serverless endpoints (L1, L2)](tier5-live/T5-05-serverless-endpoints.md) | yes | issues, cleanup evidence |
