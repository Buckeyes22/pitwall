"""Top-level cost CLI command contracts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from pitwall.cost.cli import cmd_cost
from pitwall.cost.read_models import RecentWorkloadsRead


def test_cost_workloads_json_uses_shared_read_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = object()
    with (
        patch("pitwall.cost.cli.get_pool", new=AsyncMock(return_value=pool)),
        patch(
            "pitwall.cost.cli.recent_workloads_read",
            new=AsyncMock(return_value=RecentWorkloadsRead(workloads=())),
        ) as read,
    ):
        assert cmd_cost(["workloads", "--limit", "7", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {"workloads": []}
    read.assert_awaited_once_with(
        pool,
        capability_id=None,
        provider_id=None,
        provider_type=None,
        state=None,
        since=None,
        until=None,
        limit=7,
    )


def test_cost_rejects_out_of_range_limit_before_database_access(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("pitwall.cost.cli.get_pool", new=AsyncMock()) as get_pool:
        with pytest.raises(SystemExit) as exc_info:
            cmd_cost(["workloads", "--limit", "101"])
        assert exc_info.value.code == 2

    get_pool.assert_not_awaited()
    assert "invalid choice" in capsys.readouterr().err
