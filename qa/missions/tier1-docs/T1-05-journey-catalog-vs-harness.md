# T1-05 — Journey catalog vs harness

**Tier:** 1 | **Mode:** training | **Repeatable:** no | **Needs:** T1-01 | **Output:** issues

## Why this matters

The [user journey catalog](../../../docs/operator/user-journey-catalog.md) lists every hermetic
journey that the README promises a new user can walk through. The catalog is a contract. The
harness in [`scripts/release/run-user-journeys.sh`](../../../scripts/release/run-user-journeys.sh)
is supposed to check every claim in every row. When a row claims something the harness does not
actually check, the catalog lies to the next reader, and broken journeys ship.

## Sources

- [`docs/operator/user-journey-catalog.md`](../../../docs/operator/user-journey-catalog.md) — the
  catalog with rows J01–J27 and their "Expected outcome" columns
- [`scripts/release/run-user-journeys.sh`](../../../scripts/release/run-user-journeys.sh) — the
  hermetic harness, one function per row (`j01()` … `j27()`)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)).

This mission only reads files. Do not run `scripts/release/run-user-journeys.sh` itself; that is on
the never-run list outside the local test stack. Use `grep`, `sed`, `cat`, and reading.

## Setup

Open the catalog and the harness side by side. Pick a row to inspect.

```bash
ls docs/operator/user-journey-catalog.md scripts/release/run-user-journeys.sh
```

Expected: both paths printed.

## Steps

### Step 1 — Learn the row-to-function map

Coach: explain that every row in the catalog has an id (`J01` … `J27`) and the harness has one
function per id (`j01()` … `j27()`). The harness may also have helper functions; only the
top-level `jNN()` function is what counts as "the harness check for that row".

Tester does:

```bash
grep -n '^j[0-9][0-9]()' scripts/release/run-user-journeys.sh
```

Expected: 27 lines, one per function, with line numbers. Save it:

```bash
grep -n '^j[0-9][0-9]()' scripts/release/run-user-journeys.sh \
  | tee qa/.work/evidence/t1-05-functions.txt
```

### Step 2 — Work through J01 with the coach

Coach: read J01's "Expected outcome" column aloud. It says a dry-run inference returns
`"dry_run": true` and `"selected_provider_id": "prov_demo_runpod_lb"`. The tester checks whether
the `j01()` function asserts both of those fields.

Tester does:

```bash
sed -n '/^j01() {/,/^}/p' scripts/release/run-user-journeys.sh
```

Expected: the function calls `init`, starts the API, hits `/v1/inference`, and uses `json_assert`
to compare both fields. Save the slice:

```bash
sed -n '/^j01() {/,/^}/p' scripts/release/run-user-journeys.sh \
  | tee qa/.work/evidence/t1-05-j01.txt
```

Coach: ask the tester to mark "yes" or "no" in the journal note for each claim in the row.

### Step 3 — Loop the rest of the rows

Coach: for each remaining hermetic row, the tester finds the function and reads it. Show the
pattern once more, then let the tester run it for J02, J03, J04, and so on through J27. The
tester records one line per row in `qa/.work/notes/t1-05-journal.md` with the format:

```
JNN | claims: <short list> | checked: yes/no | finding: <link or "-">
```

Tester does, for each row, an example for J07:

```bash
grep -n '^j07()' scripts/release/run-user-journeys.sh
sed -n '/^j07() {/,/^}/p' scripts/release/run-user-journeys.sh \
  | tee qa/.work/evidence/t1-05-j07.txt
```

Expected: line number returned, then the function body, with no error. The tester reads the body
and compares each catalog claim against the assertions.

If different: a missing function or a wrong range is a finding itself; record it and continue.

### Step 4 — File findings

Coach: any row whose function does not check one of its catalog claims is a finding. The
catalog is a doc, so the label is `documentation`. One issue per missing claim, with the
catalog row text quoted and the function body attached as evidence.

Tester does: drafts one issue per mismatch in `qa/.work/notes/`, shows it to the coach, then
posts with `gh issue create --title "<title>" --body-file qa/.work/notes/<short-name>.md
--label documentation --label found-by-qa --label severity:<n>-<name>`.

Expected: every mismatch now has an open issue with a link in the journal note.

## What counts as a finding

- A row's function is missing (no `jNN()` definition exists).
- A row's function does not assert one of the claims listed in its "Expected outcome" column.
- A row's function asserts more than the catalog claims (extra checks that the doc does not
  document).
- The row's text refers to a flag, command, or path that does not exist anywhere in the
  function.

Every finding is filed through [bug reports](../../handbook/bug-reports.md) with `found-by-qa`
and one severity label.

## Done when

- All 27 rows are compared, each result is recorded in `qa/.work/notes/t1-05-journal.md`.
- Every mismatch has a filed issue; the issue link is in the journal note.
- `grep -n '^j[0-9][0-9]()' scripts/release/run-user-journeys.sh` still lists 27 functions
  (re-run it to confirm no row was added without a function or vice versa).

## Record in progress

Add a `Completed` row for T1-05 with a link to the journal note. Set `Current item: T1-06`.
Write a session log entry that lists the count of rows checked and the count of issues filed.
