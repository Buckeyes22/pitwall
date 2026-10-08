# T2-07 — TUI tour and exploratory pass

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T2-01 | **Output:** issues

## Why this matters

Operators live in the TUI every day. A crash, a missing view, or a layout that stops being
readable when the terminal is narrow are findings. Some actions are guarded by typed
confirmation, so the test must read them carefully without completing them.

## Sources

- [User journey J09 — TUI boots in a pty](../../../docs/operator/user-journey-catalog.md)
- [CLI, TUI section](../../../docs/sdlc/18-cli.md#srcpitwalltui)
- [Support matrix — Textual dashboard](../../../docs/support-matrix.md)
- [The TUI concept](../../concepts/the-tui.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Start the TUI
only in a terminal where the README Quick Start `export` lines (including `DATABASE_URL`) are set
(rule R2): without them it starts `pitwall setup`. Never finish a type-to-confirm dialog. Do not press `g` or `a` on the Providers view
or `g` inside a model's detail view; those contact providers and belong to tier 5.

## Setup

Run the tier 2 standard setup. Check the test stack is free, start it, set the five `export`
lines from the README Quick Start in every terminal, and run migrations plus the seed loader. Do
not start the API for this mission.

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

Expected: no other clone's Postgres on `5444` or Redis on `6380`; both compose services started;
both `uv run` commands finished without errors.

## Steps

### Step 1 — Boot the dashboard

Tester does (make the terminal at least 100 columns wide and 30 rows tall first):

```bash
uv run pitwall dashboard
```

Expected: the Overview view fills the screen, with key bindings in the footer and no traceback.
Quit with `q`.

### Step 2 — Walk the ten views

Coach: the ten views are Overview, Providers, Leases, Models, Serve, Pods, Routes, Cost,
Resources, and Operations. The Serve view puts the cursor in its Model field, so press `Escape`
there before the next view key; otherwise the letters are typed into the field.
Tester does: press `o`, `p`, `l`, `m`, `s`, `d`, `t`, `c`, `e`, `a` in turn, then `q`. Record the
view title shown in the header after each key into `qa/.work/notes/T2-07-views.txt`.
Expected: each view opens with its title. No traceback.

### Step 3 — Command palette and help

Tester does, from any view:

```bash
# press : then type cost then Enter; press ?; press Escape twice
```

Expected: typing `cost` opens the Cost view. `?` shows a key list matching Step 2. Escape
closes the help.

### Step 4 — Search and dossier in Models

Tester does, in Models:

```bash
# press / then type part of a model name, then Escape
# press Enter on a row, then m
```

Expected: rows filter to those containing the typed text. Escape clears the filter. Enter opens
a Markdown dossier with a variants table; `m` returns to the table.

### Step 5 — Read the Serve form, open Pods confirmation

Tester does, in Serve (`s`): read the form without submitting. In Pods (`d`), if a row exists,
open the stop confirmation and press Escape without typing the route name. Note the exact
wording in `qa/.work/notes/T2-07-confirmations.txt`.
Expected: the form is readable. The confirmation asks for the exact route name.

### Step 6 — Narrow terminal, revisit Models and Providers

Resize the terminal to under 100 columns wide and under 30 rows tall. Press `m`, then `Escape`,
then `p`.
Expected: the tables remain readable in compact form; no overlap, no clipped columns.

### Step 7 — Take the database away

Tester does:

```bash
# press q to quit
make down 2>&1 | tee qa/.work/evidence/T2-07-make-down.txt
uv run pitwall dashboard
# press q to quit
docker compose -f docker-compose.testinfra.yml up -d --wait
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

Expected: the console opens and the Overview shows `Overview unavailable: [Errno 111] Connect
call failed ('127.0.0.1', 5444)`. No traceback, no `DATABASE_URL` value, no password. After
restarting, migrations and init finish without errors.

### Step 8 — Compare the ten views to the docs

Open the [Operator TUI row](../../../README.md#architecture) and the [support matrix entry for the Textual
dashboard](../../../docs/support-matrix.md). Cross off each view from Step 2.
Expected: every view from Step 2 appears in the support matrix. Any view in Step 2 that is
missing from the README row, or any view in the README row that did not open, is a
documentation gap (label `documentation`, severity 3).

## What counts as a finding

- A traceback in the captured output (severity 2).
- A connection detail, DSN, or secret on the screen (severity 1).
- A view missing from the dashboard or misnamed in the header.
- A key listed in `?` that does not match the binding in [the TUI concept](../../concepts/the-tui.md)
  or the [CLI TUI section](../../../docs/sdlc/18-cli.md#srcpitwalltui).
- A layout that becomes unreadable under 100 columns.
- A README or support-matrix entry that disagrees with what the dashboard actually shows.

## Done when

- All eight steps have evidence in `qa/.work/evidence/T2-07-*.txt` and notes in
  `qa/.work/notes/T2-07-*.txt`.
- The ten view titles recorded in Step 2 match the support matrix.
- Any disagreement with the docs is filed with the tester using
  [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-07 with output links to the evidence and notes files and any issue
numbers filed. Set `Current item` to [T2-08 — Pitwall as an agent tool (MCP)](T2-08-mcp-agent-client.md).
End the session log with what the tester learned and any open questions.
