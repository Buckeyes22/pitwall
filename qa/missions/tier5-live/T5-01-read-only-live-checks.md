# T5-01 — Read-only live checks

**Tier:** 5 | **Mode:** training | **Repeatable:** yes | **Needs:** T5-00 | **Output:** issues

## Why this matters

Read-only calls prove the key works without spending anything. A catalogue read tells the tester
the key authenticates, the account balance is what the maintainer said it is, and the catalogue
fields match the [RunPod market doc](../../../docs/operator/runpod-market.md). A wrong field
name, a missing balance line, or a refusal on a read-only call is the cheapest way to find a
provider integration bug before any GPU is involved.

## Sources

- [RunPod catalogue, balance, and billing reads](../../../docs/operator/runpod-market.md) — the
  shared `RunpodMarketRead` contract, the four value and support label rows
- [Live testing safety](../../handbook/live-testing-safety.md) — the four safety layers from
  T5-00, still in force
- [Before every live step](../../handbook/live-testing-safety.md#before-every-live-step) —
  status then catalogue before any paid step

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Tier 5 is
always training mode (rule R5): the tester types every command. The key is loaded into this
terminal only, with `set -a; . ~/.config/pitwall-qa/runpod.env; set +a`; the file content is
never echoed. The catalogue read is read-only; nothing is launched, nothing is billed.

## Setup

The key file from T5-00 exists at `~/.config/pitwall-qa/runpod.env` with mode `0600`. Confirm
nothing is running, then load the key into this terminal only. The five `export` lines from the
README Quick Start are not used here; tier 5 uses the maintainer's key, not the placeholders.

```bash
ls -l ~/.config/pitwall-qa/runpod.env
set -a; . ~/.config/pitwall-qa/runpod.env; set +a
env | grep -c '^RUNPOD_API_KEY'
```

Expected: the first command prints mode `-rw-------`; the `set` block loads without error; the
last command prints `1` (the key is exported in this terminal).

## Steps

### Step 1 — Run a catalogue read and inspect the four tables

Coach: the human-friendly rendering is four tables: GPUs, datacenters, balance, and billing
categories. The tester should be able to read each table and point at the rows the
[runpod-market doc](../../../docs/operator/runpod-market.md#value-and-support-labels) lists in
its "Value and support labels" table.

Tester does:

```bash
uv run pitwall runpod catalogue | tee qa/.work/evidence/T5-01-catalogue.txt | head -40
```

Expected: a header line `RunPod catalogue: <state> | age <N>s | refreshed`; four tables in order (`RunPod GPUs`, `RunPod Datacenters`, a `RunPod credit` panel or a warning that
balance is unavailable, and `RunPod Billing Categories`).
If different: a missing table or a balance warning when the maintainer said the balance is set
is a finding.

### Step 2 — Run the same read as machine-readable JSON

Coach: the JSON shape is what other surfaces (REST `/v1/runpod/catalogue`, MCP
`pitwall_runpod_catalogue`, TUI panel) share. Comparing field names across surfaces starts here.

Tester does:

```bash
uv run pitwall runpod catalogue --json | uv run python -m json.tool | head -40
```

Expected: a JSON object with at least the keys `state`, `age_seconds`, `cache_hit`, `gpus`,
`datacenters`, and `balance`. The fields match the
[operator surfaces section](../../../docs/operator/runpod-market.md#operator-surfaces) of the
doc.
If different: a missing key, or a renamed key, is a finding that affects every other surface.

### Step 3 — Check the cache claim with `--refresh`

Coach: the [cache and refresh section](../../../docs/operator/runpod-market.md#cache-and-refresh)
says the cache lives inside one process. Each CLI run is a new process, so every CLI read is
fresh, with or without `--refresh`. The long-running API and MCP servers are where the cache
shows up.

Tester does:

```bash
uv run pitwall runpod catalogue --refresh | tee qa/.work/evidence/T5-01-refresh.txt | head -40
```

Expected: the header ends in `refreshed`, and the same GPU types and datacenters appear as in
step 1. Prices and stock may move between reads.
If different: a CLI header that says `cache hit` contradicts the doc and is a finding. A refusal
on the key is severity 1: stop and tell the maintainer.

## What counts as a finding

- A missing `RunPod GPUs`, `RunPod Datacenters`, balance panel, or `RunPod Billing Categories`
  table.
- A field name in the JSON output that the
  [runpod-market doc](../../../docs/operator/runpod-market.md) does not list.
- A `--refresh` read that fails, or a CLI header that says `cache hit`.
- A balance that is missing when the maintainer said one is set, or a balance that differs from
  the agreed ceiling in `qa/.work/notes/tier5-ceilings.md`.
- Any refusal or auth error on a read-only call (severity 1).

## Done when

- Steps 1, 2, and 3 each produced saved output under `qa/.work/evidence/T5-01-*.txt`.
- The four tables in step 1 are noted in the progress file under `Coach notes`.
- The JSON keys from step 2 are listed in `qa/.work/notes/`.
- Step 3's read listed the same GPU types and datacenters as step 1.
- Every mismatch is filed through [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T5-01 with the three evidence file links and any issue numbers.
Set `Current item` to
[T5-02 — Personal serving smoke](T5-02-personal-serving-smoke.md). End the session log with the
agreed balance, the field names the tester wants to remember, and any open questions for the
maintainer.
