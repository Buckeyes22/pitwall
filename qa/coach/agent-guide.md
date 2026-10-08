# Agent guide

The tester uses three AI agents: Claude Code, Codex, and opencode with a self-hosted Qwen model.
This packet works with all three.

## Which agent for which job

| Job | Best agent |
| --- | --- |
| Bootcamp lessons, concept cards, drills | Any. Qwen through opencode is fine |
| Running checklists and journeys (tiers 1 and 2) | Any |
| Reading large pull-request diffs (tier 3) | Claude Code or Codex |
| Writing tests (tier 4) | Claude Code or Codex |
| A step where answers keep changing or look wrong | Switch to Claude Code or Codex |

If you are the Qwen model and a step asks you to write or change Python code, tell the tester this
job is better done in Claude Code or Codex.

## Switching agents

Everything important is in `qa/.work/progress.md`, so any agent can pick up where another stopped.
To switch, the tester starts the other agent at the repository root and types the kickoff
sentence again: `Read qa/START-HERE.md and follow it.`

## Checking what an agent tells you

Agents can be confidently wrong, including you. Teach the tester to check:

- Re-run the command and read the output.
- Run `git status` and `git diff` to see what really changed.
- Be suspicious of a command that is not in a lesson, a linked doc, or `--help` output.
- A claim without output is not evidence (R6).

## Approval settings

Keep command approval prompts on in every agent. Never use modes that skip approvals, such as
Claude Code's bypass-permissions mode or Codex's full-auto and dangerous-bypass options.

## Harness tips

- **Claude Code:** the tester can type `!` followed by a command to run it inside the session, so
  you see the output directly.
- **Every agent:** in training mode the tester can run commands in a second terminal window and
  paste the output into the chat.
- **Start every session at the repository root,** where the `qa/` folder is.
