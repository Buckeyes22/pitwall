# Session routine

## Starting a session

1. Read the files listed in [Start here](../START-HERE.md#1-read-these-files-first-in-order).
2. Greet the tester by name from the progress file. Recap the last "Session log" entry in two
   sentences.
3. If tier 3 or higher is unlocked, check [the work queue](#the-work-queue) first.
4. Propose one item for today: queue work first, otherwise `Current item`, otherwise the next item
   from [the mission ladder](../missions/README.md#how-the-coach-picks-the-next-item). Say which
   mode you will use.
5. Check only the environment this item needs. Usually that is `git status`,
   `git branch --show-current`, and `docker ps`.
6. Start a new "Session log" entry with today's date and your agent name.

## The work queue

From tier 3 on, run these read-only checks in order:

1. Severity 1 issues: `gh issue list --state open --label severity:1-critical`
2. Pull requests waiting for QA: `gh pr list --label needs-qa`
3. Fixed issues waiting for verification:
   `gh issue list --state closed --search "label:found-by-qa -label:qa-verified"`

If any list has items, suggest that work before curriculum work. The tester decides.

## During a session

- Update `Stopped at` in the progress file at each checkpoint (R12).
- Save important command output to `qa/.work/evidence/` with `tee`.
- Add raw findings to "Findings not yet filed" as soon as you see them.

## Ending a session

1. Stop what you started: the API (Ctrl-C in its terminal), and `make down` if the test stack is
   no longer needed.
2. Update the progress file: `Completed` rows, `Current item`, `Stopped at` (the exact step),
   "Findings not yet filed", "Questions for the maintainer", the session log entry, and "Coach
   notes".
3. Tell the tester in plain words what we did, what they learned, and what comes next.
4. If "Questions for the maintainer" has open items, remind the tester to bring them to the weekly
   check-in.
