# T2-12 — Exploratory session

**Tier:** 2 | **Mode:** work | **Repeatable:** yes | **Needs:** T2-01 | **Output:** notes, issues

## Why this matters

Scripted checks cover known paths, and exploration finds what nobody wrote down. A charter keeps
the session focused without scripting it; the notes file is the only artifact the tester needs to
prove the work happened.

## Sources

- [Exploratory testing](../../handbook/exploratory-testing.md)
- [Pitwall charter list](../../handbook/exploratory-testing.md#pitwall-charter-list)
- [Heuristics](../../handbook/exploratory-testing.md#heuristics)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Exploring never
means running anything on the never-run list. Use only the README Quick Start placeholder values
when a charter touches the API. Set the five `export` lines from the README Quick Start in every
terminal the charter needs.

## Setup

Whatever the charter needs, drawn from the tier 2 standard setup. If the charter touches the
API, start it in a second terminal with `uv run pitwall-api`. If it touches the database or the
webhook receiver, run `docker compose -f docker-compose.testinfra.yml up -d --wait` and the
migrations first.

## Steps

### Step 1 — Pick a charter

Coach: a charter is one sentence — Explore (area) with (resources or technique) to discover
(kind of problem). Pick from the [Pitwall charter list](../../handbook/exploratory-testing.md#pitwall-charter-list)
or write your own. Keep it focused so the session fits in one sitting.
Tester does: write the charter as a single sentence at the top of `qa/.work/notes/explore-<date>-<topic>.md`.
Expected: one sentence with an area, a technique, and a kind of problem.

### Step 2 — Copy the template

Coach: the template at `qa/templates/exploratory-session.md` has the sections the notes need.
Tester does:

```bash
cp qa/templates/exploratory-session.md qa/.work/notes/explore-$(date +%F)-<topic>.md
```

Expected: a new file appears in `qa/.work/notes/`. Replace `<topic>` with a short kebab-case label
that names the charter.

### Step 3 — Record commit and setup

Coach: the notes file must say what was tested. Run `git rev-parse --short HEAD` and put the
short hash in the `Commit tested` line. Fill in `Test stack running` and `Starting state`.
Tester does:

```bash
git rev-parse --short HEAD
```

Expected: a short commit id (seven or more hex characters). Write it into the notes.

### Step 4 — Explore and log as you go

Coach: the [heuristics](../../handbook/exploratory-testing.md#heuristics) give you ideas when you
run out. Write every notable thing in the notes log as you go, including what worked. A finding
worth filing shows up in the `Worth a finding?` column the moment you see it; capture the
command and the output in the row so the bug report has evidence.
Tester does: runs the charter, adds rows to the notes log, and pauses to write a finding row when
something looks off.
Expected: the notes log grows throughout the session. No row is left blank.

### Step 5 — Stop at the end of the session

Coach: a session ends when time runs out, not when the charter is done. Stop and write the
`Areas not reached` section with the parts of the charter you did not get to and the new charter
ideas the session surfaced.
Tester does: fills in `Areas not reached` and `New charter ideas` before closing the file.
Expected: both sections are filled in, even if only with one bullet.

### Step 6 — File findings and record questions

Coach: every row in the notes log marked `Worth a finding?` becomes an issue using
[bug reports](../../handbook/bug-reports.md), or a question in the progress file's `Questions for
the maintainer` section. The notes file is the source of evidence, not the issue body.
Expected: every finding row has an issue number in the progress file's `Completed` row, or a
question in the progress file's `Questions for the maintainer` block.

## What counts as a finding

- A bug or doc drift uncovered by a charter (file a `bug` or `documentation` issue).
- A question about behavior that you cannot answer from the docs (record in the progress file;
  the coach may also file a `question` issue).
- A new charter idea worth a follow-up session.

## Done when

- `qa/.work/notes/explore-<date>-<topic>.md` has a charter, a setup block, a filled log, and the
  `Areas not reached` and `New charter ideas` sections.
- Every finding row in the log has a corresponding issue number or a question in the progress
  file.

## Record in progress

Add a `Completed` row for T2-12 with output links to the notes file and any issue numbers filed.
Set `Current item` to the lowest-numbered incomplete mission in the highest unlocked tier, or the
next exploratory charter if the coach and tester want another session. End the session log with
what the tester learned and any open questions.
