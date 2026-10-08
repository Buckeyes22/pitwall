# T1-04 — CLI help vs CLI reference

**Tier:** 1 | **Mode:** training | **Repeatable:** no | **Needs:** T1-01 | **Output:** issues

## Why this matters

The CLI reference promises commands, flags, and exit codes that scripts depend on. When
`pitwall --help` and `18-cli.md` disagree, every script built on the doc breaks. Walking
the two side by side catches the mismatches before they ship.

## Sources

- [Command inventory](../../../docs/sdlc/18-cli.md#3-command-inventory)
- [Exit codes](../../../docs/sdlc/18-cli.md#exit-codes)
- [Failure modes and error types](../../../docs/sdlc/18-cli.md#6-failure-modes--error-types)
- [Coach safety](../../coach/rules.md#safety)
- [Bug reports](../../handbook/bug-reports.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)).
For never-run-list commands, run only `--help`; never run the command itself (rule R2).
Step 4 needs the five `export` lines from the README Quick Start and the local test
stack; the rest do not.

## Setup

The stack and the README `export` lines are needed only for step 4. Confirm
`docker ps` shows no row for 5444 or 6380 (rule R13) before starting the stack.

## Steps

### Step 1 — Capture the top-level usage

Tester does:

```bash
uv run pitwall --help 2>&1 | tee qa/.work/evidence/T1-04-help.txt
```

Expected: a `Usage:` line listing every command group, and one description per command
(each description sits on its own line).

### Step 2 — Match every command to the reference's exit-code table

Coach: the [exit codes](../../../docs/sdlc/18-cli.md#exit-codes) table should have a row
for every command in the usage line. These commands write both lists, sorted, and print any
command the table is missing.

Tester does:

```bash
uv run pitwall --help 2>&1 | head -1 | tr '{|}' '\n\n\n' | grep -xE '[a-z-]+' | sort -u > qa/.work/notes/T1-04-help.txt
grep -oE '^\| `pitwall [a-z-]+' docs/sdlc/18-cli.md | cut -d' ' -f3 | sort -u > qa/.work/notes/T1-04-reference.txt
comm -23 qa/.work/notes/T1-04-help.txt qa/.work/notes/T1-04-reference.txt
```

Expected: the last command prints nothing. Each name it prints is a command without a row: a
`documentation` finding (one issue listing every missing name).

### Step 3 — Compare flags per command

Coach: for each command, run `--help` and compare the flags with the reference's
flag table. For long output, save to `qa/.work/evidence/T1-04-<command>-help.txt`.

Tester does (example for `models list`):

```bash
uv run pitwall models list --help 2>&1 | tee qa/.work/evidence/T1-04-models-list-help.txt
```

Expected: the same flags as the reference. A missing flag, an extra flag, or a
different default is a finding.

### Step 4 — Trigger the safe exit codes

Coach: run only the safe-to-trigger commands. Never run `pitwall setup`, `pitwall
dashboard` (without the README `export` lines), `pitwall serve` without `--dry-run`, or
any never-run-list command (rule R2).

Tester does, with the stack up and the five README `export` lines set:

```bash
env -u DATABASE_URL -u REDIS_URL uv run pitwall config check
echo "exit=$?"
```

Expected: an error message that names `missing-runtime-config`; `exit=78` (the
reference's exit-code table says `EX_CONFIG`).

```bash
uv run pitwall dashboard --bogus
echo "exit=$?"
```

Expected: `unrecognized arguments: --bogus`; `exit=2`.

```bash
uv run pitwall mcp
echo "exit=$?"
```

Expected: `the following arguments are required: command`; `exit=2`.

```bash
uv run pitwall db reset
echo "exit=$?"
```

Expected: `Refusing destructive database reset`; `exit=1`. A passing run here would
itself be a severity 1 issue (rule R14).

Save the four command outputs to `qa/.work/evidence/T1-04-exit-*.txt`.

### Step 5 — Verify JSON output

Tester does, with the stack up and the five `export` lines set:

```bash
uv run pitwall config check --json | uv run python -m json.tool | head -5
```

Expected: valid JSON (the `json.tool` invocation prints the parsed structure; a parse
error means `--json` is broken). Save the output to `qa/.work/evidence/T1-04-json.txt`.

## What counts as a finding

- A command in `--help` that has no section in `18-cli.md`.
- A flag in `--help` that is missing from the reference, or a flag in the reference
  that is missing from `--help`.
- An exit code that does not match the reference's table.
- Output that contradicts what the reference says the command does.

Every finding is filed through [bug reports](../../handbook/bug-reports.md) with
`found-by-qa` and a severity label.

## Done when

Every command in `--help` is found in the reference or filed; flags are compared; the
four safe exit codes are checked.

## Record in progress

Add a `Completed` row with links to the evidence files and to every filed issue. Set
`Current item: T1-05`. Write a short session log entry.
