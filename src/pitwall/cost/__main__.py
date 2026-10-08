"""Entry point for ``python -m pitwall.cost``: serve the cost exporter."""

from __future__ import annotations

import os
from collections.abc import Sequence

import uvicorn

from pitwall.config import require_valid_service_env
from pitwall.service_args import parse_service_args


def _refuse_unrunnable_start() -> None:
    """Exit with one line when the budget or the database would stop the exporter at startup."""
    from pitwall.cli.runtime_errors import database_preflight, exit_with, runtime_reason
    from pitwall.cost.budget_gate import BudgetNotConfigured
    from pitwall.cost.exporter import configured_monthly_budget

    try:
        configured_monthly_budget()
    except BudgetNotConfigured as exc:
        exit_with("pitwall-cost-exporter", runtime_reason(exc) or str(exc))
    reason = database_preflight()
    if reason:
        exit_with("pitwall-cost-exporter", reason)


def main(argv: Sequence[str] | None = None) -> None:
    parse_service_args("pitwall-cost-exporter", argv)
    require_valid_service_env("cost-exporter")
    _refuse_unrunnable_start()
    host = os.environ.get("PITWALL_COST_EXPORTER_HOST", "127.0.0.1")
    port = int(os.environ.get("PITWALL_COST_EXPORTER_PORT", "9109"))
    concurrency = int(os.environ.get("PITWALL_COST_EXPORTER_MAX_CONCURRENCY", "20"))
    if concurrency < 1:
        raise SystemExit("PITWALL_COST_EXPORTER_MAX_CONCURRENCY must be at least 1")
    uvicorn.run(
        "pitwall.cost.exporter:app",
        host=host,
        port=port,
        limit_concurrency=concurrency,
    )


if __name__ == "__main__":
    main()
