"""MCP ROUTE-01 tools remain thin wrappers around the shared service."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.shared.exceptions import MCPError

from pitwall.mcp.tools import routing
from pitwall.resolver.exceptions import CapabilityNotFoundError
from pitwall.routing.production import NoExecutableRouteError

pytestmark = pytest.mark.anyio


class _Plan:
    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": "plan_mcp",
            "selected_provider_id": "prov_mcp",
            "ranked_candidates": [],
        }


class _Events:
    def to_dict(self) -> dict[str, object]:
        return {
            "workload_id": "wkl_mcp",
            "plan_id": "plan_mcp",
            "events": [],
            "streaming_supported": False,
            "unavailable_reason": "provider_event_stream_unavailable",
        }


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def preview(self, **kwargs: Any) -> _Plan:
        self.calls.append(("preview", kwargs))
        return _Plan()

    async def job_events(self, workload_id: str, *, limit: int) -> _Events:
        self.calls.append(("events", {"workload_id": workload_id, "limit": limit}))
        return _Events()


async def test_preview_tool_delegates_without_transport_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Service()

    async def factory() -> _Service:
        return service

    monkeypatch.setattr(routing, "get_production_routing_service", factory)

    result = await routing.pitwall_preview_route(
        "llm.mcp",
        {"messages": []},
        "sync_inference",
        "prov_mcp",
    )

    assert result["plan_id"] == "plan_mcp"
    assert service.calls[0][0] == "preview"
    assert service.calls[0][1]["payload"] == {"messages": []}


async def test_job_events_tool_delegates_bounded_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Service()

    async def factory() -> _Service:
        return service

    monkeypatch.setattr(routing, "get_production_routing_service", factory)

    result = await routing.pitwall_get_job_events("wkl_mcp", 7)

    assert result["streaming_supported"] is False
    assert service.calls == [("events", {"workload_id": "wkl_mcp", "limit": 7})]


async def test_preview_tool_rejects_unknown_operation() -> None:
    with pytest.raises(MCPError) as exc_info:
        await routing.pitwall_preview_route("llm.mcp", operation="random")

    assert exc_info.value.error.data == {"error": "production_routing_invalid_request"}


def test_feature_manifest_has_stable_names() -> None:
    assert [item.name for item in routing.ROUTING_TOOL_SPECS] == [
        "pitwall_preview_route",
        "pitwall_get_job_events",
    ]


class _FailingService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def preview(self, **kwargs: Any) -> _Plan:
        raise self.error

    async def job_events(self, workload_id: str, *, limit: int) -> _Events:
        raise self.error


def _failing(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    async def factory() -> _FailingService:
        return _FailingService(error)

    monkeypatch.setattr(routing, "get_production_routing_service", factory)


async def test_job_events_for_an_unknown_workload_is_workload_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _failing(monkeypatch, LookupError("workload 'wkl_missing' was not found"))

    with pytest.raises(MCPError) as exc_info:
        await routing.pitwall_get_job_events("wkl_missing")

    assert exc_info.value.error.data["error"] == "workload_not_found"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (NoExecutableRouteError("embedding.demo", []), "no_providers_available"),
        (CapabilityNotFoundError("llm.missing"), "capability_not_found"),
    ],
    ids=["no_route", "unknown_capability"],
)
async def test_preview_planning_failures_keep_the_rest_codes(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
) -> None:
    _failing(monkeypatch, error)

    with pytest.raises(MCPError) as exc_info:
        await routing.pitwall_preview_route("cap_missing", {"texts": ["hi"]})

    assert exc_info.value.error.data["error"] == code
