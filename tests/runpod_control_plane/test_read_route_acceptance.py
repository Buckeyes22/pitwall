"""Hermetic HTTP read contracts; provider access and global auth are separate lanes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.exceptions import install_api_error_handler
from pitwall.api.routes.runpod_resources import router, runpod_control_plane_service

pytestmark = pytest.mark.anyio

_RESOURCE_CASES = [
    ("pods", "pod", {"status": "RUNNING"}),
    ("endpoints", "endpoint", {"workers": {}, "scaling": {}, "flashboot": False}),
    (
        "templates",
        "template",
        {
            "image": "example/image:1",
            "disk_gb": 20,
            "volume_gb": 0,
            "serverless": True,
            "public": False,
        },
    ),
    ("volumes", "volume", {"size_gb": 80, "data_center_id": "fixture-dc"}),
    ("registry-auths", "registry_auth", {}),
    ("hub/templates", "hub_template", {"image": "example/image:1", "serverless": True}),
]


@pytest.mark.parametrize(("path", "singular", "fields"), _RESOURCE_CASES)
@pytest.mark.parametrize("detail", [False, True])
async def test_read_routes_forward_exact_resource_and_preserve_result(
    path: str, singular: str, fields: dict[str, object], detail: bool
) -> None:
    row = {"id": "resource_selected", "name": "selected fixture", **fields}
    method = ("get_" + singular) if detail else ("list_" + singular + "s")
    call = AsyncMock(return_value=row if detail else [row])
    source = SimpleNamespace(**{method: call})
    app = FastAPI()
    app.include_router(router)
    install_api_error_handler(app)
    app.dependency_overrides[runpod_control_plane_service] = lambda: source
    url = "/v1/admin/runpod/" + path + ("/resource_selected" if detail else "")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(url)
    assert response.status_code == 200, response.text
    result = response.json() if detail else response.json()[0]
    assert result["id"] == "resource_selected"
    assert result["name"] == "selected fixture"
    for key, value in fields.items():
        if key not in {"workers", "scaling"}:
            assert result[key] == value
    if detail:
        call.assert_awaited_once_with("resource_selected")
    elif singular == "hub_template":
        call.assert_awaited_once_with(limit=50, offset=0)
    else:
        call.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("suffix", "method", "args", "kwargs"),
    [
        ("?limit=7&offset=12", "list_hub_templates", (), {"limit": 7, "offset": 12}),
        (
            "/search?query=language%20model&limit=3",
            "search_hub_templates",
            ("language model",),
            {"limit": 3},
        ),
    ],
)
async def test_hub_query_and_pagination_reach_service_exactly(
    suffix: str, method: str, args: tuple, kwargs: dict
) -> None:
    call = AsyncMock(return_value=[])
    app = FastAPI()
    app.include_router(router)
    install_api_error_handler(app)
    app.dependency_overrides[runpod_control_plane_service] = lambda: SimpleNamespace(
        **{method: call}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/admin/runpod/hub/templates" + suffix)
    assert response.status_code == 200 and response.json() == []
    call.assert_awaited_once_with(*args, **kwargs)


@pytest.mark.parametrize(
    "suffix", ["?limit=0", "?limit=101", "?offset=-1", "/search?query=", "/search?limit=3"]
)
async def test_invalid_hub_query_never_calls_service(suffix: str) -> None:
    source = SimpleNamespace(list_hub_templates=AsyncMock(), search_hub_templates=AsyncMock())
    app = FastAPI()
    app.include_router(router)
    install_api_error_handler(app)
    app.dependency_overrides[runpod_control_plane_service] = lambda: source
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/admin/runpod/hub/templates" + suffix)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    source.list_hub_templates.assert_not_awaited()
    source.search_hub_templates.assert_not_awaited()
