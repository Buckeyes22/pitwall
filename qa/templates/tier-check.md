# Tier check

The coach asks three to five questions from the tier being unlocked. The tester passes when every
answer is right after at most one hint each. Record the result and date in the progress file. The
answer key is for the coach.

## Tier 1 (after the bootcamp)

1. What is the difference between training mode and work mode?
   Answer: in training mode the tester types every command that changes something; in work mode
   the coach may run allowed commands after saying what and why.
2. Name three commands on the never-run list and why each is risky.
   Answer: any three from rule R2 with a sensible reason (spends money, changes the machine,
   destroys data, publishes).
3. What goes under "Expected Behavior" in a bug report?
   Answer: what should have happened, ideally quoting the doc that says so.
4. What does a dry run mean in Pitwall?
   Answer: routing and cost are worked out, but nothing is sent to a paid provider
   ([card](../concepts/dry-run-and-hermetic.md)).
5. What is severity 1, and what do you do when you find one?
   Answer: money, secrets, or safety; stop, file it, and message the maintainer directly.

## Tier 2

1. What makes repro steps good?
   Answer: numbered, start from a clean state, exact commands, one action per step
   ([card](../concepts/reproducing-a-bug.md)).
2. Doc bug or product bug: how do you decide, and which label goes with each?
   Answer: if the product does the right thing and the doc is wrong, `documentation`; otherwise
   `bug`.
3. What do 401, 403, 404, and 422 mean?
   Answer: no or bad credential; valid credential without permission; not found; request body
   invalid ([card](../concepts/http-and-status-codes.md)).
4. How do you start and stop the local test stack?
   Answer: `docker compose -f docker-compose.testinfra.yml up -d --wait`; `make down`.
5. Why search for duplicates before filing?
   Answer: so one problem has one issue and the maintainer's time goes to fixing, not sorting.

## Tier 3

1. What does "hermetic" mean, and why must the fast tests be hermetic?
   Answer: no real outside services; so they are safe, free, and repeatable anywhere.
2. What does an exploratory charter contain?
   Answer: an area, the resources or technique, and the kind of problem to look for.
3. Why are the README Quick Start values fake?
   Answer: local testing must never use or spend with real credentials.
4. What is the budget gate, and which status code shows it working?
   Answer: it refuses spend over the budget before any provider call; 402.
5. Why must the journey harness only run against the local test stack?
   Answer: it resets the database it is pointed at.

## Tier 4

1. Walk through an acceptance run for a `needs-qa` pull request.
   Answer: list, view the QA notes, check out, sync, baseline tests, criteria with evidence,
   explore, smoke, draft, approve, post, swap labels, file issues.
2. Which review action and label go with pass, fail, and pass with issues?
   Answer: approve and `qa-passed`; request changes and `qa-failed`; comment and `qa-passed`
   with issues linked.
3. What is a regression test, and how do you prove it catches the bug?
   Answer: a test that fails if the bug comes back; undo the fix locally and watch it fail.
4. What does `git commit -s` add, and why does this project need it?
   Answer: a `Signed-off-by:` line certifying you may contribute the change; CI requires it.
5. How do you read a failing CI job with `gh`?
   Answer: `gh pr checks <N>`, then `gh run view <run-id> --log-failed`.

## Tier 5

1. Where does the live key live, and what must never happen to it?
   Answer: in a `chmod 600` file outside the repository; it is never printed, pasted, or
   committed.
2. What are the three spend ceilings?
   Answer: the dedicated account's prepaid balance, `PITWALL_MONTHLY_BUDGET_USD`, and each
   launch's TTL and maximum price.
3. What do you check before and after every live step?
   Answer: what is already running and the balance before; nothing left running after.
4. What is an orphaned pod, and how do you find one?
   Answer: a pod with no working owner; see the troubleshooting guide's orphaned-pods section.
5. What do you do about a charge you did not expect?
   Answer: stop, treat it as severity 1, and message the maintainer.
