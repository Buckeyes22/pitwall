"""The HuggingFace catalogue check must be opt-in and absent from CI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
LIVE_NAME = "PITWALL_HF_CATALOGUE_CHECK"


def test_live_marker_is_deselected_by_default() -> None:
    env = {key: value for key, value in os.environ.items() if key != LIVE_NAME}
    env["DATABASE_URL"] = ""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/live/test_catalogue_weights_resolve.py",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 5
    assert "no tests collected" in result.stdout


def test_ci_workflows_never_set_the_catalogue_check_variable() -> None:
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        assert LIVE_NAME not in workflow.read_text(), f"{workflow.name} enables a live check"
