"""The cost exporter lives at pitwall.cost.exporter only."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


def test_cost_exporter_package_removed() -> None:
    # A developer checkout can keep an untracked ``__pycache__`` after the package is
    # deleted; that stale directory is not the package, so check for source, not a path.
    package_dir = ROOT / "src" / "pitwall" / "cost_exporter"
    assert not list(package_dir.rglob("*.py"))
    try:
        stale = importlib.import_module("pitwall.cost_exporter")
    except ModuleNotFoundError:
        pass
    else:
        # Only an empty namespace package (no module file) may remain from stale bytecode.
        assert getattr(stale, "__file__", None) is None
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pitwall.cost_exporter.app")


def test_exporter_app_and_entry_point_live_under_pitwall_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    exporter = importlib.import_module("pitwall.cost.exporter")
    entry = importlib.import_module("pitwall.cost.__main__")

    assert exporter.app.title == "Pitwall Cost Exporter"
    assert callable(entry.main)


def test_python_dash_m_exporter_module_serves_the_exporter() -> None:
    """The image CMD `python -m pitwall.cost.exporter` reaches the service entry point."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "DATABASE_URL": "postgresql://x/y", "PITWALL_MONTHLY_BUDGET_USD": "10"}
    done = subprocess.run(
        [sys.executable, "-m", "pitwall.cost.exporter", "--help"],
        env=env,
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("usage: pitwall-cost-exporter")
