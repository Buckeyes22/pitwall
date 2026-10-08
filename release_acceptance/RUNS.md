# Release acceptance run recorder

`tools/release_acceptance/run_recorder.py` retains one explicit subprocess run
with enough provenance for later evidence review. It is a run recorder, not an
acceptance evaluator: every record contains `acceptance_status: not_evaluated`,
even when the child exits zero.

The Python API is the primary interface:

```python
from pathlib import Path

from tools.release_acceptance.run_recorder import record_run

record = record_run(
    ["uv", "run", "pytest", "-q", "tests/example.py"],
    cwd=Path("/absolute/checkout"),
    candidate_root=Path("/absolute/checkout"),
    timeout_s=300,
    output_root=Path("$PITWALL_EVIDENCE_ROOT/release-acceptance/run-records"),
    env_allowlist=("PITWALL_TEST_DATABASE_URL",),
    secret_values=(known_value,),
    result_paths={
        "junit": Path("$PITWALL_EVIDENCE_ROOT/release-acceptance/fresh/junit.xml"),
    },
)
```

`argv` must be a list or tuple of arguments; command strings and shell syntax
are rejected. The child always runs with `shell=False`, stdin closed, and a
new process session. A timeout sends `SIGTERM` to that owned process group,
waits the bounded grace period, and then sends `SIGKILL` if needed. The
recorder never signals a process outside the group it created. If the caller
raises `KeyboardInterrupt`, the recorder performs the same bounded owned-process
cleanup, writes a terminal `interrupted` receipt, and then re-raises the
interrupt; the CLI therefore follows the caller's conventional interrupt exit
behavior.

`output_root` must be an absolute private directory outside both `cwd` and
`candidate_root`. Each invocation creates a fresh `run-*` directory with
exclusive creation, so a later run cannot overwrite an earlier run. The
directory contains `run.json`, `candidate-before.json`,
`candidate-after.json`, `stdout.log`, and `stderr.log`. Failed, non-zero, and
timed-out runs remain retained.

`run.json` records UTC start and end times, the explicit argv, cwd, timeout,
exit code, timeout and termination state, hashes and paths for both output
streams, Python and platform metadata, and only explicitly allowlisted
environment variable names. Environment values are never recorded. Candidate
snapshots are produced by the existing `candidate.build_candidate` function.
If the before and after candidate IDs differ, `candidate.status` is `stale`
and `candidate.source_drift` is `true`; an unavailable or unproven identity
(including a gitlink whose component contents were not independently proven)
is `unknown`.

The output record also carries `output.complete` and `output.loss_reason`.
Normal completion and bounded cleanup that closes both streams can be complete;
an escaped descendant, repeated interrupt, or pipe-collection deadline marks
the output incomplete instead of implying that all child output was captured.

Callers can bind structured test output with `result_paths`, a mapping from a
label to an absolute, initially nonexistent destination inside `output_root`.
The initial receipt records each requested path. The final receipt records
`status`, `path`, `sha256`, `size`, and `reason`; only a regular non-symlink
file contained by `output_root` receives `status: retained` and a digest.
Missing, escaped, symlink, non-regular, or unreadable destinations remain
`status: unresolved` and are never read outside the trusted output root.

Credential values are not accepted by the CLI. API callers that provide known
credential values must pass them through `secret_values`; those values are
redacted from captured output and JSON metadata before they are written. This
is a caller declaration boundary and cannot detect undeclared secrets. The
recorder does not print child output or secret values.

The CLI accepts `--cwd`, `--candidate-root`, `--output-root`, `--timeout`,
`--terminate-grace`, `--env-name`, and `--artifact`; the explicit command
follows `--`. It inherits the caller's environment but records only the names
selected with `--env-name`:

```bash
uv run python -m tools.release_acceptance.run_recorder \
  --cwd /absolute/checkout \
  --candidate-root /absolute/checkout \
  --output-root $PITWALL_EVIDENCE_ROOT/release-acceptance/run-records \
  --timeout 300 \
  --env-name PITWALL_TEST_DATABASE_URL \
  -- uv run pytest -q tests/example.py
```

The recorder's exit code reports recorder input errors, timeout, or the child
exit code. A zero child exit code is only execution evidence; a separate
acceptance matrix and its oracle must establish a case result.
