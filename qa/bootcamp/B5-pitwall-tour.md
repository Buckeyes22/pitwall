# B5 — Pitwall tour

**Mode:** training | **Needs:** B4 | **Output:** none

## Goal

Understand what Pitwall is, its parts, and what is safe to test.

## You will learn

- [pitwall-in-one-page](../concepts/pitwall-in-one-page.md), [capabilities-and-providers](../concepts/capabilities-and-providers.md), [dry-run-and-hermetic](../concepts/dry-run-and-hermetic.md), [routing-and-plans](../concepts/routing-and-plans.md), [cost-budget-and-guardrails](../concepts/cost-budget-and-guardrails.md), [kill-switch](../concepts/kill-switch.md), and [the-tui](../concepts/the-tui.md).

## Before you start

The tester is at the repository root on `main`, with `docker` and `uv` working ([B2](B2-install-and-clone.md)). The local test stack is not yet running. `qa/.work/progress.md` exists and `Current item: B5`.

Not allowed in this lesson (rule R2): never run `pitwall dashboard`, or `pitwall` with no arguments, in a terminal where the five README `export` lines are not set, because it would start `pitwall setup`. Never run the commands Step 5 points out; `--help` on them is fine.

## Steps

### Step 1 — One page

Coach: read [pitwall-in-one-page](../concepts/pitwall-in-one-page.md) together with the tester.
Tester does: explains Pitwall in one sentence.
Expected: a sentence that says Pitwall is a control plane that takes requests, picks a provider, checks safety and cost, runs the work, and records what happened.
If different: read the card again and pick the words that fit best.

### Step 2 — Architecture

