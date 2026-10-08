"""Runs the Node built-in test suites for the Pi extensions."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SUITE_DIRECTORY = Path(__file__).resolve().parent
SUITES = sorted(SUITE_DIRECTORY.glob("*.test.mjs"))


def failed_blocks(tap: str) -> str:
    """Return every ``not ok`` TAP block (name and YAML error detail) from node's output."""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in tap.splitlines():
        if current is None:
            if line.lstrip().startswith("not ok"):
                current = [line]
        else:
            current.append(line)
            if line.strip() == "...":
                blocks.append("\n".join(current))
                current = None
    if current is not None:
        blocks.append("\n".join(current))
    return "\n".join(blocks)


@pytest.mark.parity
def test_extension_suites_pass() -> None:
    """Ports provider-extension.test.ts, native-extension.test.ts and comparison-lifecycle-observer.test.ts.

    The suites run under ``node --test`` against the committed ``.js`` modules. The two suites that
    exercise the Pi packages skip themselves with a reason when those packages are not installed
    (set ``PITWALL_PI_MODULES`` to a ``node_modules`` directory that contains ``@earendil-works``).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the Pi extension suites need Node 22 or newer")
    assert {suite.name for suite in SUITES} == {
        "comparison-lifecycle-observer.test.mjs",
        "native-extension.test.mjs",
        "provider-extension.test.mjs",
    }
    completed = subprocess.run(  # noqa: S603  # reason: fixed argv, node resolved from PATH, no shell
        [node, "--test", "--test-timeout=120000", *map(str, SUITES)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
        timeout=600,
    )
    tail = "\n".join(completed.stdout.splitlines()[-40:] + completed.stderr.splitlines()[-10:])
    failures = failed_blocks(completed.stdout)
    assert completed.returncode == 0, f"node --test failed:\n{failures}\n--- tail ---\n{tail}"
    assert "# fail 0" in completed.stdout, tail
    assert "# cancelled 0" in completed.stdout, tail
