from __future__ import annotations

import socket
from collections.abc import Generator
from unittest.mock import MagicMock

import pytest

from tests import conftest as root_conftest

_TRUTHY_VALUES = ("1", "true", "TRUE", "yes", "on")
_FALSE_VALUES = ("0", "false", "False", "FALSE", "no", "No", "off", "OFF", "", "  ")


@pytest.fixture(autouse=True)
def _clear_live_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*root_conftest.RUNPOD_LIVE_ENV_VARS, *root_conftest.SELFHOSTED_LIVE_ENV_VARS):
        monkeypatch.delenv(name, raising=False)


def _config(*, run_live: bool = False) -> MagicMock:
    config = MagicMock(spec=pytest.Config)
    config.getoption.return_value = run_live
    return config


def _start_guard() -> Generator[None]:
    fixture = root_conftest._block_real_runpod_control_plane_dns.__wrapped__()
    next(fixture)
    return fixture


def _assert_guard_installed() -> None:
    original = socket.getaddrinfo
    fixture = _start_guard()
    try:
        assert socket.getaddrinfo is not original
        for host in ("api.runpod.io", "s3api-US-KS-2.runpod.io"):
            with pytest.raises(AssertionError, match="without RunPod-live authorization"):
                socket.getaddrinfo(host, 443)
    finally:
        fixture.close()
    assert socket.getaddrinfo is original


def test_ordinary_hermetic_test_installs_runpod_dns_guard() -> None:
    config = _config()

    assert root_conftest._live_enabled(config) is False
    _assert_guard_installed()


@pytest.mark.parametrize("run_live", (False, True))
def test_selfhosted_live_configuration_retains_runpod_dns_guard(
    monkeypatch: pytest.MonkeyPatch,
    run_live: bool,
) -> None:
    config = _config(run_live=run_live)
    monkeypatch.setenv("PITWALL_SELFHOSTED_BASE_URL", "https://selfhosted.example.test")
    monkeypatch.setenv("PITWALL_SELFHOSTED_API_KEY_ENV", "PITWALL_TEST_SELFHOSTED_TOKEN")

    assert root_conftest._live_enabled(config) is True
    assert root_conftest._runpod_live_authorized() is False
    _assert_guard_installed()


def test_general_run_live_option_retains_runpod_dns_guard() -> None:
    config = _config(run_live=True)

    assert root_conftest._live_enabled(config) is True
    assert root_conftest._runpod_live_authorized() is False
    _assert_guard_installed()


@pytest.mark.parametrize("name", root_conftest.RUNPOD_LIVE_ENV_VARS)
@pytest.mark.parametrize("value", _TRUTHY_VALUES)
def test_explicit_runpod_live_gate_bypasses_dns_guard(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    original = socket.getaddrinfo

    fixture = _start_guard()
    try:
        assert root_conftest._runpod_live_authorized() is True
        assert socket.getaddrinfo is original
    finally:
        fixture.close()


@pytest.mark.parametrize("name", root_conftest.RUNPOD_LIVE_ENV_VARS)
@pytest.mark.parametrize("value", _FALSE_VALUES)
def test_false_runpod_live_gate_values_do_not_bypass_dns_guard(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    assert root_conftest._runpod_live_authorized() is False
    _assert_guard_installed()
