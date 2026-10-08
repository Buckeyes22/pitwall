"""Entry-point for ``python -m pitwall.api``."""

from __future__ import annotations

import os
from collections.abc import Sequence

import uvicorn

from pitwall.config import require_valid_service_env
from pitwall.service_args import parse_service_args


def main(argv: Sequence[str] | None = None) -> None:
    parse_service_args("pitwall-api", argv)
    require_valid_service_env("api")
    host = os.environ.get("PITWALL_API_HOST", "127.0.0.1")
    port = int(os.environ.get("PITWALL_API_PORT", "8080"))
    concurrency = int(os.environ.get("PITWALL_API_MAX_CONCURRENCY", "100"))
    if concurrency < 1:
        raise SystemExit("PITWALL_API_MAX_CONCURRENCY must be at least 1")
    uvicorn.run(
        "pitwall.api.app:app",
        host=host,
        port=port,
        limit_concurrency=concurrency,
    )


if __name__ == "__main__":
    main()
