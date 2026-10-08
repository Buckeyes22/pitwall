"""Pitwall MCP server — local stdio entrypoint on the MCP Python SDK 2 ``MCPServer``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer

from pitwall.config import require_runtime_env
from pitwall.mcp.registry import register_all
from pitwall.mcp.tools.runpod_market import close_runpod_market_service
from pitwall.security.redaction import configure_logging_redaction

configure_logging_redaction()


def _distribution_version() -> str:
    try:
        return version("pitwall")
    except PackageNotFoundError:  # reason: a source checkout without metadata reports unknown
        return "0+unknown"


@asynccontextmanager
async def _mcp_lifespan(_server: MCPServer[Any]) -> AsyncIterator[dict[str, object]]:
    try:
        yield {}
    finally:
        await close_runpod_market_service()


INSTRUCTIONS = (
    "Pitwall brokers GPU inference, pod leases, and RunPod resources under a monthly budget. "
    "Use the catalogue first: pitwall_models_list, then pitwall_models_fit, then "
    "pitwall_serve_model with the fitted variant. Mutating RunPod tools take intent='preview' "
    "(validates and reports the effect and cost ceiling; nothing changes) before "
    "intent='apply', and require an idempotency_key; repeating an apply with the same key and "
    "request returns the stored result (replayed: true) instead of changing RunPod again. "
    "pitwall_submit_inference, pitwall_submit_job, pitwall_lease_pod, and pitwall_serve_model "
    "accept an optional idempotency_key; repeating a call with the same key and request returns "
    "the original result or lease instead of running the work or launching a pod again (a "
    "serve whose lease is no longer serving is refused instead). On mutation_in_progress retry "
    "with the same key later; on idempotency_conflict, idempotency_mismatch, or "
    "lease_state_conflict use a new key. Spend is gated by the "
    "budget: on budget_rejected, sub_budget_rejected, or budget_exhausted read the remedy "
    "field, and change limits only with pitwall_budget_set and a reason. Tool failures are "
    "returned as {'error': '<code>'} tool results; invalid_tool_arguments lists parameter "
    "names."
)

_HOUR_PUBLIC = CacheHint(ttl_ms=3_600_000, scope="public")

mcp: MCPServer[Any] = MCPServer(
    "pitwall",
    version=_distribution_version(),
    instructions=INSTRUCTIONS,
    lifespan=_mcp_lifespan,
    cache_hints={"server/discover": _HOUR_PUBLIC, "tools/list": _HOUR_PUBLIC},
)


def ensure_runtime_env() -> None:
    """Validate required runtime env for the MCP service.

    Called from the serve entry points only, never at import, so test
    collection and ``import pitwall.mcp`` stay hermetic (no SystemExit at
    import time).
    """
    require_runtime_env("mcp")


register_all(mcp)


__all__ = ["INSTRUCTIONS", "ensure_runtime_env", "mcp"]
