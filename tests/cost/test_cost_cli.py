"""Feature-local CLI helper contracts for shared cost reads."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from unittest.mock import AsyncMock, patch

import pytest

from pitwall.cli.output import Output
from pitwall.cost.cli import show_recent_workload_costs
from pitwall.cost.read_models import RecentWorkloadsRead, WorkloadCostRead, WorkloadCostRecord

pytestmark = pytest.mark.anyio


def _read() -> RecentWorkloadsRead:
    return RecentWorkloadsRead(
        workloads=(
            WorkloadCostRecord(
                fields={"id": "wkl-cost"},
                cost=WorkloadCostRead(
                    estimate=Decimal("1.250000"),
                    ceiling=Decimal("1.500000"),
                    confidence="bounded",
                    actual=Decimal("1.000000"),
                    provenance="provider_config",
                ),
            ),
        )
    )


async def test_human_workload_cost_table_labels_distinct_cost_semantics() -> None:
    buffer = StringIO()
    output = Output(stdout_file=buffer)
    with patch(
        "pitwall.cost.cli.recent_workloads_read",
        new=AsyncMock(return_value=_read()),
    ):
        await show_recent_workload_costs(object(), output)

    rendered = buffer.getvalue()
    assert "Estimate" in rendered
    assert "Ceiling" in rendered
    assert "Confidence" in rendered
    assert "Actual" in rendered
    assert "Reconciliation" in rendered
    assert "1.250000" in rendered
    assert "1.500000" in rendered
    assert "actual_recorded" in rendered


async def test_json_workload_cost_output_uses_the_shared_read_serializer() -> None:
    output = Output(json_mode=True)
    with patch(
        "pitwall.cost.cli.recent_workloads_read",
        new=AsyncMock(return_value=_read()),
    ):
        await show_recent_workload_costs(object(), output)

    buffer = StringIO()
    with redirect_stdout(buffer):
        output.emit()
    payload = json.loads(buffer.getvalue())

    cost = payload["workloads"][0]["cost"]
    assert cost["estimate"] == "1.250000"
    assert cost["ceiling"] == "1.500000"
    assert cost["confidence"] == "bounded"
    assert cost["actual"] == "1.000000"
    assert cost["reconciliation_status"] == "actual_recorded"


def test_cost_command_failure_prints_a_fixed_message_without_exception_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pitwall.cost import cli

    secret = "postgresql://pitwall:hunter2@db.internal/pitwall"  # pragma: allowlist secret

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"could not connect to {secret}")

    with patch.object(cli, "_run", new=_boom):
        assert cli.cmd_cost(["summary"]) == 1
        human = capsys.readouterr()
        assert cli.cmd_cost(["summary", "--json"]) == 1
        machine = capsys.readouterr()

    for stream in (human.out, human.err, machine.out, machine.err):
        assert "hunter2" not in stream  # pragma: allowlist secret
        assert "RuntimeError" not in stream
    assert "cost_unavailable" in human.err
    assert json.loads(machine.out) == {"error": "cost_unavailable"}


def test_cost_cli_module_docstring_describes_the_command_group() -> None:
    from pitwall.cost import cli

    doc = cli.__doc__ or ""
    assert "pitwall cost" in doc
    assert "hotspot" not in doc
    assert "no database" not in doc.lower()
