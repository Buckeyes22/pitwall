"""Thin MCP adapter factory for the shared RunPod market service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pitwall.runpod_market import RunpodMarketService, build_configured_runpod_market_service

type RunpodMarketTool = Callable[[bool], Awaitable[dict[str, object]]]

_service: RunpodMarketService | None = None


def make_pitwall_runpod_catalogue(service: RunpodMarketService) -> RunpodMarketTool:
    """Bind one process service to the registry-owned MCP handler."""

    async def pitwall_runpod_catalogue(force_refresh: bool = False) -> dict[str, object]:
        """Read cached RunPod catalogue, availability, prices, balance, and billing support."""

        snapshot = await service.read(force_refresh=force_refresh)
        return snapshot.to_serializable_dict()

    return pitwall_runpod_catalogue


def get_runpod_market_service() -> RunpodMarketService:
    """Return the one lazy market cache owned by this MCP process."""

    global _service
    if _service is None:
        _service = build_configured_runpod_market_service()
    return _service


async def pitwall_runpod_catalogue(force_refresh: bool = False) -> dict[str, object]:
    """Read cached RunPod catalogue, prices, availability, balance, and billing support."""

    handler = make_pitwall_runpod_catalogue(get_runpod_market_service())
    return await handler(force_refresh)


async def close_runpod_market_service() -> None:
    """Close the lazy process service when the MCP server exits."""

    global _service
    service, _service = _service, None
    if service is not None:
        await service.aclose()


__all__ = [
    "RunpodMarketTool",
    "close_runpod_market_service",
    "get_runpod_market_service",
    "make_pitwall_runpod_catalogue",
    "pitwall_runpod_catalogue",
]
