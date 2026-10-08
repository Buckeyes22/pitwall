from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
LIVE_NAMES = {
    "PITWALL_SELFHOSTED_BASE_URL",
    "PITWALL_SELFHOSTED_API_KEY_ENV",
}


def test_live_marker_is_deselected_by_default() -> None:
    env = {key: value for key, value in os.environ.items() if key not in LIVE_NAMES}
    env["DATABASE_URL"] = ""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/live/test_selfhosted_endpoint.py",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 5
    assert "no tests collected" in result.stdout


def test_ci_workflows_never_set_live_endpoint_variables() -> None:
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        for name in LIVE_NAMES:
            assert name not in text, f"{workflow} sets forbidden live variable {name}"
