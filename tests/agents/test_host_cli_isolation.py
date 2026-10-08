"""Ledger G-09: no agents test can reach a real host CLI or the real home directory."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import HOST_CLIS, REFUSAL, REFUSAL_EXIT


@pytest.mark.parametrize("name", HOST_CLIS)
def test_real_host_cli_cannot_run_in_tests(name: str, host_cli_stubs: Path) -> None:
    resolved = shutil.which(name)
    assert resolved == str(host_cli_stubs / name)
    completed = subprocess.run([name], capture_output=True, text=True, check=False)
    assert completed.returncode == REFUSAL_EXIT
    assert REFUSAL in completed.stderr


def test_home_is_not_the_real_home(tmp_path: Path) -> None:
    assert Path(os.environ["HOME"]).is_relative_to(tmp_path)
    assert Path(os.environ["XDG_CONFIG_HOME"]).is_relative_to(tmp_path)
    assert Path.home().is_relative_to(tmp_path)
