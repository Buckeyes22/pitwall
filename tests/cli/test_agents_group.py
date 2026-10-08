"""``pitwall agents``: the Agent Routing group is wired through the top-level CLI."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

from pitwall import cli
from pitwall.agents import installation


def test_agents_help_names_the_group_program(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["agents", "--help"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith("usage: pitwall agents")


def test_install_and_uninstall_call_the_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_install(env: Any, home: Path, **kwargs: Any) -> installation.InstallResult:
        calls.append(("install", {"home": home, **kwargs}))
        return installation.InstallResult(
            files=[home / "a", home / "b"], mcp_harnesses=["claude"], warnings=["careful"]
        )

    def fake_uninstall(env: Any, home: Path, **kwargs: Any) -> installation.InstallResult:
        calls.append(("uninstall", {"home": home}))
        return installation.InstallResult(removed=[home / "a"])

    monkeypatch.setattr(installation, "install", fake_install)
    monkeypatch.setattr(installation, "uninstall", fake_uninstall)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert cli.main(["agents", "install", "--harness", "claude", "--plugin-host", "claude"]) == 0
    out = capsys.readouterr()
    assert (
        "installed 2 files" in out.out and "registered the channel server with: claude" in out.out
    )
    assert "warning: careful" in out.err
    assert calls[0] == (
        "install",
        {"home": tmp_path, "harnesses": ["claude"], "plugin_hosts": ["claude"]},
    )

    assert cli.main(["agents", "uninstall"]) == 0
    assert "removed 1 files" in capsys.readouterr().out
    assert calls[1] == ("uninstall", {"home": tmp_path})


def test_install_refusal_exits_two_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> installation.InstallResult:
        raise installation.InstallationError("destination is not ours")

    monkeypatch.setattr(installation, "install", refuse)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["agents", "install"]) == 2
    assert "pitwall agents: destination is not ours" in capsys.readouterr().err


def test_steer_gate_fails_closed_on_an_unreadable_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Stdin:
        buffer = io.BytesIO(b"{not json")

    monkeypatch.setattr(sys, "stdin", Stdin())
    assert cli.main(["agents", "_steer-gate"]) == 2
    assert "steering gate could not be evaluated" in capsys.readouterr().err


def test_migrate_runs_with_legacy_env_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``agents migrate`` reports the legacy variables, so the legacy-env refusal skips it."""
    from pitwall.agents import migrate

    seen: list[list[str]] = []
    monkeypatch.setattr(migrate, "main", lambda argv: seen.append(list(argv)) or 0)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SUBAGENT_MODEL_" + "ROUTING_UNRESTRICTED", "1")

    assert cli.main(["agents", "migrate"]) == 0
    assert seen == [[]]
    # every other subcommand still refuses while the variable is set
    assert cli.main(["agents", "harnesses"]) == 2
    assert "SUBAGENT_MODEL_" in capsys.readouterr().err


def test_migrate_rejects_arguments_with_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["agents", "migrate", "--nope"]) == 2
    assert "usage: pitwall agents migrate" in capsys.readouterr().err
