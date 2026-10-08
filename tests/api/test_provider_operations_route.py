"""Feature-local REST contracts for the shared provider-operations service."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pitwall.api.exceptions import PitwallApiError
from pitwall.api.routes.provider_operations import provider_operations_router
from tests.providers._provider_operations import (
    StubProviderOperationsService,
    availability,
    descriptor,
    health,
)

pytestmark = pytest.mark.anyio


def _app(service: StubProviderOperationsService) -> FastAPI:
    app = FastAPI()
    app.state.provider_operations_service = service

    @app.exception_handler(PitwallApiError)
    async def _pitwall_error(_request: Request, exc: PitwallApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())

    app.include_router(provider_operations_router)
    return app


async def test_provider_operations_routes_return_the_shared_model_serialization() -> None:
    service = StubProviderOperationsService()
    app = _app(service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        listing = await client.get(
            "/v1/provider-ops/descriptors?capability_id=cap-gpu&enabled_only=true&limit=7"
        )
        described = await client.get("/v1/provider-ops/descriptors/prov-vast")
        observed = await client.get("/v1/provider-ops/prov-vast/availability?limit=3")
        persisted = await client.get("/v1/provider-ops/prov-vast/health")
        probed = await client.get("/v1/provider-ops/prov-vast/health?probe=true")

    assert listing.json() == {"items": [descriptor().as_dict()], "total": 1}
    assert described.json() == descriptor().as_dict()
    assert observed.json() == availability().as_dict()
    assert persisted.json() == health(probe=False).as_dict()
    assert probed.json() == health(probe=True).as_dict()
    assert service.calls == [
        ("list", "cap-gpu", True, 7),
        ("describe", "prov-vast"),
        ("availability", "prov-vast", 3, None),
        ("health", "prov-vast", False, None),
        ("health", "prov-vast", True, None),
    ]


async def test_provider_operations_routes_are_bounded_and_safe_for_unknown_provider() -> None:
    service = StubProviderOperationsService()
    app = _app(service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = await client.get("/v1/provider-ops/prov-vast/availability?limit=101")
        missing = await client.get("/v1/provider-ops/missing/health?probe=true")

    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert missing.json() == {"error": "provider_not_found", "id": "missing"}
    assert service.calls == [("health", "missing", True, None)]


def test_provider_operations_openapi_labels_read_only_contracts() -> None:
    app = _app(StubProviderOperationsService())
    schema = app.openapi()

    assert schema["paths"]["/v1/provider-ops/descriptors"]["get"]["tags"] == ["provider-operations"]
    parameters = schema["paths"]["/v1/provider-ops/{provider_id}/availability"]["get"]["parameters"]
    limit = next(parameter for parameter in parameters if parameter["name"] == "limit")
    assert limit["schema"] == {
        "default": 100,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    assert "ProviderOperationAvailabilityResponse" in schema["components"]["schemas"]


async def test_descriptor_listing_rejects_nul_capability_id() -> None:
    # Regression (fuzz): a NUL capability filter reached the provider lookup (500).
    service = StubProviderOperationsService()
    app = _app(service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/provider-ops/descriptors?capability_id=cap%00x")

    assert response.status_code == 422
    assert service.calls == []
