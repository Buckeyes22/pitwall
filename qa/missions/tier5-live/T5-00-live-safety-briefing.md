# T5-00 — Live safety briefing and key handling

**Tier:** 5 | **Mode:** training | **Repeatable:** no | **Needs:** maintainer's spend-capped key | **Output:** tier check

## Why this matters

Nothing live happens until the tester can explain every safety layer in their own words. Tier 5
spends real money on real provider capacity. The four layers are: where the key lives on disk,
how to load it into one terminal, the three spend ceilings, and the cleanup check. A tester who
skips the briefing will type a command they cannot afford to type wrong.

## Sources

- [Live testing safety](../../handbook/live-testing-safety.md) — the four layers above
- [Tier check](../../templates/tier-check.md) — the five questions, with answers, the coach asks
- [Coach rules](../../coach/rules.md) — rules R2 (never-run list), R3 (when unsure, stop), R14
  (severity 1)
- [Unlocking tier 5](../../maintainer-setup.md#7-unlocking-tier-5) — what the maintainer did to
  hand you the key

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Tier 5 is
always training mode (rule R5): the tester types every command. The key is never printed, pasted
into chat, pasted into an issue, pasted into a pull request, or committed. The key file is read
only by `ls -l`; its contents stay on disk. Step 3's three ceilings are agreed before any live
step; do not pick numbers alone.

## Setup

The maintainer has sent the key privately (per
[unlocking tier 5](../../maintainer-setup.md#7-unlocking-tier-5)). The tester is at the
repository root with `qa/.work/progress.md` up to date. The local test stack does not need to be
running; this mission does not call the API.

## Steps

### Step 1 — Read live-testing-safety together, one section per message

Coach: walk the handbook with the tester, one heading per message. Pause after each so the
tester can repeat it back in plain words. The five sections are
[key handling](../../handbook/live-testing-safety.md#key-handling),
[spend ceilings](../../handbook/live-testing-safety.md#spend-ceilings),
[before every live step](../../handbook/live-testing-safety.md#before-every-live-step),
[cleanup check](../../handbook/live-testing-safety.md#cleanup-check), and
[surprise charges](../../handbook/live-testing-safety.md#surprise-charges).
Tester does: reads, paraphrases each section, and writes a one-line summary of each into the
progress file's `Coach notes` block under `Terms taught`.
Expected: five summaries in the progress file, each ending in the tester's own words, not copied
from the doc.

### Step 2 — Store the key in a `chmod 600` file outside the repository

Coach: walk the tester through
[key handling](../../handbook/live-testing-safety.md#key-handling). The maintainer's key is pasted
into a text editor; it never goes through a shell pipeline. The directory is `chmod 700`, the file
is `chmod 600`. The file content is never shown.

Tester does:

```bash
mkdir -p ~/.config/pitwall-qa && chmod 700 ~/.config/pitwall-qa
$EDITOR ~/.config/pitwall-qa/runpod.env
chmod 600 ~/.config/pitwall-qa/runpod.env
ls -l ~/.config/pitwall-qa/runpod.env
```

Expected: the final `ls -l` line shows `-rw-------` (mode `0600`) and the owner's name. No
output from `$EDITOR` is captured. The file content is never echoed or printed.
If different: a mode other than `-rw-------` means the file is readable by other users on the
machine; fix the permissions before continuing. If the file does not exist, the `$EDITOR` step
did not save; redo it.

### Step 3 — Agree the three spend ceilings with the maintainer

Coach: read [spend ceilings](../../handbook/live-testing-safety.md#spend-ceilings) again. The
three layers are: the dedicated account's prepaid balance (the hard ceiling),
`PITWALL_MONTHLY_BUDGET_USD` (Pitwall's own budget gate), and each launch's `--ttl-minutes` and
`--max-usd-per-hour` (the smallest values that work). The tester confirms the maintainer set all
three and writes them into the progress file under a new `Tier 5 ceilings` block with one line
per layer.

Tester does:

```bash
mkdir -p qa/.work/notes
$EDITOR qa/.work/notes/tier5-ceilings.md
```

Expected: a short file with three labeled lines: the prepaid balance in USD,
`PITWALL_MONTHLY_BUDGET_USD=<number>`, and the agreed TTL and per-hour cap. The tester can show
this file at the weekly check-in.
If different: any layer missing or set to "ask later" means tier 5 is not yet ready to open.

### Step 4 — Pass the Tier 5 tier check

Coach: ask the five [tier 5 questions](../../templates/tier-check.md#tier-5) from the tier
check, one at a time. The tester may take one hint per question. Record the result and date in
the progress file's `Completed` row for T5-00, and the maintainer agreement in
`Unlocked tiers: ... 5 (<date>, confirmed by the maintainer)` per the
[unlocking a tier](../../missions/README.md#unlocking-a-tier) section.
Expected: all five questions answered correctly. A wrong answer on any one means the coach
reviews that section of the handbook again and re-asks.
If different: a missed answer blocks tier 5; redo the missed section and re-ask the question
before opening any live mission.

## What counts as a finding

- A mode on `~/.config/pitwall-qa/runpod.env` that is not `0600`.
- A key in the chat, an issue, a pull request, a commit, or any log file.
- A spend ceiling picked without the maintainer (no `Confirmed by the maintainer` line in the
  progress file).
- A wrong tier-check answer after more than one hint.

## Done when

- The five sections of the handbook are paraphrased in the progress file's `Coach notes`.
- The key file exists with mode `0600`; its contents were never shown.
- The three spend ceilings are recorded in `qa/.work/notes/tier5-ceilings.md`.
- All five tier-5 questions were answered correctly.
- The progress file records `Unlocked tiers: ... 5 (<date>, confirmed by the maintainer)`.

## Record in progress

Add a `Completed` row for T5-00 with the date, a link to `qa/.work/notes/tier5-ceilings.md`, and
the tier-check pass. Set `Current item` to
[T5-01 — Read-only live checks](T5-01-read-only-live-checks.md). End the session log with which
section the tester found hardest to paraphrase and any open questions for the maintainer.
