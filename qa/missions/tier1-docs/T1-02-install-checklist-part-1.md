# T1-02 — Install checklist, part 1

**Tier:** 1 | **Mode:** training | **Repeatable:** no | **Needs:** T1-01 | **Output:** issues

## Why this matters

The install checklist is the project's own hermetic acceptance runbook. If it drifts,
releases get signed off against wrong steps. Walking steps 1 to 6 against the public
artifact catches every command that no longer matches the code.

## Sources

- [Prerequisites](../../../docs/operator/install-acceptance-checklist.md#prerequisites)
- [Steps 1 to 6](../../../docs/operator/install-acceptance-checklist.md#step-1--fresh-clone)
- [Coach safety](../../coach/rules.md#safety)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)).
The checklist says not to source any internal-only environment files; use only the values it lists. Step 1's clone is a scratch clone under `/tmp`; nothing from it touches this checkout. With the stack up, `docker ps` shows the local test stack on ports 5444 and 6380; stop it at the end of part 1 or hand it to part 2 (T1-03) as the progress file says.

## Setup

`docker ps` shows no container bound to 5444 or 6380 (rule R13). Use only the placeholder values the checklist lists; do not paste real keys (rule R1). See [bug reports](../../handbook/bug-reports.md) for the filing format.

## Steps

### Step 1 — Fresh clone

Tester does:

```bash
E="$PWD/qa/.work/evidence"
tmpdir="$(mktemp -d)"
git clone https://github.com/Buckeyes22/pitwall.git "$tmpdir/pitwall-public"
cd "$tmpdir/pitwall-public"
git remote -v | tee "$E/T1-02-step1.txt"
ls pyproject.toml src docs tools | tee -a "$E/T1-02-step1.txt"
```

Expected: clone succeeds; `git remote -v` shows only the public GitHub remote; the four
paths in the last command each exist. `E` points at your checkout's evidence folder, so every
later save in parts 1 and 2 goes to `"$E/<name>.txt"` even from inside the scratch clone.

If different: a clone failure, a missing directory, or an extra remote is a finding. A
credential prompt is also a finding.

### Step 2 — Dependency installation

Coach: the checklist's `export` lines hold placeholder values only. Keep using this terminal
for steps 3 to 6, because the exports live only here.

Tester does: `uv sync --extra dev`, then the five `export` lines from the checklist's step 2,
then its check:

```bash
env | grep -c -E '^(RUNPOD_API_KEY|DATABASE_URL|REDIS_URL|PITWALL_ADMIN_SECRET|PITWALL_WEBHOOK_SECRET)='
```

Expected: the sync finishes without an error, and the count is `5`. Save the output to
`"$E/T1-02-step2.txt"`.

### Step 3 — Local infrastructure

Tester does:

```bash
docker compose -f docker-compose.testinfra.yml up -d
docker compose -f docker-compose.testinfra.yml ps
docker compose -f docker-compose.testinfra.yml exec postgres pg_isready -U pitwall -d pitwall_test
```

Expected: `postgres` and `redis` service containers start; `ps` shows both with
`(healthy)`; `pg_isready` prints `accepting connections`. Save the output to
`"$E/T1-02-step3.txt"`.

### Step 4 — Database migration

Tester does:

```bash
uv run pitwall db migrate
uv run pitwall db status
```

Expected: each migration script reports `applied`; the final line contains `OK` or a
migration count; `db status` reports all migrations `applied` and zero `pending`. Save
the output to `"$E/T1-02-step4.txt"`.

### Step 5 — Seed capability and provider

Coach: use path A (`pitwall init --non-interactive`); if it fails, fall back to path B
(`register-endpoint`).

Tester does:

```bash
uv run pitwall init --non-interactive
uv run pitwall set-provider-health prov_demo_runpod_lb healthy
```

Expected: `Pitwall init complete`; a capability and provider are created from `seed/`;
the first provider is marked `healthy`; `set-provider-health` prints
`Provider health updated: prov_demo_runpod_lb`. Save the output to
`"$E/T1-02-step5.txt"`.

### Step 6 — Successful inference (dry-run)

Tester does:

```bash
uv run pitwall-api &
curl -s -X POST "http://127.0.0.1:8080/v1/inference" \
  -H "Content-Type: application/json" \
  -d '{"capability": "embedding.demo", "texts": ["hello"], "dry_run": true}' \
  | uv run python -m json.tool
curl -s http://127.0.0.1:8080/v1/capabilities | uv run python -m json.tool | grep '"name"'
```

Expected: uvicorn starts on `127.0.0.1:8080`; the inference reply shows `"dry_run": true` and a
non-null `"selected_provider_id"`; the capabilities list shows `"embedding.demo"`. Leave the API
running for part 2. Save the output to `"$E/T1-02-step6.txt"`. Any 5xx, missing
field, or stack trace is a finding. A `401` usually means the README `export` lines are also set
in this terminal (`env | grep -c PITWALL_API_TOKEN` prints `1`); use a fresh terminal.

## What counts as a finding

- A command that fails as written against the public artifact.
- A missing prerequisite the checklist did not list.
- A vague instruction that does not say how to know the step worked.
- Output that contradicts the step's **Expected** line.
- A checklist row that points at something that does not exist.

Every finding is filed through [bug reports](../../handbook/bug-reports.md) with
`found-by-qa` and a severity label.

## Done when

Steps 1 through 6 are each ticked in a note or filed as an issue. Note in the progress
file whether the stack and the API were left running for part 2 (T1-03).

## Record in progress

Add a `Completed` row with links to the six evidence files and to every filed issue.
Set `Current item: T1-03`. Write a short session log entry.
