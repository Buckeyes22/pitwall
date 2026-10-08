"""The real J09 shell check must reject early dashboard exits."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.release


@pytest.mark.parametrize(
    ("status", "transcript", "passed"),
    [
        (0, "early exit", False),
        (1, "startup error", False),
        (124, "dashboard rendered", True),
        (124, "", False),
        (124, "Traceback: failure", False),
    ],
)
def test_j09_requires_survival_and_terminal_output(
    tmp_path: Path,
    status: int,
    transcript: str,
    passed: bool,
) -> None:
    source = (Path(__file__).parents[2] / "scripts/release/run-user-journeys.sh").read_text()
    function = source.split("j09() {", 1)[1].split("\n}\n", 1)[0]
    shell = (
        """
script() { printf '%s' "$BOOT_TEXT" > "${@: -1}"; return "$BOOT_STATUS"; }
journey_pass() { printf 'PASS\\n'; }
journey_fail() { printf 'FAIL\\n'; }
"""
        + "\nj09() {"
        + function
        + "\n}\nj09\n"
    )
    result = subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "ARTIFACTS": str(tmp_path),
            "BOOT_STATUS": str(status),
            "BOOT_TEXT": transcript,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert ("PASS" in result.stdout) is passed, result
    assert ("FAIL" in result.stdout) is not passed, result
