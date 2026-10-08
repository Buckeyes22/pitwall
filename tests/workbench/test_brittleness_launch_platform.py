"""``pitwall workbench launch`` refuses early on hosts without flock(1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench import cli


@pytest.mark.parametrize("platform", ["darwin", "win32", "freebsd14"])
def test_launch_fails_before_any_work_on_non_linux(
    platform: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.sys, "platform", platform)
    started: list[Any] = []
    monkeypatch.setattr(cli, "launch_pi", lambda *a, **k: started.append(a))
    monkeypatch.setattr(
        cli, "require_pinned_toolchain", lambda **k: pytest.fail("toolchain checked first")
    )
    profile = tmp_path / "profiles.json"
    profile.write_text("{}", encoding="utf-8")

    assert cli.main(["launch", str(profile), "local", str(tmp_path)]) == 1

    err = capsys.readouterr().err
    assert "needs Linux" in err
    assert "flock" in err
    assert platform in err
    assert started == []
    assert list(tmp_path.iterdir()) == [profile]
