"""J27 in the journey harness: the CI-covered unit lane is skipped, the security lane is not.

Runs the real script with ``JOURNEY_FILTER=J27`` and a stub ``uv`` on PATH that records its
arguments, so no test runs.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.release

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "release" / "run-user-journeys.sh"
UNIT_LANE = "run pytest -q -n auto -m not integration and not slow"
SECURITY_LANE = "run pytest -q -m security and not fuzz tests/security"


def _run_j27(
    tmp_path: Path, unit_lane: str | None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "uv-calls.txt"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text('#!/bin/bash\necho "$*" >>"$UV_CALLS"\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "UV_CALLS": str(calls),
        "TMPDIR": str(tmp_path),
    }
    env.pop("PITWALL_JOURNEYS_UNIT_LANE", None)
    if unit_lane is not None:
        env["PITWALL_JOURNEYS_UNIT_LANE"] = unit_lane
    result = subprocess.run(
        ["bash", str(SCRIPT), "J27"],
        env=env,
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )
    recorded = calls.read_text().splitlines() if calls.exists() else []
    return result, recorded


def test_covered_skips_unit_lane_and_keeps_security_lane(tmp_path: Path) -> None:
    result, calls = _run_j27(tmp_path, "covered")
    assert result.returncode == 0, result.stderr
    assert "README unit lane: covered by the test job" in result.stdout
    assert "PASS  J27 README testing commands" in result.stderr
    assert calls == [SECURITY_LANE]


def test_unset_runs_unit_lane_then_security_lane(tmp_path: Path) -> None:
    result, calls = _run_j27(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert "covered by the test job" not in result.stdout
    assert calls == [UNIT_LANE, SECURITY_LANE]
