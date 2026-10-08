"""steer-gate must not deny work when pitwall is merely off the hook's PATH."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = (ROOT / "plugins/claude/hooks/steer-gate.py", ROOT / "plugins/codex/hooks/steer-gate.py")
DISPATCH = {"PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "00000000-0000-4000-8000-0000000000a1"}


def _run(hook: Path, home: Path, *, dispatched: bool) -> tuple[int, str]:
    env = {"PATH": "/nonexistent", "HOME": str(home)}
    if dispatched:
        env.update(DISPATCH)
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=b'{"tool_name": "Bash"}',
        capture_output=True,
        env=env,
        check=False,
    )
    return result.returncode, result.stdout.decode()


@pytest.mark.parametrize("hook", HOOKS, ids=lambda p: p.parent.parent.name)
def test_not_dispatched_allows_without_pitwall_on_path(hook: Path, tmp_path: Path) -> None:
    assert _run(hook, tmp_path, dispatched=False) == (0, "")


@pytest.mark.parametrize("hook", HOOKS, ids=lambda p: p.parent.parent.name)
def test_dispatched_falls_back_to_local_bin_pitwall(hook: Path, tmp_path: Path) -> None:
    binary = tmp_path / ".local/bin/pitwall"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    assert _run(hook, tmp_path, dispatched=True) == (0, "")


@pytest.mark.parametrize("hook", HOOKS, ids=lambda p: p.parent.parent.name)
def test_dispatched_without_any_pitwall_denies_with_accurate_recovery(
    hook: Path, tmp_path: Path
) -> None:
    code, stdout = _run(hook, tmp_path, dispatched=True)
    assert code == 0
    output = json.loads(stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    reason = output["permissionDecisionReason"]
    assert "~/.local/bin" in reason
    assert "uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/" in reason
    assert "--python 3.14.7" not in reason
