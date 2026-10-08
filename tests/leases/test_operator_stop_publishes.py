"""Operator stops from the CLI and MCP publish lease.terminated when Redis is configured.

SDLC 06 (teardown step 8) publishes lease.terminated to Redis. The REST route passes its
client; the CLI and MCP stops passed none, so their stops were never announced.
"""

from __future__ import annotations

import argparse
import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeRedis:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def redis_env(monkeypatch: pytest.MonkeyPatch) -> list[_FakeRedis]:
    made: list[_FakeRedis] = []

    def from_url(url: str, **_kwargs: Any) -> _FakeRedis:
        assert url == "redis://127.0.0.1:6399/0"
        made.append(_FakeRedis())
        return made[-1]

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    monkeypatch.setattr("redis.asyncio.from_url", from_url)
    return made


def _teardown_recorder(seen: list[Any]) -> AsyncMock:
    async def run_teardown(*_args: Any, redis_client: Any = None, **_kwargs: Any) -> Any:
        seen.append(redis_client)
        return SimpleNamespace(lease=SimpleNamespace(id="lease-1", state="stopped"))

    return AsyncMock(side_effect=run_teardown)


async def test_cli_stop_hands_teardown_a_redis_client(
    monkeypatch: pytest.MonkeyPatch, redis_env: list[_FakeRedis]
) -> None:
    from pitwall.cli import leases as cli_leases
    from pitwall.cli import output as cli_output

    seen: list[Any] = []
    # Patch the module object each call site imports from (sys.modules), which stays right
    # even after another test reloads the package.
    monkeypatch.setattr(
        importlib.import_module("pitwall.api.leases"), "run_teardown", _teardown_recorder(seen)
    )
    monkeypatch.setattr("pitwall.db.get_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(cli_leases, "guard_cli_pre_spend", lambda *_a, **_k: None)
    monkeypatch.setattr(cli_leases, "safe_json", lambda lease: {"id": lease.id})

    args = argparse.Namespace(lease_id="lease-1", reason="done")
    assert await cli_leases._leases_stop_async(args, cli_output.Output(True)) == 0

    assert seen == redis_env and redis_env[0].closed


async def test_mcp_stop_hands_teardown_a_redis_client(
    monkeypatch: pytest.MonkeyPatch, redis_env: list[_FakeRedis]
) -> None:
    from pitwall.mcp.tools import leases

    seen: list[Any] = []
    monkeypatch.setattr(
        importlib.import_module("pitwall.api.leases.teardown"),
        "run_teardown",
        _teardown_recorder(seen),
    )
    monkeypatch.setattr(leases, "get_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(leases, "lease_to_response", lambda lease: {"id": lease.id})

    assert await leases.pitwall_stop_lease("lease-1", reason="done") == {"id": "lease-1"}

    assert seen == redis_env and redis_env[0].closed


async def test_without_redis_url_stops_still_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.mcp.tools import leases

    monkeypatch.delenv("REDIS_URL", raising=False)
    seen: list[Any] = []
    monkeypatch.setattr(
        importlib.import_module("pitwall.api.leases.teardown"),
        "run_teardown",
        _teardown_recorder(seen),
    )
    monkeypatch.setattr(leases, "get_pool", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(leases, "lease_to_response", lambda lease: {"id": lease.id})

    await leases.pitwall_stop_lease("lease-1")
    assert seen == [None]
