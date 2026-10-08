# Install Pitwall as a coding agent

This is a walkthrough for a coding agent that just cloned Pitwall and needs a working
installation, verified at each stage. Run every command from the repository root unless a
step says otherwise. Never run a paid operation without the user's explicit authorization,
and never print or commit a credential value; every command below reports variable names,
not values.

## 1. Prerequisites and toolchain

Pitwall needs Python 3.14.7, managed through `uv`. The registry path also needs Docker.
Install the pinned dependency set:

```bash
uv sync --frozen --extra dev --python 3.14.7
```

Verify:

```bash
uv run pitwall --help
```

This should print the top-level command list with no error.

## 2. Choose a mode

Pitwall runs in one of two modes, decided by whether `DATABASE_URL` is set in the
environment:

- **Personal mode** (no `DATABASE_URL`): only a RunPod credential is required. State lives
  on disk; there is no database.
- **Registry mode** (`DATABASE_URL` set): Postgres, Redis, and the Pitwall API back a
  shared capability and provider registry.

`pitwall doctor` reports which mode it detected as `mode personal` or `mode registry` in
its summary line, and only runs that mode's checks. Pick personal mode to try Pitwall
quickly with a single RunPod credential; pick registry mode to exercise the full API,
routing, and cost-control surface.

## 3. Personal path

Run setup once. It resolves a RunPod credential, creates a local endpoint key, offers to
export that key from your shell profile, and reports whether the routing command (`pitwall agents`, or
`PITWALL_ROUTING_CLI` when set) is on `PATH`:

```bash
uv run pitwall setup
```

Then run the readiness report:

```bash
uv run pitwall doctor
```

Each line has the form `[status] check.id: detail`, followed by `next: <step>` when there
is a next step to take. The last line is a summary, for example:

```text
doctor: fail (3 ok, 1 warn, 1 fail, 2 skip), mode personal, pitwall 0.1.0a2
```

Read `next:` for any `fail` line, act on it, and rerun `pitwall doctor` until it reports no
`fail`. `--json` prints the same report as one JSON object instead, for a script to parse:

```bash
uv run pitwall doctor --json
```

## 4. Registry path

Start local Postgres and Redis:

```bash
docker compose -f docker-compose.testinfra.yml up -d --wait
```

Export the same variables the README quickstart uses:

```bash
export DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test
export REDIS_URL=redis://127.0.0.1:6380/0
export RUNPOD_API_KEY=local-dry-run-key
export PITWALL_ADMIN_SECRET=local-admin-secret
export PITWALL_API_TOKEN=local-api-token
```

Apply migrations and seed a demo capability and provider:

```bash
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

In a second terminal, start the API:

```bash
uv run pitwall-api
```

Back in the first terminal, run the readiness report with the dry-run canary against the
seeded demo capability:

```bash
uv run pitwall doctor --canary embedding.demo
```

Read `next:` for any `fail` line, act on it, and rerun until the summary reports no `fail`.
The canary sends a dry-run inference only; it never triggers paid GPU work.

## 5. Register the MCP server

Preview what registration will change, for every harness detected on this machine:

```bash
uv run pitwall mcp install --dry-run
```

This writes nothing. For each detected harness it prints the config path, the exact
snippet or command it would apply, and (outside registry mode) a note that `pitwall mcp serve broker`
still needs `DATABASE_URL`, `REDIS_URL`, and `RUNPOD_API_KEY` where the harness runs.
Review the output, then register for real:

```bash
uv run pitwall mcp install
```

Each successful write prints `wrote <path>` and a `verify:` line naming the check to run
in that harness, for example `claude mcp list`. Run the printed verify command to confirm
the harness sees the `pitwall` server. Per-harness detail, including uninstall commands and
exact registration output, is in [`claude-code.md`](claude-code.md),
[`codex.md`](codex.md), and [`opencode.md`](opencode.md).

Codex only loads MCP servers from its user-scope config; `--scope project` fails for
`codex` with an error naming the fix. Claude Code and OpenCode support both scopes. To
register one harness and scope explicitly instead of autodetecting, name it:

```bash
uv run pitwall mcp install claude-code --scope project --project-root .
```

## 6. What an agent must not do

- Never run a paid operation (a live RunPod launch, a non-dry-run inference call) without
  the user's explicit authorization. `pitwall doctor` and `pitwall mcp install --dry-run`
  only read; the registry-path steps above use `dry_run: true` throughout.
- Never print, log, or commit a credential value. Checks and registrations only ever name
  a variable (`RUNPOD_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `PITWALL_CONFIG_FILE`), never
  its contents.
