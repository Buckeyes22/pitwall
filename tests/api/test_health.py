"""A-23: health endpoints are provider-neutral and public paths are real routes."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import _env_for_app, _import_app

pytestmark = pytest.mark.anyio


async def test_health_is_provider_neutral(clear_app_module: None) -> None:
    mod = _import_app(_env_for_app())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        for path in ("/health", "/healthz"):
            body = (await client.get(path)).json()
            assert body == {"ok": True}, path


def test_public_health_paths_all_exist(clear_app_module: None) -> None:
    mod = _import_app(_env_for_app())
    routes = {getattr(route, "path", None) for route in mod.app.routes}
    assert routes >= mod._PUBLIC_HEALTH_PATHS
    assert not hasattr(mod, "_RUNPOD_API_KEY")
    assert not hasattr(mod, "_DATABASE_URL")
