"""Drill JSON reports land inside the checkout's artifacts directory by default."""

from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.db import drill_evidence

_REPO = Path(__file__).resolve().parents[2]


def test_default_report_directory_is_the_repo_artifacts_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PITWALL_DRILL_ARTIFACTS_DIR", raising=False)
    written: list[Path] = []
    monkeypatch.setattr(Path, "mkdir", lambda self, **kwargs: written.append(self))
    monkeypatch.setattr(Path, "write_text", lambda self, *args, **kwargs: 0)

    path = drill_evidence.write_drill_json_report({"ok": True}, drill_type="location")

    assert path.parent == _REPO / "artifacts" / "drills"
    assert written == [_REPO / "artifacts" / "drills"]


def test_the_environment_overrides_the_report_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PITWALL_DRILL_ARTIFACTS_DIR", str(tmp_path / "drills"))

    path = drill_evidence.write_drill_json_report({"ok": True}, drill_type="location")

    assert path.parent == tmp_path / "drills"
    assert path.is_file()
