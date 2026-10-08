# Structured test-result adapters

This acceptance unit records component test results. The evidence validator
consumes them through `unittest-json` evidence entries with
an explicit `project_prefix`. The adapter code is standard-library-only and reads
unittest callback data; it does not execute Docker, providers, or
routing workflows.

## unittest contract

`tools.release_acceptance.unittest_report.run_unittest_discovery` runs the
stdlib loader in a specified component root:

```python
report = run_unittest_discovery(
    component_root,
    start_dir="tests",
    pattern="test*.py",
    top_level_dir=".",
    output_path=Path("unittest-report.json"),
)
```

The report records the exact unittest IDs, component-relative source files and
SHA-256 hashes, equivalent command tuple, exit code, status counts, subtest outcomes,
import errors, expected failures (`xfailed`), and unexpected successes. Source
files are obtained from loaded test classes; an import error has no fabricated
source path. Discovery isolates the component's package modules while running,
then restores the caller's module and import-path state. No suffix-only name
mapping or runner-text interpretation is used.

The wrapper leaves the standard unittest command exit semantics intact:
failures and errors produce exit code 1; skips, expected failures, and
unexpected successes remain explicit result statuses and are never rewritten
as passes. Its retained report rows keep full dotted unittest ids such as
`tests.test_cases.Cases.test_pass`; `match_unittest_report` accepts the
repository `test_index` form
`components/sample/tests/test_cases.py::Cases::test_pass` and expands that
class/method tail only when the source module prefix matches exactly. The
reader validates report hashes, relative source metadata, row uniqueness, and
declared counts without opening the recorded component root or source paths;
candidate/run provenance is responsible for comparing those hashes with a
trusted checkout.

## Focused validation

The owned tests cover explicit project prefixes, cross-component suffix
collisions, and missing results. Real temporary unittest fixtures cover passing,
subtest failure, import error, skip, expected failure, unexpected success,
source hashes, JSON output, and same-suffix test IDs in separate modules.
The same fixtures also cover duplicate rows, offline report reading, and
non-passing requested statuses.

Validation command:

```text
~/git/pitwall-release-acceptance/.venv/bin/pytest -q tests/release_acceptance/test_result_adapters.py
26 passed
```

Ruff check, Ruff format check, and Python compileall pass for the four owned
implementation/test files. The parent acceptance integration should import
`read_unittest_report`, `match_unittest_report`, and
`run_unittest_discovery` and convert
`ResultAdapterError`/report statuses into its existing evidence errors; this
unit does not modify `tools/release_acceptance/evidence.py`.
