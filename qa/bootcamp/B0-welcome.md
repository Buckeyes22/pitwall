# B0 — Welcome

**Mode:** training | **Needs:** none | **Output:** `qa/.work/progress.md`

## Goal

Get to know the tester, explain how this program works, and fill in the progress file.

## You will learn

- What QA means on this project.
- How the program is organized: a bootcamp, then five tiers of missions.
- Training mode and work mode.
- The rules that matter most.
- How a session starts and ends.

## Before you start

The coach has just created `qa/.work/progress.md` from the template (Start here, section 2). The
tester is at the repository root.

## Steps

### Step 1 — Introductions

Coach: introduce yourself as the tester's QA coach. Say in two sentences what QA means here:
finding problems before users do, and proving that things work, with evidence. Then ask, one at a
time: the tester's name; what they do at work; which AI agents they use; how they like to learn
(see it first, try it first, or have it explained first).
Tester does: answers.
Expected: the coach fills in "About me" in the progress file.

### Step 2 — The maintainer

Coach: ask who the tester reports to and how they reach that person.
Expected: the coach fills in the "Maintainer" line.

### Step 3 — How the program works

Coach: explain [the tiers](../missions/README.md#the-tiers) in plain words: the bootcamp first,
then docs testing, hands-on testing, checking pull requests, writing tests, and a locked live tier
that opens only when the maintainer gives the tester a spend-capped key.
Tester does: answers "Which tier can spend real money, and what unlocks it?"
Expected: tier 5; the maintainer's key.
If different: explain again with the table in `missions/README.md`.

### Step 4 — Modes

Coach: explain [training and work mode](../coach/rules.md#modes). In training mode the tester
types every command that changes something. In work mode the coach may run allowed commands after
saying what and why. The bootcamp is always training mode.
Expected: the progress file says `Mode default: training`.

### Step 5 — The rules that matter most

Coach: walk through five of [the coach rules](../coach/rules.md):
- R1: no real credentials
- R2: the never-run list, with three examples: `pitwall setup`, `pitwall serve` without
  `--dry-run`, and `git push` to `main`
- R6: evidence
- R9: the tester approves everything posted
- R14: severity 1 is urgent

Tester does: says one rule back in their own words.
Expected: a correct paraphrase.

### Step 6 — How sessions work

Coach: every session starts with the kickoff sentence, progress lives in `qa/.work/`, and every
session ends with an update and a summary.
Tester does:

```bash
ls qa/.work
```

Expected: `evidence  notes  progress.md`.

```bash
head -3 qa/.work/progress.md
```

Expected: the first line starts with `# QA progress —`.
If different: the bootstrap in Start here, section 2, did not run. Run it now.

### Step 7 — Asking for help

Coach: tell the tester they can say "I'm stuck", "explain that", "slow down", or "why?" at any
time.
Expected: the tester knows how to ask.

## Checkpoint

Ask the tester to explain training mode and work mode in their own words, and to name three rules.

## Done when

- "About me" and "Maintainer" are filled in.
- The tester answered the checkpoint correctly.

## Record in progress

Add a `Completed` row for B0, set `Current item: B1`, and write the session log entry.

## Next

[B1 — Terminal basics](B1-terminal-basics.md)
