"""Dynamic contract acceptance, translated from packages/pi-workbench/tests/dynamic-contract-acceptance.test.ts.

Every case exercises a packaged Pi extension (lifecycle observer, native extension, provider
extension, restricted bash tool) against fake Pi hosts, so the cases run under ``node --test``
from ``fixtures/dynamic-contract-acceptance.test.mjs``. The suite skips itself with a reason when the
pinned Pi packages are not installed (set ``PITWALL_PI_MODULES`` to a ``node_modules`` directory that
contains ``@earendil-works``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parent / "fixtures" / "dynamic-contract-acceptance.test.mjs"
EXPECTED_CASES = 17


@pytest.mark.parity
def test_dynamic_contract_acceptance_suite_passes() -> None:
    """Source: dynamic-contract-acceptance.test.ts, all 17 cases (observer delivery, native rpc replies, tool_call policy, provider and restricted registration)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the Pi extension suites need Node 22 or newer")
    completed = subprocess.run(  # noqa: S603  # reason: fixed argv, node resolved from PATH, no shell
        [node, "--test", "--test-timeout=120000", str(SUITE)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
        timeout=600,
    )
    tail = "\n".join(completed.stdout.splitlines()[-40:] + completed.stderr.splitlines()[-10:])
    assert completed.returncode == 0, f"node --test failed:\n{tail}"
    assert "# fail 0" in completed.stdout, tail
    assert "# cancelled 0" in completed.stdout, tail
    if "# skipped 0" not in completed.stdout:
        pytest.skip("pinned Pi packages are not installed; the extension cases skipped themselves")
    assert f"# pass {EXPECTED_CASES}" in completed.stdout, tail
