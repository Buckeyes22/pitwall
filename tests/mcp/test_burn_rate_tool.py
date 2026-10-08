"""Feature-local MCP adapter tests for the burn-rate read model."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pitwall.mcp.tools import burn_rate as burn_rate_tool
from tests.finops._burn_rate import sample_burn_rate_read

pytestmark = pytest.mark.anyio


async def test_burn_rate_tool_delegates_to_the_common_model_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = sample_burn_rate_read()
    pool = object()
    get_pool = AsyncMock(return_value=pool)
    service = AsyncMock(return_value=expected)
    monkeypatch.setattr(burn_rate_tool, "get_pool", get_pool)
    monkeypatch.setattr(burn_rate_tool, "read_configured_burn_rate", service)
    monkeypatch.setattr(burn_rate_tool, "_utc_now", lambda: expected.now)

    result = await burn_rate_tool.pitwall_burn_rate(window_days=7)

    assert result == expected.to_dict()
    get_pool.assert_awaited_once_with()
    service.assert_awaited_once_with(pool, now=expected.now, window_days=7)