- Never run `pitwall mcp install` against a real home directory without reviewing the
  `--dry-run` output first.

## 7. Doctor check catalogue

Every check below is a `check.id` from a `pitwall doctor` report line. `phase` groups
related checks; `fix` is the action named in that check's `next:` line, when it has one.

| id | phase | meaning | fix |
| --- | --- | --- | --- |
| `install.python` | install | Pitwall requires Python 3.14. | `uv sync --frozen --extra dev --python 3.14.7` |
| `config.file` | config | Whether an optional `pitwall.toml` loaded, or failed to load. | Fix the file named in the detail, then rerun `pitwall doctor`. |
| `personal.runpod_credential` | config | Personal mode only. Whether a RunPod credential was found, from `RUNPOD_API_KEY` or `runpodctl`'s own config. | Export `RUNPOD_API_KEY`, or run `runpodctl doctor`. |
| `personal.endpoint_key` | config | Personal mode only. Whether the local endpoint key exists and `PITWALL_ENDPOINT_KEY` is exported in this shell. | Run `pitwall setup`, or export `PITWALL_ENDPOINT_KEY` in a new shell. |
| `personal.routing_cli` | install | Personal mode only. Whether the routing command (`pitwall agents`, or `PITWALL_ROUTING_CLI`) is on `PATH`, so profiles can attach. | Install the release wheel (see the [Quick Start](../../README.md#quick-start)), or unset `PITWALL_ROUTING_CLI` to use the built-in `pitwall agents`. |
| `personal.leases` | services | Personal mode only. How many pod leases are recorded in local state. | None; a warning names an unreadable state file. |
| `registry.mode` | registry | Personal mode only. Confirms `DATABASE_URL` is not set. | None; this is informational. |
| `config.runtime` | config | Registry mode only. Whether the loaded configuration passes domain checks for the API service. | Run `pitwall config check` and address the named codes. |
| `db.connect` | services | Registry mode only. Whether Postgres at `DATABASE_URL` is reachable. | Confirm Postgres is running and `DATABASE_URL` is correct. |
| `db.migrations` | install | Registry mode only. Whether every migration has been applied. | Run `pitwall db migrate`. |
| `registry.capabilities` | registry | Registry mode only. Whether at least one capability is enabled. | Run `pitwall init`. |
| `registry.providers` | registry | Registry mode only. Whether at least one provider is enabled and healthy. | Run `pitwall init`, or `pitwall set-provider-health <provider-id> healthy`. |
| `redis.connect` | services | Registry mode only. Whether Redis at `REDIS_URL` is reachable. | Set `REDIS_URL`, and confirm Redis is running. |
| `spend.budget` | spend | Registry mode only. Whether the monthly budget and per-request cap are sane. | Adjust the budget or cap in configuration. |
| `spend.kill_switch` | spend | Registry mode only. Whether the emergency kill switch is engaged. | Read [incident response](../operator/incident-response.md). |
| `spend.burn_rate` | spend | Registry mode only. Whether spend to date and its forecast stay under budget. | Reduce spend, or raise the budget. |
| `api.health` | services | Registry mode only. Whether the Pitwall API answers `/v1/health`. | Run `uv run pitwall-api`. |
| `canary.dry_run` | canary | Registry mode only, and only with `--canary <capability>`. Whether a dry-run inference against that embedding capability succeeded. | Confirm the capability name is enabled and the API is healthy. |
