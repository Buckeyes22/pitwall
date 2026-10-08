"""Regression: MCP discovery tools honor cost_mode/enabled filters.

Confirmed defect (release-acceptance): ``pitwall_list_capabilities(cost_mode=...)``
validated the enum then discarded it, and both MCP list tools mapped
``enabled=False`` to ``enabled_only=False``, returning enabled and disabled rows.

These hermetic tests pin the generated SQL and bound parameters; the real
PostgreSQL behavior is covered by ``tests/integration/test_discovery_filters.py``.
Parameterless calls keep the pre-fix SQL shape. ``source`` is a REST-only filter
(not part of the advertised MCP signature) and is intentionally absent here.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pitwall.core.enums import CostMode
from tests.conftest import make_asyncpg_pool

pytestmark = pytest.mark.anyio


def _mock_pool() -> MagicMock:
    return make_asyncpg_pool()


class TestListCapabilitiesFilters:
    async def test_cost_mode_filter_reaches_sql(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_capabilities

        pool = _mock_pool()
        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=pool):
            result = await pitwall_list_capabilities(cost_mode="per_request")

        assert result["capabilities"] == []
        query, *params = pool.conn.fetch.call_args[0]
        assert "cost_mode = $1" in query
        assert params[0] == "per_request"

    async def test_enabled_false_returns_only_disabled(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_capabilities

        pool = _mock_pool()
        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=pool):
            await pitwall_list_capabilities(enabled=False)

        query, *params = pool.conn.fetch.call_args[0]
        assert "enabled = false" in query
        assert "enabled = true" not in query
        assert params[-2:] == [100, 0]

    async def test_combined_filters_and_limit_ordering(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_capabilities

        pool = _mock_pool()
        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=pool):
            await pitwall_list_capabilities(
                capability_class="llm",
                cost_mode=CostMode.PER_REQUEST.value,
                enabled=True,
            )

        query, *params = pool.conn.fetch.call_args[0]
        assert "class = $1" in query
        assert "cost_mode = $2" in query
        assert "enabled = true" in query
        assert params == ["llm", "per_request", 100, 0]
        assert query.index("WHERE") < query.index("LIMIT")

    async def test_default_call_sql_unchanged(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_capabilities

        pool = _mock_pool()
        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=pool):
            await pitwall_list_capabilities()

        query, *params = pool.conn.fetch.call_args[0]
        assert query == ("SELECT * FROM pitwall.capabilities ORDER BY name LIMIT $1 OFFSET $2")
        assert params == [100, 0]


class TestListProvidersFilters:
    async def test_enabled_false_returns_only_disabled(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_providers

        pool = _mock_pool()
        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=pool):
            await pitwall_list_providers(enabled=False)

        query, *params = pool.conn.fetch.call_args[0]
        assert "enabled = false" in query
        assert "enabled = true" not in query
        assert params[-2:] == [100, 0]

    async def test_existing_filters_unaffected(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_providers

        pool = _mock_pool()
        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=pool):
            await pitwall_list_providers(
                capability_id="cap_llm",
                provider_type="serverless_lb",
                enabled=True,
            )

        query, *params = pool.conn.fetch.call_args[0]
        assert "capability_id = $1" in query
        assert "provider_type = $2" in query
        assert "enabled = true" in query
        assert params == ["cap_llm", "serverless_lb", 100, 0]
        assert query.index("WHERE") < query.index("LIMIT")

    async def test_default_call_sql_unchanged(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_providers

        pool = _mock_pool()
        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=pool):
            await pitwall_list_providers()

        query, *params = pool.conn.fetch.call_args[0]
        assert query == (
            "SELECT * FROM pitwall.providers ORDER BY priority, name LIMIT $1 OFFSET $2"
        )
        assert params == [100, 0]
