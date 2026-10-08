from __future__ import annotations

import os
from pathlib import Path

import pytest

from pitwall.personal import setup as personal_setup
from pitwall.personal.setup import run_setup


@pytest.fixture(autouse=True)
def _linux_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bash reads ``~/.bashrc`` on Linux; macOS login shells read ``~/.bash_profile``."""
    monkeypatch.setattr(personal_setup.sys, "platform", "linux")


def test_setup_creates_key_and_offers_profile_line(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("# rc\n")
    (home / ".runpod").mkdir()
    (home / ".runpod" / "config.toml").write_text('apikey = "k"\n')
    os.chmod(home / ".runpod" / "config.toml", 0o644)
    answers = iter([True, True])  # tighten config, edit profile
    lines: list[str] = []

    report = run_setup(
        environ={
            "HOME": str(home),
            "SHELL": "/bin/bash",
            "PATH": "",
            "PITWALL_ROUTING_CLI": "no-such-router",
        },
        home=home,
        state_root=tmp_path / "state",
        prompt=lambda _q: next(answers),
        run=lambda *a, **k: None,
        out=lines.append,
    )

    assert report.credential_source == "runpodctl"
    assert (
        report.config_tightened is True
        and oct((home / ".runpod" / "config.toml").stat().st_mode & 0o777) == "0o600"
    )
    assert (tmp_path / "state" / "endpoint.key").exists()
    assert 'export PITWALL_ENDPOINT_KEY="$(cat ' in (home / ".bashrc").read_text()
    assert report.profile_updated is True
    assert report.routing_cli_found is False
    assert report.backend == "personal"
    assert any("no-such-router" in line for line in lines)


def test_setup_is_idempotent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("")
    env = {"HOME": str(home), "SHELL": "/bin/bash", "RUNPOD_API_KEY": "k", "PATH": ""}
    run_setup(
        environ=env,
        home=home,
        state_root=tmp_path / "s",
        prompt=lambda _q: True,
        run=lambda *a, **k: None,
        out=lambda _m: None,
    )
    key_before = (tmp_path / "s" / "endpoint.key").read_text()
    run_setup(
        environ=env,
        home=home,
        state_root=tmp_path / "s",
        prompt=lambda _q: True,
        run=lambda *a, **k: None,
        out=lambda _m: None,
    )
    assert (tmp_path / "s" / "endpoint.key").read_text() == key_before
    assert (home / ".bashrc").read_text().count("PITWALL_ENDPOINT_KEY") == 1


def test_setup_says_serving_needs_a_monthly_budget(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    for environ, expected in (
        ({}, "No monthly budget"),
        ({"PITWALL_MONTHLY_BUDGET_USD": "25"}, "Monthly budget: 25 USD"),
    ):
        lines: list[str] = []
        run_setup(
            environ={"HOME": str(home), "SHELL": "/bin/bash", "PATH": "", **environ},
            home=home,
            state_root=tmp_path / "state",
            prompt=lambda _q: False,
            run=lambda *a, **k: None,
            out=lines.append,
        )
        assert any(line.startswith(expected) for line in lines), lines


def test_setup_finds_the_builtin_routing_command_on_an_empty_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("")
    lines: list[str] = []

    report = run_setup(
        environ={"HOME": str(home), "SHELL": "/bin/bash", "RUNPOD_API_KEY": "k", "PATH": ""},
        home=home,
        state_root=tmp_path / "state",
        prompt=lambda _q: False,
        run=lambda *a, **k: None,
        out=lines.append,
    )

    assert report.routing_cli_found is True
    assert not any("routing command" in line for line in lines)
