from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pitwall.personal.routes import RouteRunner, routing_command


class _FakeRun:
    def __init__(self, results: dict[str, tuple[int, str, str]]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        code, out, err = self.results.get(command[2], (0, "", ""))
        return subprocess.CompletedProcess(command, code, stdout=out, stderr=err)


def test_attach_uses_base_url_model_and_key_env() -> None:
    run = _FakeRun({})
    runner = RouteRunner("fake-routing", env={"PATH": "/usr/bin"}, run=run)

    outcome = runner.attach(
        "ornith",
        base_url="https://p-8000.proxy.runpod.net/v1",
        model_id="Ornith-1.5-35B-A3B",
        key_env="PITWALL_ENDPOINT_KEY",
    )

    assert outcome.ok and outcome.action == "added"
    assert run.calls == [
        [
            "fake-routing",
            "profiles",
            "add",
            "ornith",
            "--base-url",
            "https://p-8000.proxy.runpod.net/v1",
            "--model",
            "Ornith-1.5-35B-A3B",
            "--api-key-env",
            "PITWALL_ENDPOINT_KEY",
            "--seat",
            "local",
        ]
    ]


def test_failure_detail_is_bounded_and_not_reflected() -> None:
    run = _FakeRun({"add": (1, "", "boom Bearer sk-secret-canary " + "x" * 500)})
    runner = RouteRunner("fake-routing", env={}, run=run)

    outcome = runner.attach("r", base_url="u", model_id="m", key_env="K")

    assert not outcome.ok and outcome.action == "failed"
    assert "sk-secret-canary" not in outcome.detail
    assert len(outcome.detail) <= 160


def test_exists_uses_show_exit_code() -> None:
    run = _FakeRun({"show": (0, "{}", "")})
    assert RouteRunner("fake-routing", env={}, run=run).exists("r") is True
    run = _FakeRun({"show": (2, "", "not found")})
    assert RouteRunner("fake-routing", env={}, run=run).exists("r") is False


def test_missing_cli_is_a_failed_outcome() -> None:
    def raising(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    outcome = RouteRunner("nope", env={}, run=raising).probe("r")
    assert outcome == outcome.__class__(
        ok=False, action="failed", detail="routing cli not found: nope"
    )


def test_available_resolves_cli_on_the_runner_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "fake-routing"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    cli.chmod(0o755)

    assert RouteRunner(cli.name, env={"PATH": str(bin_dir)}).available() is True
    assert RouteRunner(cli.name, env={"PATH": str(tmp_path)}).available() is False
    assert RouteRunner(str(cli), env={"PATH": str(tmp_path)}).available() is True


def test_default_command_is_pitwall_agents_and_needs_no_other_executable(tmp_path: Path) -> None:
    argv = routing_command("pitwall agents", {"PATH": str(tmp_path)})
    assert argv == [sys.executable, "-m", "pitwall", "agents"]
    assert RouteRunner("pitwall agents", env={"PATH": str(tmp_path)}).available() is True


def test_default_command_prefers_the_pitwall_console_script_on_path(tmp_path: Path) -> None:
    script = tmp_path / "pitwall"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    assert routing_command("pitwall agents", {"PATH": str(tmp_path)}) == [str(script), "agents"]


def test_multi_token_command_is_split_before_the_verb() -> None:
    run = _FakeRun({})
    RouteRunner("fake-routing --flag", env={"PATH": "/usr/bin"}, run=run).probe("r")
    assert run.calls == [["fake-routing", "--flag", "profiles", "probe", "r"]]


def test_clean_install_registers_and_removes_a_profile_through_the_real_cli(
    tmp_path: Path,
) -> None:
    """No routing executable anywhere on PATH: the default command runs `pitwall agents` itself."""
    home = tmp_path / "home"
    home.mkdir()
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    env = {
        "HOME": str(home),
        "PATH": str(empty_bin),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "PYTHONPATH": os.pathsep.join(sys.path),
    }
    runner = RouteRunner("pitwall agents", env=env)

    assert runner.available() is True
    assert runner.exists("clean") is False
    added = runner.attach(
        "clean",
        base_url="http://127.0.0.1:9/v1",
        model_id="org/model",
        key_env="PITWALL_ENDPOINT_KEY",
    )
    assert added.ok and added.action == "added", added.detail
    assert runner.exists("clean") is True
    probed = runner.probe("clean")
    assert "not found" not in probed.detail
    assert runner.remove("clean").ok
    assert runner.exists("clean") is False