Coach: walk the flow line in [README architecture](../../README.md#architecture), from clients through pre-spend inspection, routing, cost admission, the static provider adapter, and finally the audit, Postgres, Redis, and reconciler layers.
Tester does: points at each box in order while explaining it.
Expected: every box named in the README, in order.
If different: point at the README again.

### Step 3 — Surfaces

Coach: Pitwall has several entry points. Each is tested at a different tier.

| Surface | Command | Tested in |
| --- | --- | --- |
| CLI | `uv run pitwall ...` | tiers 1–2 |
| REST API | `uv run pitwall-api`, then `http://127.0.0.1:8080/docs` | tier 2 |
| TUI | `uv run pitwall dashboard` | tier 2 |
| MCP server | `pitwall mcp serve broker` (used by agents) | tier 2 |
| Background services | reconciler, webhook receiver, cost exporter | tier 2 |
| Test suites | `make test`, `make test-int`, journey harness | tiers 1–4 (writing tests: tier 4) |

Tester does: says which surface tests which tier, in their own words.
Expected: matches the table.
If different: read [the missions README](../missions/README.md#the-tiers) together.

### Step 4 — Start the stack

Coach: rule R13 says always check `docker ps` first so no old container holds ports 5444 or 6380. Then bring up the test stack, load the five `.env.quickstart.local` settings from the README local broker setup in this terminal, migrate, and run `init`. The five lines are the placeholder values from the [README local broker setup](../../README.md#run-the-broker-locally); never invent your own values.
Tester does in terminal 1:
```bash
docker ps
docker compose -f docker-compose.testinfra.yml up -d --wait
```
Then set the five `export` lines from the README Quick Start in this same terminal and run:
```bash
uv run pitwall db migrate
uv run pitwall init --non-interactive
```
Expected: `docker ps` shows no row for 5444 or 6380 before the test stack starts; `init` finishes and prints a smoke-test command that uses `embedding.demo`.
If different: read the error out loud. If a port is busy, stop the holder first.

### Step 5 — The CLI

Coach: the CLI has many subcommands. Some are safe in tiers 0–2; some are not.
Tester does:
```bash
uv run pitwall --help 2>&1 | head -40
```
Expected: the usage line and the command list, including `setup`, `serve`, `status`, `stop`, `runpod`, `runpod-onboard`, `volume-files`, and `terminate-pod`.
If different: ask the coach which subcommand is missing.
Coach: point out the commands on rule R2's never-run list: `setup`, `serve` (allowed only with `--dry-run`), `status` and `stop` (a mission names the one allowed form), `runpod`, `runpod-onboard`, `volume-files`, `provider-ops`, `retention`, and `terminate-pod`. `--help` on any of them is fine; look, don't run.

### Step 6 — The API docs page

Coach: with a bearer token exported, even the `/docs` page needs it. On loopback Pitwall allows starting without a token and logs a warning. The tester starts the API without the token in a second terminal, after setting the same five `export` lines from the README Quick Start in that terminal too.
Tester does in terminal 2:
```bash
env -u PITWALL_API_TOKEN uv run pitwall-api
```
Expected: `API authorization disabled for loopback development`, then `Uvicorn running on http://127.0.0.1:8080`. The terminal blocks; that is normal.
If different: if a different port is printed, use that port below.
Coach: open `http://127.0.0.1:8080/docs` in a browser. Expected: the Swagger page, grouped by route. On the `GET /healthz` row, click **Try it out**, then **Execute**: response code `200` and a body with `"ok": true`.
If different: a `401` means the token is still set in terminal 2; restart it with the `env -u` line above.

### Step 7 — The TUI

Coach: the TUI is a full-screen Textual app. Start it in a third terminal with the same five `export` lines from the README Quick Start, then explore three views. Never pipe `pitwall dashboard`: it needs a real terminal.
Tester does in terminal 3:
```bash
uv run pitwall dashboard
```
Expected: the **Overview** view. Press `p` (Providers), `m` (Models), `?` (help), then `q` to quit.
If different: if `pitwall dashboard` prompts for setup, the `export` lines are missing in this terminal — answer no and stop.
Coach: explain the console trap (rule R2): a terminal without the `export` lines would launch `pitwall setup` instead of the dashboard.

### Step 8 — Dry run

Coach: in the first terminal, run the README Quick Start `curl`. The Quick Start hardcodes the placeholder token, which matches the `PITWALL_API_TOKEN` value from the README's `export` line.
Tester does in terminal 1:
```bash
curl -s -X POST http://127.0.0.1:8080/v1/inference \
  -H 'Authorization: Bearer local-api-token' \
  -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}'
```
Expected: a JSON response that includes `result.dry_run=true` and a `result.plan` with `selected_provider_id` set to the demo provider. The raw form has no space, so pipe through `uv run python -m json.tool` to see `"dry_run": true` spaced.
If different: paste the output and ask the coach. If the API is not running, return to Step 6.
Coach: read [dry-run-and-hermetic](../concepts/dry-run-and-hermetic.md) together.

### Step 9 — Why tier 5 is locked

Coach: the `RUNPOD_API_KEY` placeholder in the README Quick Start makes every paid path fail at authentication. Rule R2 also forbids the other tier-5 commands. No command runs here; the tester just explains why.
Tester does: explains in one sentence why tier 5 is locked.
Expected: tier 5 needs a real key from the maintainer, and the rules forbid running it on the placeholder key.
If different: re-read rule R2 and the tier table in [the missions README](../missions/README.md#the-tiers).

### Step 10 — Stop everything

Coach: clean up so the next session starts fresh.
Tester does in terminal 2: press Ctrl-C to stop the API.
Then in terminal 1:
```bash
make down
docker ps
```
Expected: `Ctrl-C` returns the prompt; `make down` exits cleanly; `docker ps` shows no row for 5444 or 6380.
If different: read the error and ask the coach before stopping anything else.

## Checkpoint

Ask the tester to explain dry run, hermetic, and why tier 5 is locked, in their own words.
Expected answers: a dry run plans and costs the work but does not call a paid provider; hermetic means no real outside services; tier 5 needs a real key, which the maintainer has not given the tester.

## Done when

The checkpoint is passed and every surface from Step 3 is stopped.

## Record in progress

Add a `Completed` row for B5 with today's date and a short note. Set `Current item: B6`.

## Next

[B6 — QA fundamentals and your first issues](B6-qa-fundamentals.md)
