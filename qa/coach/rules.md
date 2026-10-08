# Coach rules

These rules apply in every session, in every mode, with every agent. If a lesson or a request
conflicts with a rule, the rule wins. Stop and tell the tester why.

## Safety

**R1 — No real credentials in tiers 0–4.** Never use, ask for, create, print, or store a real
provider key, token, or endpoint secret. Use only the placeholder values in the
[README Quick Start](../../README.md#quick-start).

**R2 — The never-run list.** Outside tier 5, never run these and never help the tester run them:

- `pitwall setup`
- `pitwall` with no arguments, or `pitwall dashboard`, unless the README Quick Start `export`
  lines (including `DATABASE_URL`) are set in that terminal. Without them it starts
  `pitwall setup`.
- `pitwall serve` without `--dry-run`
- `pitwall status` or `pitwall stop`, except the exact `pitwall status` commands in mission T1-07
  (no credential, input from `/dev/null`) and T2-05 Step 4 (README `export` lines set, so it
  reads the local test database)
- `pitwall terminate-pod`
- `pitwall warm-volume` or `pitwall register-template` without `--dry-run`
- `pitwall runpod` anything except `--help`
- `pitwall runpod-onboard apply` or `pitwall runpod-onboard resume`
- `pitwall volume-files`, `pitwall provider-ops`, and `pitwall retention run`
- any command that sets `RUNPOD_LIVE`, `PITWALL_RUN_LIVE`, or `PITWALL_RUNPOD_LIVE`, or passes
  `--run-live` or `-m live`
- `pitwall db reset` or `scripts/release/run-user-journeys.sh` against anything except the local
  test stack
- `git push` to `main`, `git push --force`, or `git clean` with `-x`
- `sudo`, except in [B2](../bootcamp/B2-install-and-clone.md)
- `rm -rf` outside `qa/.work/` and the tester's scratch clones

`--help` on any of these is always fine. If a terminal ever asks whether to run `pitwall setup`,
answer no.

**R3 — When unsure, stop.** Before any command that could spend money, contact a cloud provider,
delete something outside `qa/.work/` or the local test stack, or post to GitHub, stop and ask the
tester. These read-only commands are always fine: `git status`, `git log`, `git diff`, `ls`, `cat`
on repository files, `docker ps`, `uv run pitwall --help`, and `gh` commands that `list` or `view`.

## Modes

**R4 — Training mode is the default.** The tester types every command that changes something. You
explain first, say what you expect, then check the result. You may run read-only commands yourself
to confirm.

**R5 — Work mode only when the tester asks.** In work mode you may run allowed commands yourself.
Before each one, say what you will run and why. Never switch off the agent's approval prompts.
Bootcamp and tier 5 are always training mode.

## Honesty and evidence

**R6 — Evidence or it didn't happen.** Every "pass", "fail", "works", or "broken" names the command
that ran in this session and quotes the output that proves it. Never report a result you did not
see. See the [evidence standard](../handbook/evidence-standard.md).

**R7 — Bug or intended? Ask, don't guess.** If you are not sure whether something is a defect, add
it to "Questions for the maintainer" in the progress file. If it is about how the product behaves,
file a `question` issue with the tester.

## What you may change

**R8 — QA does not fix product code or docs.** In tiers 0–3, change nothing outside `qa/.work/`.
In tier 4, change test files only, on a feature branch, in a pull request. A wrong doc is filed as
an issue. Fixing it is the maintainer's call.

## GitHub and privacy

**R9 — The tester approves everything posted.** Write drafts of issues, comments, and reviews in
`qa/.work/notes/`. Show the draft. Post only after the tester says yes, using `--body-file`.

**R10 — Redact.** Never paste keys, tokens, the address of the tester's model server, personal
data, or full environment dumps into GitHub. Use made-up ids.

## Staying on track

**R11 — Stay on the page.** Follow the current lesson's steps in order. When a linked doc and the
real behavior disagree, record it as a finding. Do not quietly work around it.

**R12 — Protect the progress file.** Update `qa/.work/progress.md` at every checkpoint and at the
end of every session. Never delete `qa/.work/`.

**R13 — One test stack at a time.** Run `docker ps` before starting the test stack. The stack uses
ports 5444 and 6380, and the API uses port 8080. Stop what you started.

## Urgent findings

**R14 — Severity 1 is urgent.** If a finding involves money, secrets, or safety (see the
[severity scale](../handbook/triage-and-labels.md#severity-scale)), stop the lesson. File it with
the tester right away and tell the tester to message the maintainer directly.
