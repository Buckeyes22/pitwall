"""A dispatch that cannot write its run state says so in one line instead of a traceback."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_dispatch_with_an_unwritable_state_dir_exits_73_with_one_line(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hello\n", encoding="utf-8")
    home.chmod(0o555)
    try:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
        }
        result = subprocess.run(
            [sys.executable, "-m", "pitwall", "agents", "dispatch", "codex", str(prompt)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        home.chmod(0o755)
    assert result.returncode == 73, result.stderr
    assert "Traceback" not in result.stderr
    assert "codex-shim: cannot write run state" in result.stderr
    assert "set XDG_STATE_HOME or HOME to a writable directory" in result.stderr
    assert "SHIM-DONE exit=73" in result.stdout + result.stderr
