"""Hermetic CLI contracts for the isolated MC-01 provider-ops group."""

from __future__ import annotations

import json

import pytest

from pitwall.cli.provider_ops import cmd_provider_ops
from tests.providers._provider_operations import (
    StubProviderOperationsService,
    availability,
    descriptor,
    health,
)


def _factory(service: StubProviderOperationsService):
    async def factory() -> StubProviderOperationsService:
        return service

    return factory


def test_provider_ops_json_is_the_same_schema_as_the_shared_service(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = StubProviderOperationsService()

    assert (
        cmd_provider_ops(
            ["list", "--capability-id", "cap-gpu", "--enabled-only", "--limit", "7", "--json"],
            service_factory=_factory(service),
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"items": [descriptor().as_dict()], "total": 1}

    assert (
        cmd_provider_ops(
            ["availability", "prov-vast", "--limit", "3", "--json"],
            service_factory=_factory(service),
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == availability().as_dict()

    assert (
        cmd_provider_ops(
            ["health", "prov-vast", "--probe", "--json"],
            service_factory=_factory(service),
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == health().as_dict()

    assert service.calls == [
        ("list", "cap-gpu", True, 7),
        ("availability", "prov-vast", 3, None),
        ("health", "prov-vast", True, None),
    ]


def test_provider_ops_help_validation_and_not_found_are_local_and_safe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = StubProviderOperationsService()

    with pytest.raises(SystemExit) as help_exit:
        cmd_provider_ops(["health", "--help"], service_factory=_factory(service))
    assert help_exit.value.code == 0
    capsys.readouterr()

    with pytest.raises(SystemExit) as invalid_limit:
        cmd_provider_ops(
            ["availability", "prov-vast", "--limit", "101"], service_factory=_factory(service)
        )
    assert invalid_limit.value.code == 2
    capsys.readouterr()

    assert (
        cmd_provider_ops(["describe", "missing", "--json"], service_factory=_factory(service)) == 2
    )
    assert json.loads(capsys.readouterr().out) == {"error": "provider_not_found", "id": "missing"}


def test_provider_ops_redacts_unexpected_service_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    credential_canary = "cli-provider-credential-canary"

    async def broken_factory() -> StubProviderOperationsService:
        raise RuntimeError(credential_canary)

    assert cmd_provider_ops(["health", "prov-vast", "--json"], service_factory=broken_factory) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"error": "provider_operations_unavailable"}
    assert credential_canary not in output
