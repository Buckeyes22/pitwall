"""A provider update's patch and enable/disable toggle commit together or not at all (G1 6c).

The REST PATCH route and ``pitwall_update_provider`` both write the changed fields, then toggle
``enabled``. Without one transaction a toggle that fails leaves the changed fields behind.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.db.repository import CapabilityRepository, ProviderRepository
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def _seed(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config)"
            " VALUES ('cap_u', 'llm.u', '1.0.0', 'llm', 'per_token', '{}')"
        )
        await conn.execute(
            "INSERT INTO pitwall.providers"
            " (id, capability_id, name, provider_type, config, priority, enabled)"
            " VALUES ('prov_u', 'cap_u', 'prov_u', 'public_endpoint',"
            ' \'{"base_url": "https://example.invalid"}\', 1, true)'
        )


async def _row(pool: Any) -> tuple[int, bool]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT priority, enabled FROM pitwall.providers WHERE id='prov_u'"
        )
    return int(row["priority"]), bool(row["enabled"])


async def _capability_row(pool: Any) -> tuple[str | None, bool]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT config->>'description' AS description, enabled"
            " FROM pitwall.capabilities WHERE id='cap_u'"
        )
    return row["description"], bool(row["enabled"])


async def _mcp_update_capability(pool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.mcp.tools import admin

    async def get_pool() -> Any:
        return pool

    monkeypatch.setattr(admin, "get_pool", get_pool)
    await admin.pitwall_update_capability(
        capability_id="cap_u", description="changed", enabled=False
    )


async def _boom(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("toggle failed")


async def _mcp_update(pool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.mcp.tools import admin

    async def get_pool() -> Any:
        return pool

    monkeypatch.setattr(admin, "get_pool", get_pool)
    await admin.pitwall_update_provider(provider_id="prov_u", priority=9, enabled=False)


async def _rest_update(pool: Any) -> None:
    from pitwall.api import provider_routes

    await provider_routes.patch_provider(
        "prov_u",
        provider_routes.ProviderPatch(priority=9, enabled=False),
        repo=ProviderRepository(pool),
        pool=pool,
    )


async def test_mcp_update_commits_the_patch_and_the_toggle(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(pg_pool)
    await _mcp_update(pg_pool, monkeypatch)
    assert await _row(pg_pool) == (9, False)


async def test_rest_update_commits_the_patch_and_the_toggle(pg_pool: Any) -> None:
    await _seed(pg_pool)
    await _rest_update(pg_pool)
    assert await _row(pg_pool) == (9, False)


async def test_a_failing_toggle_rolls_back_the_mcp_patch(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(pg_pool)
    monkeypatch.setattr(ProviderRepository, "disable", _boom)
    with pytest.raises(RuntimeError, match="toggle failed"):
        await _mcp_update(pg_pool, monkeypatch)
    assert await _row(pg_pool) == (1, True)


async def test_a_failing_toggle_rolls_back_the_rest_patch(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(pg_pool)
    monkeypatch.setattr(ProviderRepository, "disable", _boom)
    with pytest.raises(RuntimeError, match="toggle failed"):
        await _rest_update(pg_pool)
    assert await _row(pg_pool) == (1, True)


async def test_mcp_capability_update_commits_the_patch_and_the_toggle(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(pg_pool)
    await _mcp_update_capability(pg_pool, monkeypatch)
    assert await _capability_row(pg_pool) == ("changed", False)


async def test_a_failing_toggle_rolls_back_the_mcp_capability_patch(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(pg_pool)
    monkeypatch.setattr(CapabilityRepository, "disable", _boom)
    with pytest.raises(RuntimeError, match="toggle failed"):
        await _mcp_update_capability(pg_pool, monkeypatch)
    assert await _capability_row(pg_pool) == (None, True)
