# QA program

This folder turns a new tester into Pitwall's QA function, with an AI coding agent as coach. The
agent reads these files and guides the tester through a short bootcamp, then real QA missions:
testing the docs, testing the product by hand, checking pull requests before they merge, writing
automated tests, and, once the maintainer allows it, testing against real cloud providers.

## What this is

- A **coach** for the agent: [START-HERE.md](START-HERE.md) and the [coach rules](coach/rules.md).
- A **bootcamp** of seven lessons, starting at [B0 — Welcome](bootcamp/B0-welcome.md).
- **Missions** in five tiers: [the mission ladder](missions/README.md).
- **Concept cards** that each explain one idea: [concepts](concepts/README.md).
- A **handbook** for how QA works here: [handbook](handbook/README.md).
- **Templates** for progress, bug reports, QA reports, and notes, in `templates/`.

## For the tester: how to start

1. Get set up with the maintainer ([maintainer setup](maintainer-setup.md)).
2. Open a terminal in your Pitwall clone, the folder that contains `qa/`.
3. Start your AI agent (Claude Code, Codex, or opencode).
4. Type: `Read qa/START-HERE.md and follow it.`

Do this at the start of every session. Your progress is saved in `qa/.work/`, which git ignores.

## For the maintainer

- Onboarding a tester: [maintainer setup](maintainer-setup.md).
- Asking for QA on a pull request: fill in its `## QA notes` section and add the `needs-qa` label
  ([details](handbook/acceptance-testing.md#maintainer-side-requesting-qa)).
- Triage and labels: [triage and labels](handbook/triage-and-labels.md).

## How it is organized

```text
qa/
  START-HERE.md        the agent starts here
  README.md            this file
  maintainer-setup.md  onboarding a tester
  coach/               rules and teaching instructions for the agent
  bootcamp/            B0 to B6
  missions/            tiers 1 to 5 (tier 5 is locked)
  concepts/            one idea per card
  handbook/            how QA works on this project
  templates/           fill-in forms
  .work/               the tester's private progress (ignored by git)
```

## Keeping it current

A pull request that changes a command, route, screen, or heading that a lesson uses updates that
lesson in the same pull request. See [maintaining the packet](handbook/maintaining-the-packet.md)
and the [Definition of Done](../CONTRIBUTING.md#definition-of-done).
