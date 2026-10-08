# T1-07 — Troubleshooting guide messages

**Tier:** 1 | **Mode:** training | **Repeatable:** no | **Needs:** T1-04 | **Output:** issues

## Why this matters

An error message that does not match its troubleshooting entry leaves the next user stuck.
[Troubleshooting](../../../docs/operator/troubleshooting.md) is the page a user lands on after
something fails; if the wording there no longer matches what the CLI prints, the user copy-pastes
the wrong text, the doc's fix recipe no longer applies, and the maintainer triages a report that
should have been self-served. This mission re-runs the two entries that can be reproduced
without live RunPod credentials and compares what the CLI actually prints against the doc.

## Sources

- [`docs/operator/troubleshooting.md`](../../../docs/operator/troubleshooting.md) — the
  troubleshooting entries, each headed by the symptom string

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)).

`pitwall status` is on the never-run list except in the exact form in step 1, with input
redirected from `/dev/null` so it can never offer to run `pitwall setup`. The command uses
`.venv/bin/pitwall` instead of `uv run` because `uv` would otherwise try to rebuild its cache
in the empty home; the temporary home guarantees that no saved credential is found. Do not run
this command without all four of those protections.

## Setup

Make sure no leftover environment from a previous mission is leaking through. Open a fresh
terminal and confirm the project venv exists:

```bash
ls .venv/bin/pitwall docs/operator/troubleshooting.md
```

Expected: both paths printed.

## Steps

### Step 1 — Reproduce the missing-RunPod-credential entry

Coach: the first troubleshooting entry is the message a user sees when no RunPod credential is
available anywhere — not in the environment and not in the saved `runpodctl` config. The tester
runs the only form of `pitwall status` that is allowed: input from `/dev/null`, no
`RUNPOD_API_KEY`, no `DATABASE_URL`, and a throwaway `HOME`.

Tester does:

```bash
env -u RUNPOD_API_KEY -u DATABASE_URL HOME="$(mktemp -d)" \
  .venv/bin/pitwall status < /dev/null 2>&1 \
  | tee qa/.work/evidence/t1-07-no-credential.txt
echo "exit=${PIPESTATUS[0]}"
```

Expected: the saved file shows ``no RunPod credential: run `pitwall setup` or export
RUNPOD_API_KEY`` (the variable name appears verbatim in the message), and the next line is
`exit=2`. The exit comes from `_service_or_exit` in `cli_personal.py`, which prints the message
to stderr and raises `SystemExit(2)`. Compare both the message wording and the exit code with
the section headed by that exact phrase in
[troubleshooting.md](../../../docs/operator/troubleshooting.md).

If different: any drift in wording, or an exit other than 2, is a finding. Note the actual
output exactly.

### Step 2 — Reproduce the registry-backed `warm-volume` entry

Coach: the troubleshooting entry for registry-backed `serve`/`warm-volume` names `DATABASE_URL`
and exits 2, with a JSON object carrying `"error": "missing_database_url"`. The plain-text and
`--json` forms must agree.

Tester does:

```bash
env -u DATABASE_URL uv run pitwall warm-volume --model x --volume-id y --dry-run 2>&1 \
  | tee qa/.work/evidence/t1-07-warm-volume.txt
echo "exit=${PIPESTATUS[0]}"
```

Expected: the output names `DATABASE_URL` and says `warm-volume needs DATABASE_URL
(registry-backed launch planning)`, and the next line is `exit=2`.

Then the same call in JSON mode:

```bash
env -u DATABASE_URL uv run pitwall warm-volume --model x --volume-id y --dry-run --json 2>&1 \
  | tee qa/.work/evidence/t1-07-warm-volume-json.txt
echo "exit=${PIPESTATUS[0]}"
```

Expected: the output is a JSON object with the key `"error"` and the string value
`"missing_database_url"`, again with `exit=2`. Compare both runs with the section headed
`Registry-backed `serve`/`warm-volume` exits 2 needing `DATABASE_URL`` in
[troubleshooting.md](../../../docs/operator/troubleshooting.md).

If different: a missing field, a wrong exit code, or a message that does not name
`DATABASE_URL` is a finding.

### Step 3 — Record live-only entries for tier 5

Coach: several troubleshooting entries cannot be reproduced without live RunPod credentials —
serve-failure refusals, orphaned pods, and dead-route cleanup. The tester lists them under
"Coach notes" in `qa/.work/progress.md` as future tier 5 checks, so a live-key holder knows
what to run.

Tester does:

```bash
grep -n '^## ' docs/operator/troubleshooting.md \
  | tee qa/.work/evidence/t1-07-entries.txt
```

Expected: every entry heading is listed. The tester picks the three live-only entries
(serve refusals, orphaned pods, dead routes) and writes their headings into
`qa/.work/progress.md` "Coach notes" with the prefix `T5 future check:`.

If different: a heading that does not exist in the file is a finding itself.

## What counts as a finding

- A reproduced message in step 1 or 2 differs in wording, in exit code, or in field names
  from the matching troubleshooting entry.
- The CLI prints the message but the troubleshooting entry does not exist.
- The troubleshooting entry exists but the CLI no longer produces the message.
- A heading the tester reads in step 3 is missing from the file, or points at content the file
  does not contain.

Every finding is filed through [bug reports](../../handbook/bug-reports.md) with `found-by-qa`
and one severity label. Documentation drift uses `--label documentation`; behavior drift uses
`--label bug`.

## Done when

- Step 1 and step 2 have both been run, both forms (plain and `--json` where applicable), and
  their outputs are saved under `qa/.work/evidence/`.
- The two reproduced entries are compared with the matching troubleshooting sections.
- The three live-only entries are listed under "Coach notes" in `qa/.work/progress.md` as
  tier 5 future checks.

## Record in progress

Add a `Completed` row for T1-07 with links to the two evidence files and to the coach-notes
block. Set `Current item: T2-01` (the first tier 2 mission, unlocked by the maintainer after
T1-01, T1-02, and three filed issues). Write a session log entry that lists the reproduced
entries and the live-only entries recorded for tier 5.
