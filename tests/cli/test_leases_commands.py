"""An operator who launched with serve-model must be able to stop it.

`leases` exposed only `list`, so stopping a running lease meant calling the API
by hand — with a paid pod running while you look up the route.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _help(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pitwall", *args, "--help"],
        cwd=ROOT,
        env={
            "DATABASE_URL": "",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(Path.home()),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


def test_leases_exposes_stop() -> None:
    assert "stop" in _help("leases")


def test_leases_exposes_renew() -> None:
    assert "renew" in _help("leases")


def test_renew_uses_the_api_field_name() -> None:
    """The API field is extends_minutes; ttl_minutes does not exist."""
    text = _help("leases", "renew")
    assert "--extends-minutes" in text
    assert "--ttl-minutes" not in text
