# T2-06 — Database lifecycle guards

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T2-05 | **Output:** issues

## Why this matters

`pitwall db reset` drops the `pitwall` schema and wipes every table inside it. Run it against the
wrong Postgres and the next database backup is the only thing standing between the operator and a
quiet outage. Two guards stop that: the command refuses to run without `--force`, and the host
must be loopback (`127.0.0.1`, `localhost`, or `::1`).

## Sources

- [User journey catalog, J06 (db lifecycle guardrails)](../../../docs/operator/user-journey-catalog.md)
- [J06 in the journey harness](../../../scripts/release/run-user-journeys.sh)
- [CLI exit codes](../../../docs/sdlc/18-cli.md#exit-codes)
- [Logs, tracebacks, and exit codes](../../concepts/logs-tracebacks-and-exit-codes.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Only the README
Quick Start placeholder values. `pitwall db reset` runs only against the local test stack. Step 4
points at a made-up remote host on purpose, to prove the refusal.

## Setup

Run the tier 2 standard setup and leave the API off.

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
# The five export lines from the README Quick Start are set in every terminal this mission uses.
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

Expected: no other clone's Postgres on `5444` or Redis on `6380`; both services started; both `uv
run` commands finished without errors. Do not start the API for this mission.

## Steps

### Step 1 — `db status` shows the migration history

Coach: every command in this mission writes its output to `qa/.work/evidence/T2-06-*.txt` so the
tester can compare against the exit-code table later.
Tester does:

```bash
uv run pitwall db status 2>&1 | tee qa/.work/evidence/T2-06-status.txt; echo "exit=${PIPESTATUS[0]}"
```

Expected: a `Migrations` table with every row marked `applied`, a one-line `N applied, 0 pending,
M total` summary, and `exit=0`.

### Step 2 — `db migrate` is idempotent

Coach: `db migrate` runs every pending migration. Running it again on a fresh database must apply
nothing and still exit `0`.
Tester does, twice:

```bash
uv run pitwall db migrate 2>&1 | tee qa/.work/evidence/T2-06-migrate-1.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall db migrate 2>&1 | tee qa/.work/evidence/T2-06-migrate-2.txt; echo "exit=${PIPESTATUS[0]}"
```

Expected: `exit=0` both times. Setup already migrated, so both runs print `All N migrations
already applied.` and apply nothing new. A run that applies a migration or returns non-zero is a
finding.

### Step 3 — `db reset` refuses without `--force`

Coach: the guard checks for `--force` before touching the database. With no flag, it returns
`exit=1` and prints a clear refusal.
Tester does:

```bash
uv run pitwall db reset 2>&1 | tee qa/.work/evidence/T2-06-reset-no-force.txt; echo "exit=${PIPESTATUS[0]}"
```

Expected: a refusal message that names the `--force` flag, and `exit=1`. The Postgres data is
unchanged.

### Step 4 — `db reset --force` still refuses a non-local host

Coach: the second guard checks the host. `${DATABASE_URL/127.0.0.1/db.example.invalid}` is a bash
parameter substitution that copies `DATABASE_URL` and replaces the first `127.0.0.1` with
`db.example.invalid`. The original `DATABASE_URL` is left untouched; the new value goes to
`REMOTE_URL`, then into the inline `DATABASE_URL="$REMOTE_URL"` for that one command. `db.example.invalid`
is reserved by RFC 2606 and never resolves, so this stays a local proof of refusal.
Tester does:

```bash
REMOTE_URL="${DATABASE_URL/127.0.0.1/db.example.invalid}"
echo "remote URL: $REMOTE_URL"
DATABASE_URL="$REMOTE_URL" uv run pitwall db reset --force 2>&1 \
  | tee qa/.work/evidence/T2-06-reset-remote.txt
echo "exit=${PIPESTATUS[0]}"
```

Expected: a refusal message that names `db.example.invalid` (the host check), and `exit=1`. The
local Postgres data is unchanged.

### Step 5 — `db reset --force`, then re-create everything

Coach: with the local stack and the quick-start `DATABASE_URL`, the command drops the `pitwall`
schema, then the follow-up `migrate` and `init` rebuild it and restore the demo data.
Tester does:

```bash
uv run pitwall db reset --force 2>&1 | tee qa/.work/evidence/T2-06-reset-local.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall db status 2>&1 | tee qa/.work/evidence/T2-06-status-after-reset.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall db migrate 2>&1 | tee qa/.work/evidence/T2-06-migrate-3.txt; echo "exit=${PIPESTATUS[0]}"
uv run pitwall init --non-interactive 2>&1 | tee qa/.work/evidence/T2-06-init.txt; echo "exit=${PIPESTATUS[0]}"
```

Expected: `db reset --force` prints `Dropped pitwall schema.` and `exit=0`. With the schema gone,
`db status` prints `schema_migrations table does not exist yet. Run 'pitwall db migrate' first.`
and `exit=1`. `db migrate` then applies every migration and exits `0`, and `init
--non-interactive` puts the demo capability and provider back with `exit=0`. A `db reset --force`
against the local stack that exits non-zero is a finding.

## What counts as a finding

- A guard that lets `db reset --force` through against a non-local host (severity 1).
- A guard missing the `--force` check, letting `db reset` succeed with no flag (severity 1).
- `db migrate` returning non-zero on a clean database, or applying migrations on the second run.
- `db reset --force` returning non-zero against the local stack, or leaving behind objects it
  should have dropped.
- An exit code that differs from the [exit-code table](../../../docs/sdlc/18-cli.md#exit-codes) for
  any command in this mission.

## Done when

- All five steps produced saved evidence files under `qa/.work/evidence/T2-06-*.txt`.
- `db status` showed `N applied, 0 pending, N total.` in Step 1.
- `db migrate` returned `exit=0` both times in Step 2.
- `db reset` returned `exit=1` in Steps 3 and 4, with no data change on the remote-host attempt.
- `db reset --force` returned `exit=0` in Step 5, and `db migrate` plus `init` rebuilt the
  registry.
- Every mismatch with the [exit-code table](../../../docs/sdlc/18-cli.md#exit-codes) is filed using
  [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-06 with output links to the evidence files and any issues filed. Set
`Current item` to the next item the
[mission list](../../missions/README.md#mission-list) picks. End the session log with what the
tester learned and any open questions.
