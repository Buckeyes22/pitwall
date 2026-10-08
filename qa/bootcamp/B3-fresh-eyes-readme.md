# B3 — Fresh-eyes README test

**Mode:** training | **Needs:** B2 | **Output:** raw findings in the progress file

## Goal

Test the README the way a stranger would, before learning how Pitwall works.

## You will learn

- What fresh-eyes testing is.
- Recording a problem the moment you hit it.
- Tearing down what you started.

## Before you start

Coach: for this lesson, do not explain what Pitwall is or why a step exists. Help only with mechanics (typing, copy and paste, which terminal). Every stumble is data.

Coach: confirm the test stack is not running (rule R13). Tester does:

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
```

Expected: no line containing `5444` or `6380`.
If different: run `make down` in the clone that started it.

## Steps

### Step 1 — A scratch folder

Coach: a scratch clone keeps the working repo clean. The date-named folder is easy to reuse or delete. Tester does:

```bash
mkdir -p ~/qa-scratch/$(date +%Y%m%d) && cd ~/qa-scratch/$(date +%Y%m%d); pwd
```

Expected: the path ends in today's date.
If different: if a `pitwall` folder already exists there, use `~/qa-scratch/$(date +%Y%m%d)-2`.

### Step 2 — Read first

Coach: ask the tester to open the README on GitHub and read it top to bottom, then say what was unclear. Tester does:

```bash
gh repo view Buckeyes22/pitwall --web
```

Expected: the coach records each note word for word under "Findings not yet filed" in the progress file, prefixed `B3 README <section>:`.

### Step 3 — Skip the paid block

Coach: the first Quick Start block (`pitwall serve ...`) needs a real RunPod account; it belongs to tier 5 and is skipped here. Ask the tester whether the README made that clear to a first-time reader, and record the answer as a raw finding.
Tester does: no command.
Expected: a note in "Findings not yet filed" with the README section and the tester's answer.

### Step 4 — Follow the registry path exactly

Coach: one command at a time, as the README writes them. The five `export` lines come from the README Quick Start; the tester copies them as-is. Tester does:

```bash
git clone https://github.com/Buckeyes22/pitwall.git
cd pitwall
uv sync --frozen --extra dev
docker compose -f docker-compose.testinfra.yml up -d --wait
# copy the five export lines from the README Quick Start
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

Expected: each finishes without an error, and `init` ends by printing a smoke-test command. For every surprise, record the README section, the exact command, the output, and what the tester expected.

### Step 5 — The second terminal

Coach: the README says to start the API in a second terminal. The API blocks its terminal, so it gets its own code block. If anything goes wrong, record exactly what happened before giving any hint; only then use the hint ladder.

In terminal 2, after `cd` to the scratch clone, the tester runs:

```bash
uv run pitwall-api
```

Expected: log lines showing the server on `127.0.0.1:8080`.
If different: record exactly what happened before giving any hint.

### Step 6 — The smoke call

Coach: in the first terminal (still in the scratch clone), run the README `curl` or the one `init` printed. The API returns compact JSON; raw `curl` output shows `"dry_run":true` with no space. Tester does:

```bash
curl -s -X POST http://127.0.0.1:8080/v1/inference \
  -H 'Authorization: Bearer local-api-token' \
  -H 'Content-Type: application/json' \
  -d '{"capability":"embedding.demo","texts":["hello"],"dry_run":true}'
```

Expected: JSON containing `"dry_run":true`. Compare the response with the README's description and record any differences.

### Step 7 — Useful onboarding commands

Coach: these are the three commands from the README's "Useful onboarding commands" heading. Each must exit `0`. Tester does:

```bash
uv run pitwall create-capability --name embedding.demo --class embedding --cost-mode per_second; echo "exit=$?"
uv run pitwall seed seed/capabilities.yaml seed/providers.yaml --mark-healthy; echo "exit=$?"
uv run pitwall set-provider-health prov_demo_runpod_lb healthy; echo "exit=$?"
```

Expected: `exit=0` each time.

### Step 8 — Tear down

Coach: stop what you started. The scratch clone can stay; `rm -rf` on it later is allowed (rule R2).

In terminal 2: Ctrl-C the API.

In terminal 1, the tester runs:

```bash
docker compose -f docker-compose.testinfra.yml down
docker ps
```

Expected: no `5444` or `6380`.

### Step 9 — Debrief

Coach: explain in three sentences what the tester just did: a local dry run of Pitwall's routing with fake settings. Then review each raw finding for its section, command, output, and expectation.
Tester does: answers the coach's questions.
Expected: every raw finding has a section, command, output, and expectation.

## Checkpoint

Ask: "Why did we do this before learning how Pitwall works?"
Expected: fresh eyes see gaps that experts no longer notice.

## Done when

The README was attempted end to end, or up to where it broke, and every stumble is recorded with its section, command, output, and expectation.

## Record in progress

Add a `Completed` row for B3 with the count of raw findings. Set `Current item: B4`. Write a short session log entry.

## Next

[B4 — Git and GitHub](B4-git-and-github.md)
