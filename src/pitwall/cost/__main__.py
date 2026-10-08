"""Entry point for ``python -m pitwall.cost``: serve the cost exporter."""

from __future__ import annotations

import os
from collections.abc import Sequence

import uvicorn

from pitwall.config import require_valid_service_env
from pitwall.service_args import parse_service_args


def main(argv: Sequence[str] | None = None) -> None:
    parse_service_args("pitwall-cost-exporter", argv)
    require_valid_service_env("cost-exporter")
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
