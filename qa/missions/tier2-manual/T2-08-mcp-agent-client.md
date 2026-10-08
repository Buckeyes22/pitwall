# T2-08 — Pitwall as an agent tool (MCP)

**Tier:** 2 | **Mode:** training | **Repeatable:** no | **Needs:** T2-02 | **Output:** issues

## Why this matters

AI agents call Pitwall through MCP (Model Context Protocol). The tester's own agent becomes the
test client, so a broken tool schema, a silent network transport, or a dry-run that spends money
are all findings.

## Sources

- [User journeys J10, J11 — MCP over stdio, MCP fails closed on network](../../../docs/operator/user-journey-catalog.md)
- [MCP server SDLC](../../../docs/sdlc/03-mcp-server.md)
- [MCP and agent clients](../../concepts/mcp-and-agent-clients.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Use Claude
Code for this mission (switch agents as the [agent guide](../../coach/agent-guide.md) describes).
The wrapper script lives only in `qa/.work/`. Every dry-run must return `dry_run: true` without
writing anywhere; a request that would spend money without the README placeholder key is
forbidden.

## Setup

Run the tier 2 standard setup. Check the test stack is free, start it, set the five `export`
lines from the README Quick Start in every terminal, and run migrations plus the seed loader.
Do not start the API for this mission.

```bash
docker ps --format '{{.Names}}  {{.Ports}}'
docker compose -f docker-compose.testinfra.yml up -d --wait
uv run pitwall db migrate
uv run pitwall init --non-interactive
```

Expected: no other clone's Postgres on `5444` or Redis on `6380`; both compose services started;
both `uv run` commands finished without errors.

## Steps

### Step 1 — Build a wrapper script

Coach: the wrapper script sets the five README `export` lines for every MCP call. Paste them
from the README Quick Start into the script; do not paste them in this mission file.
Tester does, in an editor:

```bash
mkdir -p qa/.work
cat > qa/.work/pitwall-broker-mcp.sh <<'WRAPPER'
#!/usr/bin/env bash
cd "$(dirname "$0")/../.."
# paste the five README Quick Start export lines here, exactly as the README shows them
exec uv run pitwall mcp serve broker
WRAPPER
chmod +x qa/.work/pitwall-broker-mcp.sh
ls -l qa/.work/pitwall-broker-mcp.sh | tee qa/.work/evidence/T2-08-wrapper.txt
```

Expected: the `ls` line shows `x` permission bits set for the owner, group, and other.

### Step 2 — Register the MCP server

Tester does:

```bash
claude mcp add --scope local pitwall-local -- "$PWD/qa/.work/pitwall-broker-mcp.sh"
claude mcp list 2>&1 | tee qa/.work/evidence/T2-08-mcp-list.txt
```

Expected: `pitwall-local` is listed as connected.

### Step 3 — Resume Claude Code at the repo root

Tester does: exit Claude Code, start it again at the repository root, and type the kickoff
sentence.
Expected: the coach resumes from the progress file.

### Step 4 — List capabilities

Tester does: ask Claude Code, "Using the pitwall-local MCP server, list the capabilities."
Expected: the agent calls `pitwall_list_capabilities` and shows `embedding.demo` in the response.

### Step 5 — Dry-run inference

Tester does: ask Claude Code, "Using pitwall-local, submit a dry-run inference for
embedding.demo with the text hello."
Expected: the result contains `dry_run` equal to `true`. No workload is created in the database.

### Step 6 — Probe failures

Tester does: ask Claude Code for a capability that does not exist, then ask for a non-dry-run
request.
Expected: `capability_not_found` for the unknown capability. The non-dry-run request passes the
same gates as the REST API and reaches the demo provider, whose fake endpoint and key make the
call fail: the agent reports a `no_providers_available` error, and nothing is billed. Anything
that looks like a successful paid call is severity 1.

### Step 7 — Confirm stdio is the only transport

Coach: MCP must fail closed on every non-stdio transport (J11).
Tester does:

```bash
PITWALL_MCP_TRANSPORT=sse uv run pitwall mcp serve broker; echo "exit=$?" \
  | tee qa/.work/evidence/T2-08-transport-sse.txt
```

Expected: an immediate `exit=2` and the error `network MCP transports are unavailable in the public
alpha` ([MCP server](../../../docs/sdlc/03-mcp-server.md)).
No network listener is opened.

### Step 8 — Keep or remove the registration

Coach: the wrapper lets the QA smoke set use MCP directly. Keep it registered or remove it.
Tester does, one of:

```bash
# keep
claude mcp list | tee qa/.work/evidence/T2-08-keep.txt
# or remove
claude mcp remove --scope local pitwall-local
claude mcp list | tee qa/.work/evidence/T2-08-remove.txt
```

Record which path was used in `qa/.work/notes/T2-08-decisions.txt` and in the progress file.

## What counts as a finding

- A status code or error envelope that differs from the [MCP server failure modes](../../../docs/sdlc/03-mcp-server.md#6-failure-modes--error-types).
- Any `500` (severity 2).
- A non-stdio transport that opens a listener or accepts a connection (severity 1).
- A dry-run that creates a real workload row or spends money (severity 1).
- A tool that bypasses the budget gate or the kill switch (severity 1).

## Done when

- All eight steps have evidence in `qa/.work/evidence/T2-08-*.txt` and notes in
  `qa/.work/notes/T2-08-*.txt`.
- `pitwall-local` is either registered (keep) or removed (remove) and the choice is recorded.
- Any disagreement with the [MCP server SDLC](../../../docs/sdlc/03-mcp-server.md) is filed with
  the tester using [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-08 with output links to the evidence and notes files and any issue
numbers filed. Set `Current item` to
[T2-09 — Background services](T2-09-background-services.md). End the session log with what the
tester learned and any open questions.
