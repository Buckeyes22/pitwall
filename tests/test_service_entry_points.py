"""The service entry points answer --help and refuse unknown arguments before starting."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from pitwall import service_args

ENTRY_POINTS = [
    ("api", "pitwall-api"),
    ("reconciler", "pitwall-reconciler"),
    ("webhook", "pitwall-webhook"),
    ("cost_exporter", "pitwall-cost-exporter"),
]


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """No runtime settings, and any server start is recorded instead of run."""
    for name in tuple(os.environ):
        if name.startswith(("PITWALL_", "RUNPOD_")) or name in {"DATABASE_URL", "REDIS_URL"}:
            monkeypatch.delenv(name, raising=False)
    started: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: started.append(args))
    return started


@pytest.mark.parametrize(("function", "prog"), ENTRY_POINTS)
def test_help_prints_usage_and_exits_zero_without_starting(
    function: str, prog: str, unconfigured: list[object], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        getattr(service_args, function)(["--help"])

    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"usage: {prog}")
    assert "pitwall config check" in out
    assert unconfigured == []


@pytest.mark.parametrize(("function", "prog"), ENTRY_POINTS)
def test_an_unknown_argument_is_refused_before_starting(
    function: str, prog: str, unconfigured: list[object], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        getattr(service_args, function)(["--bogus"])

    assert raised.value.code == 2
    assert f"{prog}: error: unrecognized arguments: --bogus" in capsys.readouterr().err
    assert unconfigured == []


def test_console_scripts_point_at_the_argument_first_entry_points() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    scripts = pyproject["project"]["scripts"]
    for function, prog in ENTRY_POINTS:
        assert scripts[prog] == f"pitwall.service_args:{function}"
    assert sorted(service_args.SERVICES) == sorted(prog for _function, prog in ENTRY_POINTS)
