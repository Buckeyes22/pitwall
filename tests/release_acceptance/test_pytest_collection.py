"""Hermetic subprocess checks for the source-only pytest collection receipt."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN = "tools.release_acceptance.pytest_collection"


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "candidate"
    source = root / "tests/test_parameterized.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_parameterized(value):\n"
        "    raise AssertionError('body must not run')\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    return root, source, evidence


def _run(root: Path, evidence: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    output = evidence / "receipt.json"
    env = {"PATH": os.environ["PATH"], "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            # Pin the rootdir: pytest otherwise roots it at the common ancestor of the cwd (the
            # checkout) and the candidate, which is /tmp when the checkout itself lives under /tmp.
            "--rootdir",
            str(root),
            "-p",
            PLUGIN,
            "--pitwall-collection-root",
            str(root),
            "--pitwall-collection-output",
            str(output),
            *extra,
            str(root / "tests/test_parameterized.py"),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_real_pytest_collect_receipt_records_exact_parameterized_items(tmp_path: Path) -> None:
    root, source, evidence = _fixture(tmp_path)
    result = _run(root, evidence)

    assert result.returncode == 0, result.stderr
    report = json.loads((evidence / "receipt.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "release-acceptance-pytest-collection.v1"
    assert report["collect_only"] is True
    assert report["exitstatus"] == 0
    assert report["collection_failures"] == []
    assert [row["collected_id"] for row in report["items"]] == [
        "tests/test_parameterized.py::test_parameterized[1]",
        "tests/test_parameterized.py::test_parameterized[2]",
    ]
    assert all(row["parameterized"] is True for row in report["items"])
    assert {row["source_sha256"] for row in report["items"]} == {
        hashlib.sha256(source.read_bytes()).hexdigest()
    }
    assert (evidence / "receipt.json").stat().st_mode & 0o777 == 0o600


def test_collection_failure_is_recorded_without_raw_longrepr(tmp_path: Path) -> None:
    root, _, evidence = _fixture(tmp_path)
    broken = root / "tests/test_broken.py"
    broken.write_text("def test_broken(:\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            # Pin the rootdir: pytest otherwise roots it at the common ancestor of the cwd (the
            # checkout) and the candidate, which is /tmp when the checkout itself lives under /tmp.
            "--rootdir",
            str(root),
            "-p",
            PLUGIN,
            "--pitwall-collection-root",
            str(root),
            "--pitwall-collection-output",
            str(evidence / "broken.json"),
            str(broken),
        ],
        cwd=REPO_ROOT,
        env={"PATH": os.environ["PATH"], "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    report = json.loads((evidence / "broken.json").read_text(encoding="utf-8"))
    assert report["status"] == "unresolved"
    assert report["exitstatus"] != 0
    assert report["collection_failures"] == [
        {"nodeid": "tests/test_broken.py", "outcome": "failed"}
    ]
    assert "longrepr" not in json.dumps(report)


def test_output_inside_candidate_and_preexisting_output_are_refused(tmp_path: Path) -> None:
    root, _, evidence = _fixture(tmp_path)
    inside = root / "receipt.json"
    result = _run(root, evidence, "--pitwall-collection-output", str(inside))
    assert result.returncode != 0
    assert not inside.exists()

    existing = evidence / "receipt.json"
    existing.write_text("sentinel", encoding="utf-8")
    result = _run(root, evidence)
    assert result.returncode != 0
    assert existing.read_text(encoding="utf-8") == "sentinel"
