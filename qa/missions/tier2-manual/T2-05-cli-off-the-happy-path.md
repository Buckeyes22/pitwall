# T2-05 — The CLI off the happy path

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T1-04 | **Output:** issues

## Why this matters

Real users mistype flags, run commands in the wrong shell, and ask for things the CLI cannot do. The
CLI must refuse those calls clearly and return the
[exit code](../../concepts/logs-tracebacks-and-exit-codes.md) shown in the
[exit-code table](../../../docs/sdlc/18-cli.md#exit-codes). A wrong exit code breaks every script
that checks it, and a silently ignored typo runs a command the user did not ask for.

## Sources

- [CLI exit codes](../../../docs/sdlc/18-cli.md#exit-codes)
- [CLI argument parsing errors](../../../docs/sdlc/18-cli.md#cli-argument-parsing-errors)
- [Logs, tracebacks, and exit codes](../../concepts/logs-tracebacks-and-exit-codes.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Only the README
Quick Start placeholder values. The tester runs only the nine commands in Step 3 and the two
`pitwall status` lines in Step 4, exactly as written. Never run `pitwall status` without
`DATABASE_URL`: that switches it to the personal backend, which manages RunPod pods.

## Setup

Run the tier 2 standard setup, then leave the API off.

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
# The five export lines from the README Quick Start are set in every terminal this mission uses.
uv run pitwall db migrate
uv run pitwall init --non-interactive
mkdir -p qa/.work/evidence qa/.work/notes
```

Expected: no other clone's Postgres on `5444` or Redis on `6380`; both services started; both `uv
run` commands finished without errors.

## Steps

### Step 1 — Load the probe

Coach: `probe` runs one command four ways and saves everything to an evidence file: with a made-up
flag (`--bogus`), without `DATABASE_URL`, with `--json`, and with `--help`. `"$@"` stands for the
words after `probe`; `IFS=-` joins them with dashes, so `db status` becomes `db-status` in the file
name. The tester pastes it once per terminal; it is not a command to memorise.
Tester does:

```bash
probe() {
  local name; name="$(IFS=-; echo "$*")"
  {
    echo "== pitwall $* --bogus"; uv run pitwall "$@" --bogus; echo "bogus_exit=$?"
    echo "== no DATABASE_URL"; env -u DATABASE_URL uv run pitwall "$@"; echo "nodb_exit=$?"
    echo "== --json"; uv run pitwall "$@" --json > "qa/.work/evidence/T2-05-$name.json"; echo "json_exit=$?"
    uv run python -m json.tool "qa/.work/evidence/T2-05-$name.json" > /dev/null; echo "json_valid=$?"
    echo "== --help"; uv run pitwall "$@" --help; echo "help_exit=$?"
  } 2>&1 | tee "qa/.work/evidence/T2-05-$name.txt" | grep -E '^[a-z_]+=[0-9]+$' | tr '\n' ' '
  echo " <- $*"
}
```

Expected: no output. `type probe | head -1` prints `probe is a function`.

### Step 2 — Probe one command and read the evidence

Tester does:

```bash
probe db status | tee -a qa/.work/notes/T2-05-cli.md
cat qa/.work/evidence/T2-05-db-status.txt
```

Expected: `bogus_exit=2 nodb_exit=1 json_exit=0 json_valid=0 help_exit=0  <- db status`. In the
file: `pitwall db status: error: unrecognized arguments: --bogus` with the usage line under it,
then `DATABASE_URL is not set`, then the usage line again from `--help`. Coach: walk each `== ...`
block with the tester and name the exit code it earned.

### Step 3 — Probe the other eight

Tester does:

```bash
for c in "db migrate" "config check" "leases list" "cost summary" "cost workloads" \
         "burn-rate" "guardrails status" "models list"; do
  read -ra words <<<"$c"
  probe "${words[@]}" | tee -a qa/.work/notes/T2-05-cli.md
done
```

Expected (measured on a clean clone):

| Command | bogus | no DB | json | json valid | help | No-DB message |
| --- | --- | --- | --- | --- | --- | --- |
| `db migrate` | 2 | 1 | 0 | 0 | 0 | `DATABASE_URL is not set` |
| `config check` | 2 | 78 | 0 | 0 | 0 | `ERROR [missing-runtime-config] ... DATABASE_URL` |
| `leases list` | 2 | 1 | 0 | 0 | 0 | `lease operation failed` |
| `cost summary` | 2 | 1 | 0 | 0 | 0 | `dsn or DATABASE_URL environment variable is required` |
| `cost workloads` | 2 | 1 | 0 | 0 | 0 | same as `cost summary` |
| `burn-rate` | 2 | 1 | 0 | 0 | 0 | `burn_rate_unavailable` |
| `guardrails status` | 2 | 0 | 0 | 0 | 0 | none: it needs no database |
| `models list` | 2 | 0 | 0 | 0 | 0 | none: it needs no database |

Coach: `leases list` and `burn-rate` print a fixed message on purpose. They never echo database or
provider text, which could contain a secret. `78` is `EX_CONFIG`, "configuration is wrong".

### Step 4 — `pitwall status` on the database backend

Coach: with `DATABASE_URL` set, `pitwall status` lists database leases. It once skipped its flag
check there (`--bogus` exited `0`, `--json` printed a table); this step guards the fix.
Tester does:

```bash
uv run pitwall status --bogus 2>&1 | tee qa/.work/evidence/T2-05-status.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall status --json | tee -a qa/.work/evidence/T2-05-status.txt | uv run python -m json.tool > /dev/null
echo "json_exit=${PIPESTATUS[0]} json_valid=${PIPESTATUS[2]}"
```

Expected: `pitwall status: error: unrecognized arguments: --bogus` and `exit=2`; then
`json_exit=0 json_valid=0`, and the saved JSON has `items`, `total`, and `refreshed_at`.

### Step 5 — Compare against the exit-code table

Tester does: `cat qa/.work/notes/T2-05-cli.md`, then finds each command's row in the
[exit-code table](../../../docs/sdlc/18-cli.md#exit-codes); `78` is explained under it.
Expected: nine notes lines, each matching Step 2 or Step 3 and the exit-code table.

## What counts as a finding

- An exit code that differs from Steps 2–4 or the
  [exit-code table](../../../docs/sdlc/18-cli.md#exit-codes).
- `bogus_exit=0`: a mistyped flag silently ignored (severity 2; the command ran anyway).
- `json_valid` other than `0` after `json_exit=0`: `--json` printed something that is not JSON.
- A Python traceback (`Traceback (most recent call last):`) anywhere in an evidence file.
- A no-DB message that differs from the table above.

## Done when

- Nine notes lines; a `.txt` and `.json` evidence file per command; Step 4's `exit=2` and
  `json_exit=0 json_valid=0` saved in `T2-05-status.txt`.
- Every mismatch is filed using [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-05 with output links to the notes file and any issues filed. Set
`Current item` to [T2-06 — Database lifecycle guards](T2-06-database-guards.md). End the session
log with what the tester learned and any open questions.
