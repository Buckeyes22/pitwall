"""One default API base URL: PITWALL_API_URL / PITWALL_BASE_URL, then PITWALL_API_PORT, then 8080."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.cli.base_url import configured_base_url, default_base_url


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "http://127.0.0.1:8080"),
        ({"PITWALL_API_PORT": "9090"}, "http://127.0.0.1:9090"),
        ({"PITWALL_API_PORT": "not-a-port"}, "http://127.0.0.1:8080"),
        ({"PITWALL_API_PORT": "70000"}, "http://127.0.0.1:8080"),
        ({"PITWALL_API_PORT": "9090", "PITWALL_BASE_URL": "http://broker:1/"}, "http://broker:1"),
        (
            {"PITWALL_BASE_URL": "http://broker:1", "PITWALL_API_URL": "http://api.example/"},
            "http://api.example",
        ),
        ({"PITWALL_API_URL": "  ", "PITWALL_API_PORT": "9091"}, "http://127.0.0.1:9091"),
    ],
)
def test_default_base_url(environ: dict[str, str], expected: str) -> None:
    assert default_base_url(environ) == expected


def test_configured_value_wins_over_the_default() -> None:
    assert configured_base_url(" http://set:1/ ", {"PITWALL_API_PORT": "9090"}) == "http://set:1"
    assert configured_base_url("", {"PITWALL_API_PORT": "9090"}) == "http://127.0.0.1:9090"


def test_init_serve_warm_volume_and_doctor_follow_the_api_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PITWALL_API_PORT", "9191")
    monkeypatch.delenv("PITWALL_API_URL", raising=False)
    monkeypatch.delenv("PITWALL_BASE_URL", raising=False)

    from pitwall.cli.init import _print_smoke_inference_command, _resolve_smoke_base_url

    _print_smoke_inference_command(_resolve_smoke_base_url(None), "embedding.demo", "hi")
    assert "http://127.0.0.1:9191" in capsys.readouterr().out


async def test_doctor_probes_the_api_on_the_configured_port() -> None:
    from pitwall import doctor as doctor_module
    from pitwall.doctor import ProbeError, Probes

    seen: list[str] = []

    async def api_health(url: str, token: str | None, timeout: float) -> dict[str, Any]:
        seen.append(url)
        raise ProbeError("down")

    async def down(*_args: object) -> Any:
        raise ProbeError("down")

    probes = Probes(database=down, redis=down, api_health=api_health, canary=down)
    env = {"PITWALL_API_PORT": "9292", "HOME": "/nonexistent", "PATH": ""}

    await doctor_module._registry_checks(
        env, None, probes, None, None, None, 1.0, lambda *a, **k: None
    )

    assert seen == ["http://127.0.0.1:9292"]


@pytest.mark.parametrize(
    "module",
    [
        "pitwall.cli.serve_model",
        "pitwall.cli.warm_volume",
        "pitwall.mcp.tools.serve",
        "pitwall.tui.app",
    ],
)
def test_no_client_hard_codes_the_api_port(module: str) -> None:
    import importlib
    import inspect

    assert "127.0.0.1:8080" not in inspect.getsource(importlib.import_module(module))
