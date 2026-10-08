"""A lease renewal publishes ``lease.renewed`` from REST, the CLI, and MCP alike."""

from __future__ import annotations

import argparse
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall.cli.output import Output
from pitwall.core.enums import LeaseState
from pitwall.db.repository import LeaseMutationResult
from tests.leases.test_teardown import _lease

pytestmark = pytest.mark.anyio

_POOL = object()
_CAPABILITY_NAME = "coding.chat"


class _Fixture:
    def __init__(self) -> None:
        self.lease = _lease(LeaseState.ACTIVE)
        self.repo = SimpleNamespace(
            get=AsyncMock(return_value=self.lease),
            renew=AsyncMock(return_value=LeaseMutationResult(self.lease)),
        )
        self.publish = AsyncMock()


@pytest.fixture
def fixture(monkeypatch: pytest.MonkeyPatch) -> _Fixture:
    fx = _Fixture()
    provider_repo = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(capability_id="c1")))
    capability_repo = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(name=_CAPABILITY_NAME))
    )
    monkeypatch.setattr("pitwall.leases.events.publish_lease_event", fx.publish)
    # The budget reservation is pinned in tests/leases/test_renewal_reservation.py.
    monkeypatch.setattr(
        importlib.import_module("pitwall.leases.mutations"),
        "renewal_extension_usd",
        AsyncMock(return_value=None),
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    # REST resolves the names in its own module; the shared helper resolves them for CLI and MCP.
    # Other suites evict and re-import pitwall.api modules, so patch the module objects that
    # sys.modules holds rather than resolving dotted paths through stale parent attributes.
    for name in ("pitwall.api.routes.leases", "pitwall.leases.mutations"):
        module = importlib.import_module(name)
        monkeypatch.setattr(module, "ProviderRepository", lambda pool: provider_repo)
        monkeypatch.setattr(module, "CapabilityRepository", lambda pool: capability_repo)
    return fx


async def _rest(fx: _Fixture) -> None:
    renew_lease = importlib.import_module("pitwall.api.routes.leases").renew_lease
    await renew_lease(
        fx.lease.id,
        importlib.import_module("pitwall.api.schemas.leases").LeaseRenew(extends_minutes=30),
        repo=fx.repo,
        pool=_POOL,
        redis_client=None,
    )


async def _cli(fx: _Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.cli import leases as cli_leases

    monkeypatch.setattr("pitwall.db.get_pool", AsyncMock(return_value=_POOL))
    monkeypatch.setattr("pitwall.db.repository.LeaseRepository", lambda pool: fx.repo)
    args = argparse.Namespace(lease_id=fx.lease.id, extends_minutes=30, json=True)
    assert await cli_leases._leases_renew_async(args, Output(json_mode=True)) == 0


async def _mcp(fx: _Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.mcp.tools import leases

    monkeypatch.setattr(leases, "get_pool", AsyncMock(return_value=_POOL))
    monkeypatch.setattr(leases, "LeaseRepository", lambda pool: fx.repo)
    await leases.pitwall_renew_lease(fx.lease.id, extends_minutes=30)


@pytest.mark.parametrize("surface", ["rest", "cli", "mcp"])
async def test_renewal_event_published_from_every_surface(
    surface: str, fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    if surface == "rest":
        await _rest(fixture)
    elif surface == "cli":
        await _cli(fixture, monkeypatch)
    else:
        await _mcp(fixture, monkeypatch)

    fixture.publish.assert_awaited_once()
    pool, _redis, event = fixture.publish.await_args.args
    assert pool is _POOL
    assert event["event"] == "lease.renewed"
    assert event["capability"] == _CAPABILITY_NAME
    assert event["data"]["lease_id"] == fixture.lease.id
    assert event["data"]["renewed_by"] == "operator"
