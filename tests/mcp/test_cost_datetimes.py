"""MCP cost tools reject naive datetimes exactly as ``pitwall cost`` does."""

from __future__ import annotations

import argparse

import pytest

from pitwall.cost import cli as cost_cli
from pitwall.mcp.tools import cost

pytestmark = pytest.mark.anyio


def test_naive_datetime_rejected() -> None:
    with pytest.raises(argparse.ArgumentTypeError) as cli_error:
        cost_cli._iso_datetime("2026-09-01T00:00:00")
    with pytest.raises(ValueError) as mcp_error:
        cost._parse_datetime("2026-09-01T00:00:00")
    assert str(mcp_error.value) == str(cli_error.value) == "datetime must include a UTC offset"


def test_aware_datetime_normalises_to_utc() -> None:
    parsed = cost._parse_datetime("2026-09-01T02:00:00+02:00")
    assert parsed is not None
    assert parsed.isoformat() == "2026-09-01T00:00:00+00:00"


async def test_recent_workloads_rejects_naive_since_before_touching_the_pool(monkeypatch) -> None:
    async def _no_pool() -> object:
        raise AssertionError("pool must not be opened")

    monkeypatch.setattr(cost, "get_pool", _no_pool)
    with pytest.raises(ValueError, match="UTC offset"):
        await cost.pitwall_recent_workloads(since="2026-09-01T00:00:00")


def test_docstring_cites_real_modules() -> None:
    assert "src.pitwall" not in (cost.__doc__ or "")
    assert "pitwall.core.cost_reporting" in (cost.__doc__ or "")
