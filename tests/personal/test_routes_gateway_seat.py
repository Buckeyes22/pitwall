"""Tests for the `gateway` seat on RouteRunner.attach (free-tier gateway)."""

from __future__ import annotations

import subprocess
from typing import Any

from pitwall.personal.routes import RouteRunner


class _FakeRun:
    def __init__(self, results: dict[str, tuple[int, str, str]]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        code, out, err = self.results.get(command[2], (0, "", ""))
        return subprocess.CompletedProcess(command, code, stdout=out, stderr=err)


def test_attach_gateway_seat_passes_seat_argument() -> None:
    run = _FakeRun({})
    runner = RouteRunner("fake-routing", env={"PATH": "/usr/bin"}, run=run)

    outcome = runner.attach(
        "gw",
        base_url="http://127.0.0.1:8000/v1/openai/coding.chat/v1",
        model_id="gw/glm-flash",
        key_env="PITWALL_API_TOKEN",
        seat="gateway",
    )

    assert outcome.ok and outcome.action == "added"
    assert run.calls == [
        [
            "fake-routing",
            "profiles",
            "add",
            "gw",
            "--base-url",
            "http://127.0.0.1:8000/v1/openai/coding.chat/v1",
            "--model",
            "gw/glm-flash",
            "--api-key-env",
            "PITWALL_API_TOKEN",
            "--seat",
            "gateway",
        ]
    ]


def test_attach_defaults_to_local_seat_when_omitted() -> None:
    run = _FakeRun({})
    runner = RouteRunner("fake-routing", env={"PATH": "/usr/bin"}, run=run)

    outcome = runner.attach(
        "local-only",
        base_url="http://127.0.0.1:8000/v1",
        model_id="local/m",
        key_env="LOCAL_KEY",
    )

    assert outcome.ok and outcome.action == "added"
    assert run.calls[0][-2:] == ["--seat", "local"]


def test_attach_seat_can_be_overridden_from_local_to_gateway() -> None:
    run = _FakeRun({})
    runner = RouteRunner("fake-routing", env={"PATH": "/usr/bin"}, run=run)

    runner.attach(
        "gw2", base_url="http://127.0.0.1:8000/v1", model_id="gw/m", key_env="K", seat="gateway"
    )

    assert run.calls[0][-2:] == ["--seat", "gateway"]
