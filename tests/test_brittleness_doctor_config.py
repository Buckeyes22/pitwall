"""``pitwall doctor`` reports an invalid pitwall.toml as a failure with the parse error."""

from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.doctor import DoctorReport, Probes, run_doctor


def _probes() -> Probes:
    async def unused(*_args: object) -> dict[str, object]:
        raise AssertionError("personal mode never probes the registry services")

    return Probes(database=unused, redis=unused, api_health=unused, canary=unused)  # type: ignore[arg-type]


async def _doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toml_text: str) -> DoctorReport:
    config = tmp_path / "pitwall.toml"
    config.write_text(toml_text, encoding="utf-8")
    env = {
        "HOME": str(tmp_path),
        "PATH": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "PITWALL_CONFIG_FILE": str(config),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return await run_doctor(environ=env, probes=_probes())


async def test_unparseable_toml_is_a_fail_row_not_a_traceback_or_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = await _doctor(tmp_path, monkeypatch, "this is = = not toml [\n")

    rows = [check for check in report.checks if check.id == "config.file"]
    assert [row.status for row in rows] == ["fail"]
    assert "invalid TOML at line 1" in rows[0].detail
    assert report.exit_code() != 0


async def test_invalid_personal_backend_is_a_fail_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = await _doctor(tmp_path, monkeypatch, '[personal]\nbackend = "bogus"\n')

    rows = [check for check in report.checks if check.id == "config.file"]
    assert [row.status for row in rows] == ["fail"]
    assert "[personal] backend" in rows[0].detail
    assert "pitwall.toml" in rows[0].detail
    assert report.exit_code() != 0


async def test_no_config_file_is_still_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PITWALL_CONFIG_FILE", raising=False)
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "s")}

    report = await run_doctor(environ=env, probes=_probes())

    assert {check.id: check.status for check in report.checks}["config.file"] == "skip"
