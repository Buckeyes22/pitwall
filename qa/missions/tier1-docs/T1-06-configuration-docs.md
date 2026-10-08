# T1-06 — Configuration docs cross-check

**Tier:** 1 | **Mode:** training | **Repeatable:** no | **Needs:** T1-01 | **Output:** issues

## Why this matters

Configuration docs that disagree cause failed starts and unsafe deployments. If the
[README Configuration table](../../../README.md#configuration) lists a variable but
[`.env.example`](../../../.env.example) has no entry for it, the tester cannot follow the README.
If [`docs/sdlc/16-core-config.md`](../../../docs/sdlc/16-core-config.md) names a default the
README does not, the next operator who copies the README gets the wrong value. When the live
`pitwall config check` exits non-zero after the docs say it should exit zero, the boot contract
is broken.

## Sources

- [`README.md` Configuration section](../../../README.md#configuration) — the public contract
- [`.env.example`](../../../.env.example) — every key the project recognizes at boot
- [`docs/sdlc/16-core-config.md`](../../../docs/sdlc/16-core-config.md) — the field-by-field
  reference, including defaults and pydantic names
- [`docs/sdlc/18-cli.md` `cmd_config`](../../../docs/sdlc/18-cli.md#3-command-inventory) — what
  `pitwall config check` does and the exit codes it returns

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)).

This mission only compares names. Never put real values in `.env`. Use only the five export
lines from the README Quick Start, and only in this terminal session. Never commit any of them.
When you need to read the run behavior, use `uv run` so the project venv is used.

## Setup

Export the five lines from the README Quick Start in this terminal, exactly as printed there.
This gives the local config check something to validate.

```bash
ls .env.example docs/sdlc/16-core-config.md
```

Expected: both paths printed.

## Steps

### Step 1 — Cross-check each README variable

Coach: the README Configuration table is a list of variables the project promises exist at boot.
For each one, the tester confirms the name appears in both `.env.example` and `16-core-config.md`.
A variable in the README that is missing from either doc is a finding.

Tester does, for the first variable as an example:

```bash
grep -n 'DATABASE_URL' .env.example docs/sdlc/16-core-config.md \
  | tee qa/.work/evidence/t1-06-database-url.txt
```

Expected: at least one matching line in each of the two files. Save the slice. Repeat for every
variable listed in the README Configuration table. Record each result in
`qa/.work/notes/t1-06-journal.md` with the format:

```
NAME | README row: yes/no | .env.example: yes/no | 16-core-config: yes/no | finding: <link or "-">
```

If different: a missing match in either file is a documentation finding; record it and continue
to the next variable.

### Step 2 — Run `pitwall config check` with the env set

Coach: this command is what the README points an operator at before starting the API. It must
exit zero when all five exports are set, otherwise the README Quick Start is a lie.

Tester does:

```bash
uv run pitwall config check 2>&1 | tee qa/.work/evidence/t1-06-config-check-ok.txt
echo "exit=${PIPESTATUS[0]}"
```

Expected: a configuration report printed to stdout, then `exit=0` on its own line.

If different: any non-zero exit, any traceback, or any message that does not match the
`format_config_check_result` shape from `18-cli.md` is a finding.

### Step 3 — Run `pitwall config check` with `DATABASE_URL` removed

Coach: the docs say `DATABASE_URL` is required and that removing it makes `config check` fail
closed with exit code 78 (`os.EX_CONFIG`). The tester confirms both: the message must name
`DATABASE_URL`, and the exit code must be 78.

Tester does:

```bash
env -u DATABASE_URL uv run pitwall config check 2>&1 \
  | tee qa/.work/evidence/t1-06-config-check-no-db.txt
echo "exit=${PIPESTATUS[0]}"
```

Expected: the output names `DATABASE_URL` (the exact variable, in code or text), and the next
line is `exit=78`. Compare both behaviors with the section about `cmd_config` in
[18-cli.md](../../../docs/sdlc/18-cli.md#3-command-inventory); the CLI doc says
`return os.EX_CONFIG` on a validation error, and `os.EX_CONFIG` is 78.

If different: a different exit code, or a message that does not name `DATABASE_URL`, is a
finding. Note the actual numbers and the actual message.

## What counts as a finding

- A variable in the README Configuration table is not in `.env.example`, or not in
  `16-core-config.md`, or not in both.
- A variable name in `.env.example` differs in spelling from the README (case, underscores).
- `pitwall config check` exits non-zero with all five exports set.
- `pitwall config check` without `DATABASE_URL` does not exit 78, or does not name
  `DATABASE_URL` in its message.
- The exit-code or message behavior disagrees with `18-cli.md`.

Every finding is filed through [bug reports](../../handbook/bug-reports.md) with `found-by-qa`
and one severity label. Documentation disagreements use `--label documentation`; runtime
disagreements use `--label bug`.

## Done when

- Every variable in the README Configuration table has been checked against both
  `.env.example` and `docs/sdlc/16-core-config.md`, and the result is in
  `qa/.work/notes/t1-06-journal.md`.
- Steps 2 and 3 have been run; their outputs are saved and compared with `18-cli.md`.
- Any mismatch is filed as an issue; the link is in the journal note.

## Record in progress

Add a `Completed` row for T1-06 with a link to the journal note. Set `Current item: T1-07`.
Write a session log entry that lists the count of variables checked and the count of issues
filed.
