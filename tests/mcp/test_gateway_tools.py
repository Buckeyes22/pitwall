"""Feature-local MCP tests for the free-tier gateway catalog read and quota list (Task 14)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from pitwall.routing.quota import QuotaRecord

pytestmark = pytest.mark.anyio


def _record() -> QuotaRecord:
    return QuotaRecord(
        provider_id="prov_gw_alpha",
        pool_key="alpha-pool",
        free_type="recurring-monthly",
        window_start=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        reset_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
        budget_units=Decimal("10"),
        used_units=Decimal("2"),
        tos_verdict="ok",
        evidence={},
        updated_at=dt.datetime(2026, 9, 10, tzinfo=dt.UTC),
    )


class _FakeQuotaRepo:
    def __init__(self, records: list[QuotaRecord]) -> None:
        self._records: tuple[QuotaRecord, ...] = tuple(records)

    async def list_all(self) -> tuple[QuotaRecord, ...]:
        return self._records


async def test_gateway_catalog_read_is_read_only_and_hermetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.mcp.tools.gateway import pitwall_gateway_catalog_read

    result = await pitwall_gateway_catalog_read(tos="ok")
    providers = result["providers"]
    assert providers, "fixture catalog should contain at least one tos=ok provider"
    catalog_tos = [row["gateway"]["catalog"]["tos"] for row in providers]
    assert all(t == "ok" for t in catalog_tos)
    assert result["source"] == "config/gateway-catalog.json"


async def test_quota_list_uses_quota_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pitwall.mcp.tools.gateway.get_pool",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "pitwall.mcp.tools.gateway.QuotaRepository",
        lambda _pool: _FakeQuotaRepo([_record()]),
    )

    from pitwall.mcp.tools.gateway import pitwall_quota_list

    result = await pitwall_quota_list()
    assert result["quotas"][0]["headroom"] == pytest.approx(0.8)


def test_registry_has_expected_tools_including_gateway() -> None:
    from pitwall.mcp.registry import TOOL_NAMES
    from tests.mcp.test_registry_health import EXPECTED_TOOL_COUNT

    assert len(TOOL_NAMES) == EXPECTED_TOOL_COUNT
    assert {"pitwall_gateway_catalog_read", "pitwall_quota_list"} <= TOOL_NAMES


async def test_routable_catalog_filter_excludes_gates_blocked_tos_and_discontinued(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from pitwall.mcp.tools import gateway

    cases = [
        ("keyless", {"free_type": "keyless", "tos": "ok"}),
        (
            "hard-stop",
            {"free_type": "recurring-monthly", "tos": "ok", "hard_stop_guaranteed": True},
        ),
        ("metered", {"free_type": "recurring-monthly", "tos": "ok", "hard_stop_guaranteed": False}),
        ("gated", {"free_type": "keyless", "tos": "ok", "eligibility_gate": "invite"}),
        ("avoid", {"free_type": "keyless", "tos": "avoid", "hard_stop_guaranteed": True}),
        ("unknown", {"free_type": "keyless", "tos": "unknown", "hard_stop_guaranteed": True}),
        ("discontinued", {"free_type": "discontinued", "tos": "ok", "hard_stop_guaranteed": True}),
    ]
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {"providers": [{"id": name, "gateway": {"catalog": data}} for name, data in cases]}
        )
    )
    before = path.read_bytes()
    monkeypatch.setattr(gateway, "_CATALOG_PATH", path)
    pool = AsyncMock(side_effect=AssertionError("catalog reads must not open the registry"))
    monkeypatch.setattr(gateway, "get_pool", pool)
    all_rows = await gateway.pitwall_gateway_catalog_read()
    assert [row["id"] for row in all_rows["providers"]] == [name for name, _ in cases]
    for kwargs in ({"routable_only": True}, {"tos": "ok", "routable_only": True}):
        result = await gateway.pitwall_gateway_catalog_read(**kwargs)
        assert [row["id"] for row in result["providers"]] == ["keyless", "hard-stop"]
    assert (await gateway.pitwall_gateway_catalog_read(tos="avoid", routable_only=True))[
        "providers"
    ] == []
    assert path.read_bytes() == before
    pool.assert_not_awaited()
