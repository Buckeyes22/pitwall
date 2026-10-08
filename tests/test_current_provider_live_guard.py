"""Provider-specific DNS gates for Vast, Together, and Lambda Cloud tests."""

from __future__ import annotations

import socket
from collections.abc import Generator

import pytest

from tests import conftest as root_conftest

_HOSTS = {
    "vast": "console.vast.ai",
    "together": "api.together.ai",
    "lambda_cloud": "cloud.lambda.ai",
    "model_studio": "token-plan.ap-southeast-1.maas.aliyuncs.com",
}


@pytest.fixture(autouse=True)
def _clear_provider_live_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for names in root_conftest.CURRENT_PROVIDER_LIVE_ENV_VARS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


def _start_guard() -> Generator[None]:
    fixture = root_conftest._block_real_current_provider_control_plane_dns.__wrapped__()
    next(fixture)
    return fixture


@pytest.mark.parametrize(("provider", "host"), _HOSTS.items())
def test_each_current_provider_requires_its_own_live_gate(provider: str, host: str) -> None:
    fixture = _start_guard()
    try:
        with pytest.raises(AssertionError, match="provider-specific live authorization"):
            socket.getaddrinfo(host, 443)
    finally:
        fixture.close()


@pytest.mark.parametrize(("provider", "host"), _HOSTS.items())
def test_exact_provider_gate_allows_only_that_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    host: str,
) -> None:
    gate = root_conftest.CURRENT_PROVIDER_LIVE_ENV_VARS[provider][0]
    monkeypatch.setenv(gate, "true")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [])

    fixture = _start_guard()
    try:
        for other_provider, other_host in _HOSTS.items():
            if other_provider == provider:
                continue
            with pytest.raises(AssertionError, match="provider-specific live authorization"):
                socket.getaddrinfo(other_host, 443)
        assert socket.getaddrinfo(host, 443) == []
    finally:
        fixture.close()


def test_run_live_and_runpod_gates_do_not_unlock_current_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_RUN_LIVE", "true")
    fixture = _start_guard()
    try:
        with pytest.raises(AssertionError, match="provider-specific live authorization"):
            socket.getaddrinfo("api.together.ai", 443)
    finally:
        fixture.close()
