# T2-11 — Agent Routing component checks

**Tier:** 2 | **Mode:** work | **Repeatable:** no | **Needs:** T2-02 | **Output:** issues

## Why this matters

Agent Routing (`pitwall agents`) is part of the Pitwall package that the tester's own agents can use.
It has its own test suite and a `doctor` health check that probes the installed harnesses. A broken test run, a doctor that
flips `fail` to a positive number, or a `--help` that no longer lists the documented subcommands
are all findings.

## Sources

- [Agent Routing CONTRIBUTING](../../../docs/agents/CONTRIBUTING.md#local-checks)
- [Doctor](../../../docs/agents/doctor.md)
- [Agent Routing README](../../../docs/agents/routing-readme.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)). Run `doctor`
only with a temporary home, so it cannot touch the tester's real agent configuration. Harness
CLIs that it probes may create first-run files in that temporary home; that is expected. Do not
start the Pitwall test stack or any service for this mission.

## Setup

No test stack. Stay in the repository root; Agent Routing uses the same environment as the rest of
Pitwall.

```bash
uv sync --frozen --extra dev --python 3.14.7 2>&1 | tail -3
```

Expected: the `tail` line shows no error from `uv sync`.

## Steps

### Step 1 — Run the component test suite

Coach: the suite is a `pytest` run of `tests/agents`. It can take several minutes; the tail captures the
final line.
Tester does:

```bash
uv run pytest tests/agents -q -p no:randomly -p no:cov 2>&1 \
  | tee qa/.work/evidence/T2-11-tests.txt | tail -4
```

Expected: a final line such as `<n> passed, <m> skipped in <t>s` with no `failed`. Skipped tests are fine.
If different: any `FAILED` line, or a final line that mentions `failed` or `error`, is a finding.

### Step 2 — Run `doctor` against a temporary home

Coach: `HOME="$(mktemp -d)"` keeps `doctor` from reading or writing the tester's real
configuration. The command prints one line per check, then a final `doctor:` summary.
Tester does:

```bash
HOME="$(mktemp -d)" uv run pitwall agents doctor 2>&1 \
  | tee qa/.work/evidence/T2-11-doctor.txt | tail -3
echo "exit=${PIPESTATUS[0]}" | tee -a qa/.work/evidence/T2-11-doctor.txt
```

Expected: a last line that starts `doctor:` with counts where `'fail': 0`, and `exit=0`.
Compare the check groups (for example `runtime`, `provider`, `plugin`, `security`, `routes`) with
the category list in [Doctor](../../../docs/agents/doctor.md). A missing
documented group, or a check that flips a documented `PASS` to `FAIL`, is a finding.

### Step 3 — Compare `--help` against the component README

Tester does:

```bash
uv run pitwall agents --help 2>&1 \
  | tee qa/.work/evidence/T2-11-help.txt
```

Expected: usage text listing the `pitwall agents` subcommands. The README's day-to-day section names
`doctor`, `runs`, `profiles`, `setup`, `harnesses`, `workflow`, and `dispatch`; every one of those
must appear in the help output.
If different: a missing subcommand, or an undocumented one that the help output adds, is a
finding.

## What counts as a finding

- A `FAILED` line in the test suite, or a final line that mentions `failed` or `error`.
- A `doctor:` summary with `'fail'` greater than `0`, or `exit` not equal to `0`.
- A `doctor` check group documented in `docs/agents/doctor.md` that is missing from the output.
- A `doctor` check that flips a documented `PASS` to `WARN` or `FAIL` (severity 3).
- A top-level subcommand from the README that is missing from `--help`.
- An undocumented top-level subcommand in `--help` (severity 3, label `documentation`).

## Done when

- `qa/.work/evidence/T2-11-tests.txt` ends with a `<n> passed` line and no `failed`.
- `qa/.work/evidence/T2-11-doctor.txt` ends with `doctor: ... 'fail': 0` and `exit=0`.
- `qa/.work/evidence/T2-11-help.txt` matches the README's documented subcommands.
- Every drift from the docs is filed with the tester using [bug reports](../../handbook/bug-reports.md).

## Record in progress

Add a `Completed` row for T2-11 with output links to the three evidence files and any issue
numbers filed. Set `Current item` to
[T2-12 — Exploratory session](T2-12-exploratory-session.md). End the session log with what the
tester learned and any open questions.
