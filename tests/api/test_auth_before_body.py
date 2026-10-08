"""A-22: authentication runs before the request body is buffered."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.conftest import _env_for_app, _import_app

pytestmark = pytest.mark.anyio


async def test_unauthenticated_large_body_rejected_without_buffering(
    clear_app_module: None,
) -> None:
    mod = _import_app(
        _env_for_app(
            PITWALL_API_TOKEN="secret-token",  # pragma: allowlist secret
            PITWALL_API_MAX_BODY_BYTES="1048576",
        )
    )
    reads = 0

    async def counting_app(scope: Any, receive: Any, send: Any) -> None:
        async def counting_receive() -> Any:
            nonlocal reads
            reads += 1
            return await receive()

        await mod.app(scope, counting_receive, send)

    async def body() -> Any:
        yield b"x" * 1024
        yield b"x" * 1024

    transport = httpx.ASGITransport(app=counting_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/inference", content=body())

    assert response.status_code == 401
    assert reads == 0
