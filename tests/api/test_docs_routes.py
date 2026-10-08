"""The interactive docs, the ReDoc page, and the OpenAPI document answer GET and HEAD."""

from __future__ import annotations

import importlib
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.anyio

ROUTES = {
    "/docs": ("text/html", "swagger-ui"),
    "/docs/oauth2-redirect": ("text/html", "oauth2"),
    "/redoc": ("text/html", "redoc"),
    "/openapi.json": ("application/json", '"openapi"'),
}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("RUNPOD_API_KEY", "docs-test-placeholder")
    monkeypatch.setenv("DATABASE_URL", "postgresql://docs@127.0.0.1:9/docs")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:9/0")
    return importlib.import_module("pitwall.api.app").app


@pytest.mark.parametrize("path", sorted(ROUTES))
async def test_get_and_head(app: Any, path: str) -> None:
    content_type, marker = ROUTES[path]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        got = await client.get(path)
        head = await client.head(path)
    assert got.status_code == 200
    assert got.headers["content-type"].startswith(content_type)
    assert marker in got.text.lower() if content_type == "text/html" else marker in got.text
    assert head.status_code == 200
    assert head.headers["content-type"] == got.headers["content-type"]
    assert head.content == b""
