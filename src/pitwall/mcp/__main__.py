"""Entry-point for ``python -m pitwall.mcp``, the same server as ``pitwall mcp serve broker``.

The public alpha permits only local stdio. Network transports fail closed until
an authenticated HTTP transport is implemented and reviewed.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from pitwall.mcp import mcp


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m pitwall.mcp",
        description="Serve the Pitwall broker MCP tools over stdio (pitwall mcp serve broker).",
        epilog=(
            "Configured by environment variables and pitwall.toml; "
            "run `pitwall config check` to validate the configuration."
        ),
    )
    parser.parse_args(None if argv is None else list(argv))

    from pitwall.mcp import ensure_runtime_env

    ensure_runtime_env()
    transport = os.environ.get("PITWALL_MCP_TRANSPORT", "stdio")
    if transport != "stdio":
        raise SystemExit(
            "network MCP transports are unavailable in the public alpha; "
            "set PITWALL_MCP_TRANSPORT=stdio"
        )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
