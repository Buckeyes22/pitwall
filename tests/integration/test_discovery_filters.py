"""Regression: discovery list filters execute SQL-side before LIMIT/OFFSET.

Confirmed defect (release-acceptance): ``CapabilityRepository.list`` had no
cost_mode/source/enabled filters and ``ProviderRepository.list`` had no
disabled-state filter, so REST ``GET /v1/capabilities?cost_mode=...&source=...``
and both discovery surfaces' ``enabled=false`` silently returned every row.
These tests use real PostgreSQL and mixed matching/nonmatching rows, including
disabled rows and a ``limit=1`` case that only returns the matching row when
the WHERE clause is applied before LIMIT.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.db.repository import CapabilityRepository, ProviderRepository
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]
_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)


def _capability(
    cap_id: str,
    name: str,
    *,
    class_: CapabilityClass = CapabilityClass.EMBEDDING,
    cost_mode: CostMode = CostMode.PER_SECOND,
    source: CapabilitySource = CapabilitySource.API,
) -> Capability:
    return Capability(
        id=cap_id,
        name=name,
        version="1.0.0",
        class_=class_,
        cost_mode=cost_mode,
        source=source,
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(
    prov_id: str,
    capability_id: str,
    *,
    provider_type: ProviderType = ProviderType.SERVERLESS_LB,
    priority: int = 1,
    enabled: bool = True,
    source: CapabilitySource = CapabilitySource.API,
) -> Provider:
    return Provider(
        id=prov_id,
        capability_id=capability_id,
        name=prov_id,
        provider_type=provider_type,
        priority=priority,
        enabled=enabled,
        health_status="healthy",
        source=source,
        updated_at=_NOW,
    )


async def _seed_mixed_capabilities(repo: CapabilityRepository) -> None:
    await repo.create(
        _capability(
            "cap_zero_a",
            "a.zero",
            class_=CapabilityClass.LLM,
            cost_mode=CostMode.ZERO,
            source=CapabilitySource.YAML,
        )
    )
    await repo.create(
        _capability(
            "cap_req_b",
            "b.per-request",
            class_=CapabilityClass.LLM,
            cost_mode=CostMode.PER_REQUEST,
            source=CapabilitySource.MCP,
        )
    )
    await repo.create(
        _capability(
            "cap_sec_c",
            "c.per-second",
            class_=CapabilityClass.EMBEDDING,
            cost_mode=CostMode.PER_SECOND,
            source=CapabilitySource.API,
        )
    )
    # disabled row that matches every other dimension
    await repo.create(
        _capability(
            "cap_req_disabled",
            "d.disabled-per-request",
            class_=CapabilityClass.LLM,
            cost_mode=CostMode.PER_REQUEST,
            source=CapabilitySource.API,
        )
    )
    await repo.disable("cap_req_disabled")


async def test_capability_no_filters_returns_all_rows(pg_pool) -> None:
    repo = CapabilityRepository(pg_pool)
    await _seed_mixed_capabilities(repo)

    names = {cap.name for cap in await repo.list(enabled_only=False)}

    assert names == {"a.zero", "b.per-request", "c.per-second", "d.disabled-per-request"}


async def test_capability_cost_mode_filter_applies_sql_side(pg_pool) -> None:
    repo = CapabilityRepository(pg_pool)
    await _seed_mixed_capabilities(repo)

    rows = await repo.list(cost_mode="per_request")

    assert {cap.name for cap in rows} == {"b.per-request", "d.disabled-per-request"}


async def test_capability_source_filter_applies_sql_side(pg_pool) -> None:
    repo = CapabilityRepository(pg_pool)
    await _seed_mixed_capabilities(repo)

    rows = await repo.list(source="mcp")

    assert {cap.name for cap in rows} == {"b.per-request"}


async def test_capability_enabled_false_returns_only_disabled(pg_pool) -> None:
    repo = CapabilityRepository(pg_pool)
    await _seed_mixed_capabilities(repo)

    rows = await repo.list(enabled=False)

    assert {cap.name for cap in rows} == {"d.disabled-per-request"}
    assert all(cap.enabled is False for cap in rows)


async def test_capability_enabled_only_default_preserved(pg_pool) -> None:
    repo = CapabilityRepository(pg_pool)
    await _seed_mixed_capabilities(repo)

    rows = await repo.list(enabled_only=True)

    assert "d.disabled-per-request" not in {cap.name for cap in rows}
    assert all(cap.enabled is True for cap in rows)


async def test_capability_cost_mode_applied_before_limit(pg_pool) -> None:
    """limit=1 must consider the filter first: the sole match sorts second."""
    repo = CapabilityRepository(pg_pool)
    await _seed_mixed_capabilities(repo)

    rows = await repo.list(cost_mode="per_request", enabled_only=True, limit=1, offset=0)

    assert [cap.name for cap in rows] == ["b.per-request"]


async def test_capability_class_filter_unaffected_by_new_filters(pg_pool) -> None:
    repo = CapabilityRepository(pg_pool)
    await _seed_mixed_capabilities(repo)

    rows = await repo.list(class_filter="llm", cost_mode="per_request", enabled_only=True)

    assert {cap.name for cap in rows} == {"b.per-request"}


async def test_provider_no_filters_returns_all_rows(pg_pool) -> None:
    cap_repo = CapabilityRepository(pg_pool)
    await cap_repo.create(_capability("cap_prov", "llm.providers"))
    repo = ProviderRepository(pg_pool)
    await repo.create(_provider("prov_enabled", "cap_prov", priority=1, enabled=True))
    await repo.create(_provider("prov_disabled", "cap_prov", priority=2, enabled=False))

    rows = await repo.list(enabled_only=False)

    assert {prov.name for prov in rows} == {"prov_enabled", "prov_disabled"}


async def test_provider_enabled_false_returns_only_disabled(pg_pool) -> None:
    cap_repo = CapabilityRepository(pg_pool)
    await cap_repo.create(_capability("cap_prov", "llm.providers"))
    repo = ProviderRepository(pg_pool)
    await repo.create(_provider("prov_enabled", "cap_prov", priority=1, enabled=True))
    await repo.create(_provider("prov_disabled", "cap_prov", priority=2, enabled=False))

    rows = await repo.list(enabled=False)

    assert [prov.name for prov in rows] == ["prov_disabled"]
    assert all(prov.enabled is False for prov in rows)


async def test_provider_enabled_filter_applied_before_limit(pg_pool) -> None:
    """Enabled provider has higher priority; limit=1 must not be eaten by the disabled one."""
    cap_repo = CapabilityRepository(pg_pool)
    await cap_repo.create(_capability("cap_prov", "llm.providers"))
    repo = ProviderRepository(pg_pool)
    await repo.create(_provider("prov_disabled_first", "cap_prov", priority=0, enabled=False))
    await repo.create(_provider("prov_enabled_second", "cap_prov", priority=1, enabled=True))

    rows = await repo.list(enabled=True, limit=1, offset=0)

    assert [prov.name for prov in rows] == ["prov_enabled_second"]


async def test_provider_enabled_only_default_preserved(pg_pool) -> None:
    cap_repo = CapabilityRepository(pg_pool)
    await cap_repo.create(_capability("cap_prov", "llm.providers"))
    repo = ProviderRepository(pg_pool)
    await repo.create(_provider("prov_enabled", "cap_prov", priority=1, enabled=True))
    await repo.create(_provider("prov_disabled", "cap_prov", priority=2, enabled=False))

    rows = await repo.list(enabled_only=True)

    assert [prov.name for prov in rows] == ["prov_enabled"]


async def test_provider_type_filter_unaffected_by_enabled_filter(pg_pool) -> None:
    cap_repo = CapabilityRepository(pg_pool)
    await cap_repo.create(_capability("cap_prov", "llm.providers"))
    repo = ProviderRepository(pg_pool)
    await repo.create(
        _provider(
            "prov_lb_disabled",
            "cap_prov",
            provider_type=ProviderType.SERVERLESS_LB,
            priority=1,
            enabled=False,
        )
    )
    await repo.create(
        _provider(
            "prov_queue_enabled",
            "cap_prov",
            provider_type=ProviderType.SERVERLESS_QUEUE,
            priority=2,
            enabled=True,
        )
    )

    rows = await repo.list(provider_type="serverless_lb", enabled=False)

    assert [prov.name for prov in rows] == ["prov_lb_disabled"]
