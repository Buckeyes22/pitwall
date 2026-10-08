# T1-01 — CONTRIBUTING walkthrough

**Tier:** 1 | **Mode:** training | **Repeatable:** no | **Needs:** B6 | **Output:** issues

## Why this matters

CONTRIBUTING is the first doc a new contributor follows. A wrong command there fails people
on day one. Reading it end to end against the real repo catches the wrong commands before
they reach a newcomer.

## Sources

- [Dev Environment](../../../CONTRIBUTING.md#dev-environment)
- [Running Tests](../../../CONTRIBUTING.md#running-tests)
- [Quality Gates](../../../CONTRIBUTING.md#quality-gates)
- [PR Process](../../../CONTRIBUTING.md#pr-process)
- [Coach safety](../../coach/rules.md#safety)
- [Bug reports](../../handbook/bug-reports.md)

## Safety

Coach rules R1–R3, R8, and R13 apply ([coach rules](../../coach/rules.md#safety)).
Run `make test-int` only against the local test stack on ports 5444 and 6380. Never push
to `main` (rule R2). Use only the placeholder `RUNPOD_API_KEY` value from the README Quick Start.

## Setup

A working clone on `main` after `git pull`. `docker ps` shows no container bound to 5444
or 6380 (rule R13). The five `export` lines from the README Quick Start are not required
for this mission; nothing here spends money.

## Steps

### Step 1 — Sync dependencies and confirm Python version

Coach: ask the tester to run the first commands from CONTRIBUTING's Dev Environment section.

Tester does:

```bash
uv sync --frozen --extra dev
uv run python --version
```

Expected: the first command finishes with no error; the second prints `Python 3.14.7`
(the repository pin).

Note in the progress file whether CONTRIBUTING's clone line `git clone
https://github.com/your-fork/pitwall.git` makes clear that `your-fork` is a placeholder for
the tester's own fork. If it does not, file a `documentation` finding.

### Step 2 — Run the hermetic test target

Tester does:

```bash
make test 2>&1 | tee qa/.work/evidence/T1-01-make-test.txt | tail -15
echo "exit=${PIPESTATUS[0]}"
```

Expected: a pytest summary line containing `passed` and no `failed`; `exit=0`. The run
can take several minutes.

### Step 3 — Start the local test stack and run integration tests

Tester does:

```bash
docker ps
make up
docker compose -f docker-compose.testinfra.yml ps
```

Expected: the first command shows no row for 5444 or 6380; `make up` exits cleanly;
the `ps` command lists the `postgres` and `redis` service containers.

Record whether CONTRIBUTING's phrase "wait a moment for the databases to be ready" tells
a newcomer how to know the databases are actually ready. If it does not, file a finding.

```bash
make test-int 2>&1 | tee qa/.work/evidence/T1-01-test-int.txt | tail -15
echo "exit=${PIPESTATUS[0]}"
```

Expected: a pytest summary line containing `passed` and no `failed`; `exit=0`.

### Step 4 — Stop the local test stack

Tester does:

```bash
make down
docker ps
```

Expected: `make down` exits cleanly; the `docker ps` shows no row for 5444 or 6380.

### Step 5 — Check the Quality Gates table

Coach: walk every row of CONTRIBUTING's Quality Gates table and confirm each named
target exists in the Makefile.

Tester does:

```bash
grep -E '^(test|test-int|test-cov|sec|sec-semgrep|sec-test|sec-fuzz|mutation-gate|load-smoke|openapi-check|ci-tools):' Makefile
```

Expected: every make target named in the Quality Gates table appears in the output. A
missing target is a finding.

```bash
make sec-test 2>&1 | tail -5
```

Expected: a pytest summary line containing `passed` and no `failed`.

### Step 6 — Compare PR Process with the pull request template

Tester does:

```bash
cat .github/pull_request_template.md
```

Expected: the template's `Testing` block and checklist use the same commands CONTRIBUTING
asks for: `make test`, and `make up && make test-int && make down`. If they diverge, file a
`documentation` finding with both quoted lines.

## What counts as a finding

- A command that fails as written on a clean clone.
- A missing prerequisite for one of the steps.
- A vague instruction that does not tell a newcomer how to know the step worked.
- Output that contradicts what CONTRIBUTING or the Makefile promises.
- A Quality Gates row that points at a make target that does not exist.
- A difference between the PR Process commands and the pull request template commands.

Every finding is filed through [bug reports](../../handbook/bug-reports.md) with
`found-by-qa` and a severity label.

## Done when

Steps 1 through 6 are run with evidence saved under `qa/.work/evidence/T1-01-*.txt`, and
every mismatch between the doc and the repo is filed as an issue.

## Record in progress

Add a `Completed` row with links to the evidence files and to every filed issue. Set
`Current item: T1-02`. Write a short session log entry.
