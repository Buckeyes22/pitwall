"""``pitwall mcp install`` forwards every variable the MCP server's config check requires."""

from __future__ import annotations

from pitwall.config import _CORE_RUNTIME_ENV, _REQUIRED_ENV_BY_SERVICE
from pitwall.mcp_install import FORWARDED_ENV


def test_forwards_every_required_mcp_variable() -> None:
    required = set(_REQUIRED_ENV_BY_SERVICE["mcp"]) | set(_CORE_RUNTIME_ENV)
    assert required <= set(FORWARDED_ENV), sorted(required - set(FORWARDED_ENV))
