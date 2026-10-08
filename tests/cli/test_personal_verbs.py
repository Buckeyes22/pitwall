"""Tests for the personal-first serve, status, and stop CLI verbs."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest

from pitwall import cli
from pitwall.cli import leases as cli_leases
from pitwall.cli import personal as cli_personal
from pitwall.cli import serve_model as cli_serve_model
from pitwall.personal.state import PersonalLease


def test_setup_closed_input_cancels_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def closed_input(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed_input)
    with pytest.raises(SystemExit) as caught:
        cli_personal._ask_setup("Update shell profile?")
    assert caught.value.code == 2
    stderr = capsys.readouterr().err
    assert "setup cancelled: input closed" in stderr
    assert "Traceback" not in stderr


@pytest.fixture
def fake_service() -> Any:
    lease = PersonalLease(
        route="ornith",
        pod_id="pod-1",
        model="m",
        served_model_id="ornith-model",
        engine="llama.cpp",
        variant=None,
        image="example/image",
        gpu_class="g",
        gpu_count=1,
        cloud="community",
        price_per_hour_usd=str(Decimal("1")),
        endpoint_url="https://pod-1-8000.proxy.runpod.net/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        deadline_at=dt.datetime(2026, 9, 2, 1, tzinfo=dt.UTC),
        state="ready",
    )

    class FakeService:
        stopped: list[str] = []

        async def serve(self, *args: Any, **kwargs: Any) -> PersonalLease:
            del args, kwargs
            return lease

        async def status(self) -> list[PersonalLease]:
            return [lease]

        async def stop(self, route: str) -> PersonalLease:
            self.stopped.append(route)
            return lease

    return FakeService()


@pytest.mark.usefixtures("registry_backend")
def test_serve_on_registry_backend_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    seen: list[list[str]] = []
    monkeypatch.setattr(cli_serve_model, "cmd_serve_model", lambda argv: seen.append(argv) or 0)

    assert cli.main(["serve", "--capability", "c", "--model", "m", "--gpu-class", "g"]) == 0
    assert seen == [["--capability", "c", "--model", "m", "--gpu-class", "g"]]


@pytest.mark.usefixtures("registry_backend")
def test_status_and_stop_on_registry_backend_pass_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    seen: list[list[str]] = []
    monkeypatch.setattr(cli_leases, "cmd_leases", lambda argv: seen.append(argv) or 0)

    assert cli.main(["status"]) == 0
    assert cli.main(["status", "--json"]) == 0
    assert cli.main(["stop", "lease-1", "--json"]) == 0
    assert seen == [["list"], ["list", "--json"], ["stop", "lease-1", "--json"]]


@pytest.mark.usefixtures("registry_backend")
def test_status_on_registry_backend_rejects_unknown_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(cli_leases, "cmd_leases", lambda argv: pytest.fail(f"ran leases {argv}"))

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["status", "--bogus"])

    assert excinfo.value.code == 2
    assert "pitwall status: error: unrecognized arguments: --bogus" in capsys.readouterr().err


def test_serve_personal_requires_cap(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    rc = cli.main(["serve", "--model", "m", "--gpu-class", "g", "--route", "r"])
    assert rc == 2
    assert "--max-usd-per-hour" in capsys.readouterr().err


def test_serve_personal_prints_route_and_shim_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_service: Any,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setattr(cli_personal, "_build_service", lambda settings: fake_service)

    rc = cli.main(
        [
            "serve",
            "--model",
            "m",
            "--gpu-class",
            "g",
            "--route",
            "ornith",
            "--max-usd-per-hour",
            "1",
            "--json",
        ]
    )

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["route"] == "ornith" and data["state"] == "ready"
    assert data["try"] == "route-shim.sh ornith prompt.md"


def test_status_and_stop_personal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_service: Any,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setattr(cli_personal, "_build_service", lambda settings: fake_service)

    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["leases"][0]["route"] == "ornith"
    assert cli.main(["stop", "ornith", "--json"]) == 0
    assert fake_service.stopped == ["ornith"]


@pytest.mark.usefixtures("registry_backend")
def test_status_on_registry_backend_names_the_backend(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def leases(argv: list[str]) -> int:
        print(json.dumps({"leases": []}) if "--json" in argv else "no leases")
        return 0

    monkeypatch.setattr(cli_leases, "cmd_leases", leases)

    assert cli.main(["status"]) == 0
    human = capsys.readouterr().out
    assert human.startswith("Backend: registry (Postgres registry;")
    assert human.endswith("no leases\n")

    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"backend": "registry", "leases": []}
