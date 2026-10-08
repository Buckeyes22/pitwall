"""Run every test bound in reviewed-bindings.json and keep one result file per runner.

The journey harness calls this after the journeys: it reads the bindings, runs each
bound node once with the runner and lane its framework needs, and writes the results
where ``tests/release/test_matrix_complete.py`` reads them.

    results/pytest-<lane>.xml    pytest JUnit (xunit1, which records each test's file)
    results/unittest-<file>.json Agent Routing unittest reports (stdlib-only runner)
    collection.json              the pytest collection receipt the matrix requires

Usage: run_bound_tests.py OUTPUT_DIR  (DATABASE_URL and REDIS_URL name the test stack)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# pytest arguments per proof lane: the release conftest runs release tests only under the
# exact marker, and the integration fixtures read the test stack from these variables.
LANE_MARKERS = {"hermetic": [], "release": ["-m", "release"], "integration": ["-m", "integration"]}


def _bindings() -> list[dict[str, str]]:
    payload = json.loads((ROOT / "release_acceptance" / "reviewed-bindings.json").read_text())
    return list(payload["bindings"])


def _run(argv: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> int:
    print("+", " ".join(argv[:6]), "..." if len(argv) > 6 else "", flush=True)
    return subprocess.run(argv, cwd=cwd, env=env, check=False).returncode


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[0]).resolve()
    results = out / "results"
    results.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.setdefault("PITWALL_TEST_DATABASE_URL", env.get("DATABASE_URL", ""))
    env.setdefault("PITWALL_TEST_REDIS_URL", env.get("REDIS_URL", ""))
    env["PITWALL_JOURNEY_HARNESS"] = "1"  # the bound journeys are harness-only

    pytest_nodes: dict[str, set[str]] = defaultdict(set)
    unittest_files: set[str] = set()
    for binding in _bindings():
        node = binding["test_node_id"]
        framework = binding["framework"]
        if framework == "pytest":
            pytest_nodes[binding["proof_lane"]].add(node)
        elif framework == "unittest":
            unittest_files.add(node.split("::", 1)[0])
        else:
            raise SystemExit(f"unsupported framework {framework!r} for {node}")

    failures = 0
    pytest = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:randomly",
        "-o",
        "junit_family=xunit1",
    ]
    for lane, nodes in sorted(pytest_nodes.items()):
        failures += bool(
            _run(
                [
                    *pytest,
                    *LANE_MARKERS[lane],
                    f"--junitxml={results}/pytest-{lane}.xml",
                    *sorted(nodes),
                ],
                env=env,
            )
        )
    files = sorted({node.split("::", 1)[0] for nodes in pytest_nodes.values() for node in nodes})
    collect_env = {**env, "PYTHONPATH": str(ROOT)}
    failures += bool(
        _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:randomly",
                "-p",
                "tools.release_acceptance.pytest_collection",
                "--pitwall-collection-root",
                str(ROOT),
                "--pitwall-collection-output",
                str(out / "collection.json"),
                *files,
            ],
            env=collect_env,
        )
    )

    # Load the report module by path so the unittest run does not import the ``tools`` package.
    runner = (
        "import importlib.util, sys; from pathlib import Path;"
        "spec = importlib.util.spec_from_file_location('unittest_report', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec); sys.modules['unittest_report'] = module;"
        "spec.loader.exec_module(module);"
        "report = module.run_unittest_discovery(Path('.'), start_dir='tests/agents', pattern=sys.argv[2], output_path=Path(sys.argv[3]));"
        "sys.exit(report.exit_code)"
    )
    unittest_env = {**env, "HOME": str(out / "unittest-home")}
    (out / "unittest-home").mkdir()
    for path in sorted(unittest_files):
        name = Path(path).name
        failures += bool(
            _run(
                [
                    str(ROOT / ".venv" / "bin" / "python"),
                    "-c",
                    runner,
                    str(ROOT / "tools" / "release_acceptance" / "unittest_report.py"),
                    name,
                    str(results / f"unittest-{Path(name).stem}.json"),
                ],
                cwd=ROOT,
                env=unittest_env,
            )
        )

    print(f"bound-test runs with failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
