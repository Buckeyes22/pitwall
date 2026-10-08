# Maintainer setup

What the maintainer does once to onboard a tester, and the parts that repeat.

## 1. GitHub access

1. Ask the tester for their GitHub username.
2. Invite them with the Write role:
   `gh api -X PUT repos/Buckeyes22/pitwall/collaborators/<username> -f permission=push`
3. The tester accepts the invitation from their email or GitHub notifications.
4. Confirm: `gh api repos/Buckeyes22/pitwall/collaborators --jq '.[].login'` lists them.

Write access lets the tester push branches, apply labels, and review pull requests. `main`
requires a pull request and the `CI` check, with zero required approvals. So the tester cannot
push to `main`, and their reviews inform you but never gate a merge.

## 2. Labels

The QA labels are created once per repository. Check them:

```bash
gh label list --search qa
gh label list --search severity
```

Expected: `found-by-qa`, `needs-qa`, `qa-passed`, `qa-failed`, `qa-verified`, `qa-packet`, and the
four `severity:` labels. Create any that are missing:

```bash
gh label create found-by-qa --color 5319e7 --description "Filed by the QA program" --force
gh label create severity:1-critical --color b60205 --description "Money, secrets, or safety" --force
gh label create severity:2-high --color d93f0b --description "Core journey broken, no workaround" --force
gh label create severity:3-medium --color fbca04 --description "Wrong behavior or docs, with a workaround" --force
gh label create severity:4-low --color c5def5 --description "Cosmetic" --force
gh label create needs-qa --color 1d76db --description "Ready for acceptance testing" --force
gh label create qa-passed --color 0e8a16 --description "Acceptance testing passed" --force
gh label create qa-failed --color e11d21 --description "Acceptance testing failed" --force
gh label create qa-verified --color 006b75 --description "Fix confirmed by QA on main" --force
gh label create qa-packet --color bfd4f2 --description "Defect in the qa/ training packet" --force
```

## 3. Agent access

- Claude Code and Codex: the tester needs their own sign-in for each. Arrange a subscription or
  seat with them.
- opencode with your self-hosted model: set up the tester's opencode with an OpenAI-compatible
  provider that points at your model server. Send the address and key privately. They belong only
  in the tester's local opencode configuration, never in this repository, an issue, or a pull
  request.
- Ask the tester to keep approval prompts on in every agent.

## 4. The kickoff message

Send this once sections 1–3 are done:

```text
Welcome aboard as Pitwall's tester! Here's how to start:

1. Accept the GitHub invitation for Buckeyes22/pitwall (check your email).
2. Install your AI agents: Claude Code, Codex, and opencode. We'll connect opencode to my model
   server together.
3. Install git and the GitHub CLI (gh) with your Linux package manager, then sign in:
     gh auth login
   Choose GitHub.com, HTTPS, and "Login with a web browser", and say yes to authenticating Git.
   If installing gh is confusing, start your agent in your home folder and ask it to help you
   install the GitHub CLI for your Linux distribution.
4. Get the code:
     gh repo clone Buckeyes22/pitwall ~/pitwall
     cd ~/pitwall
5. Start your agent in that folder and type exactly:
     Read qa/START-HERE.md and follow it.
6. Keep your agent's approval prompts on. When it asks to run something, read it first.

Do step 5 at the start of every session. Bring your questions to our weekly check-in.
```

## 5. Weekly check-in

- The tester brings their check-in notes (from `qa/templates/weekly-checkin.md`).
- Triage new `found-by-qa` issues: keep each one, or close it as `duplicate`, `invalid`, or
  `wontfix` with a reason ([issue lifecycle](handbook/triage-and-labels.md#issue-lifecycle)).
- Answer the tester's "Questions for the maintainer".
- Decide tier unlocks ([unlocking a tier](missions/README.md#unlocking-a-tier)), and tell the tester
  plainly. The coach records the unlock.

## 6. Requesting QA on a pull request

Fill in the pull request's `## QA notes` section, then add the label with
`gh pr edit <N> --add-label needs-qa`. Details are in
[the maintainer side of acceptance testing](handbook/acceptance-testing.md#maintainer-side-requesting-qa).

## 7. Unlocking tier 5

1. Create a dedicated provider account with a small prepaid balance. The balance is the hard
   spend ceiling.
2. Create an API key for that account only, and send it to the tester privately.
3. Agree on `PITWALL_MONTHLY_BUDGET_USD`, the TTL, and the maximum hourly price.
4. Confirm the tester passed
   [T5-00](missions/tier5-live/T5-00-live-safety-briefing.md).
5. Revoke the key whenever live testing pauses.
