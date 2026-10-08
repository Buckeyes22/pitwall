"""The release matrix gate: every discovered surface is bound to a test that ran and passed.

The journey harness runs ``scripts/release/run_bound_tests.py`` after the journeys and then
this test with ``PITWALL_JOURNEY_RESULTS`` naming that script's output. The matrix is
assembled from the tree with the run's pytest collection receipt. The gate holds when:

* no binding is stale, malformed, unproven, or unmatched, and every surface has at least
  one validated case (the matrix's ``unreviewed-surface`` and binding gaps are empty);
* every node every case names passed in the run's JUnit or unittest results.

The matrix keeps four standing notices open by design, and the gate does not treat them
as failures: each discovery issue (its review is enforced by ``test_discovery_review``),
each binding's "journey coverage unreviewed" disclaimer, the static test index's notes
about files no binding uses, and declaration selectors that are hints only.
"""

from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from tools.release_acceptance import matrix

pytestmark = [pytest.mark.release, pytest.mark.journey_harness]

ROOT = Path(__file__).resolve().parents[2]
STANDING = {
    "discovery-issue",
    "journey-coverage-unreviewed",
    "test-index-issue",
    "unmatched-declaration-selector",
    "candidate-issue",
}


def _pytest_passes(path: Path) -> set[str]:
    passed = set()
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        if any(child.tag in {"failure", "error", "skipped"} for child in case):
            continue
        file = case.attrib["file"]
        module = file.removesuffix(".py").replace("/", ".")
        classname = case.attrib["classname"]
        cls = classname[len(module) + 1 :] if classname.startswith(module + ".") else ""
        passed.add(
            "::".join(part for part in (file, cls.replace(".", "::"), case.attrib["name"]) if part)
        )
    return passed


def _unittest_passes(path: Path) -> set[str]:
    passed = set()
    for test in json.loads(path.read_text())["tests"]:
        if test["status"] != "pass" or not test["source_file"]:
            continue
        cls, method = test["test_id"].rsplit(".", 2)[-2:]
        passed.add(f"{test['source_file']}::{cls}::{method}")
    return passed


def test_every_surface_is_bound_to_a_test_that_passed() -> None:
    results_dir = os.environ.get("PITWALL_JOURNEY_RESULTS")
    assert results_dir, "run through scripts/release/run-user-journeys.sh (PITWALL_JOURNEY_RESULTS)"
    run = Path(results_dir)
    passed: set[str] = set()
    for path in sorted((run / "results").iterdir()):
        if path.name.startswith("pytest-"):
            passed |= _pytest_passes(path)
        elif path.name.startswith("unittest-"):
            passed |= _unittest_passes(path)

    out = Path(tempfile.mkdtemp(prefix="pitwall-matrix-gate-")) / "matrix"
    matrix.assemble(ROOT, output_dir=out, collection_report_path=run / "collection.json")
    gaps = json.loads((out / "gap-ledger.json").read_text())["gaps"]
    blocking = [gap for gap in gaps if gap["kind"] not in STANDING]
    assert blocking == [], blocking[:20]

    records = [json.loads(line) for line in (out / "acceptance.v1.jsonl").read_text().splitlines()]
    surfaces = {r["surface"]["surface_id"] for r in records if r["record_type"] == "surface"}
    cases = [r["case"] for r in records if r["record_type"] == "case"]
    assert surfaces and surfaces == {case["surface_id"] for case in cases}
    not_passed = sorted({node for case in cases for node in case["test_node_ids"]} - passed)
    assert not_passed == [], not_passed[:20]
    print(f"matrix: {len(surfaces)} surfaces, {len(cases)} bound cases, 0 unbound, 0 failing")
