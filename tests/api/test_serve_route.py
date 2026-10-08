from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pitwall import serve
from pitwall.api.schemas.serve import ServeCreate
from tests.conftest import _env_for_app, _import_app, make_asyncpg_pool


def _result() -> serve.ServeResult:
    return serve.ServeResult(
        capability="llm.serve-test",
        lease_id="lease-serve-1",
        expires_at="2026-08-27T14:00:00+00:00",
        model_id="served-model",
        proxy_base_url="http://test/v1/openai/llm.serve-test/v1",
        engine="llama.cpp",
        variant="gguf:q4",
        gpu_count=2,
        workload_id="wkl-serve",
        template_id="template-serve",
        provider_id="prov-serve",
        dry_run=False,
        created=True,
        cost_estimate_usd=None,
    )


@pytest.mark.anyio
async def test_post_serve_accepts_capability_only_and_preserves_explicit_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mod = _import_app(_env_for_app())
    import pitwall.api.routes.serve as route

    seen: dict[str, Any] = {}

    async def fake(
        pool: Any,
        request: serve.ServeRequest,
        **kwargs: Any,
    ) -> serve.ServeResult:
        del pool, kwargs
        seen["request"] = request
        return _result().model_copy(update={"created": False})

    monkeypatch.setattr(route, "serve_model", fake)
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/serve",
            json={"capability": "llm.serve-test", "idle_timeout_min": 20},
        )

    assert response.status_code == 200
    assert response.json()["created"] is False
    request = seen["request"]
    assert request.model is None and request.gpu_class is None
    assert request.idle_timeout_min == 20
    assert request.model_fields_set == {"capability_name", "idle_timeout_min"}


@pytest.mark.anyio
async def test_post_serve_without_history_returns_top_level_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.api.exceptions import ServeNoServeHistory

    app_mod = _import_app(_env_for_app())
    import pitwall.api.routes.serve as route

    monkeypatch.setattr(
        route,
        "serve_model",
        AsyncMock(side_effect=ServeNoServeHistory("llm.missing")),
    )
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/serve",
            json={"capability": "llm.missing"},
        )

    assert response.status_code == 422
    assert response.json() == {"error": "no_serve_history"}


@pytest.mark.anyio
async def test_post_serve_delegates_every_revision_3_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mod = _import_app(_env_for_app())
    import pitwall.api.routes.serve as route

    seen: dict[str, Any] = {}

    async def fake(
        pool: Any,
        request: serve.ServeRequest,
        *,
        base_url: str,
        settings: Any,
        catalogue: Any = None,
        redis: Any = None,
        market_service: Any = None,
    ) -> serve.ServeResult:
        seen.update(
            request=request,
            base_url=base_url,
            settings=settings,
            catalogue=catalogue,
            redis=redis,
            market_service=market_service,
        )
        return _result()

    monkeypatch.setattr(route, "serve_model", fake)
    catalogue = object()
    market_service = object()
    monkeypatch.setattr(route, "load_catalogue", lambda: catalogue)
    app_mod.app.state.pool = object()
    app_mod.app.state.runpod_market_service = market_service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/serve",
            json={
                "capability": "llm.serve-test",
                "model": "org/model",
                "gpu_class": "NVIDIA H100 80GB HBM3",
                "gpu_count": 2,
                "engine": "llama.cpp",
                "variant": "gguf:q4",
                "template_id": "template-serve",
                "ttl_minutes": 120,
                "image": "example/llama-server:test",
                "served_model_name": "served-model",
                "datacenter": "US-EXAMPLE-1",
                "container_disk_gb": 40,
                "rate_per_second": "0.004",
                "gated": True,
                "env": {"ROW_ENV": "caller"},
                "start_args": ["--tensor-split", "1,1"],
                "dry_run": False,
                "idempotency_key": "serve-request-1",
            },
        )
    assert response.status_code == 200
    assert response.json()["engine"] == "llama.cpp"
    request = seen["request"]
    assert request.gpu_count == 2 and request.variant == "gguf:q4"
    assert request.rate_per_second == Decimal("0.004") and request.gated is True
    assert seen["base_url"] == "http://test"
    assert seen["catalogue"] is catalogue
    assert seen["market_service"] is market_service


@pytest.mark.anyio
async def test_post_serve_rejects_unknown_engine_and_zero_gpu_count() -> None:
    app_mod = _import_app(_env_for_app())
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        bad_engine = await client.post(
            "/v1/serve",
            json={
                "capability": "c",
                "model": "m",
                "gpu_class": "NVIDIA L4",
                "engine": "tensorrt_llm",
            },
        )
        bad_count = await client.post(
            "/v1/serve",
            json={
                "capability": "c",
                "model": "m",
                "gpu_class": "NVIDIA L4",
                "gpu_count": 0,
            },
        )
    assert bad_engine.status_code == 422
    assert bad_count.status_code == 422


@pytest.mark.anyio
async def test_post_serve_rejects_overprecise_price_cap_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mod = _import_app(_env_for_app())
    import pitwall.api.routes.serve as route

    create_pod = AsyncMock()
    monkeypatch.setattr(route, "serve_model", create_pod)
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/serve",
            json={"capability": "llm.serve-test", "max_usd_per_hour": "0.12345"},
        )

    assert response.status_code == 422
    create_pod.assert_not_awaited()


@pytest.mark.anyio
async def test_post_serve_rejects_invalid_image_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mod = _import_app(_env_for_app())
    import pitwall.api.routes.serve as route

    create_pod = AsyncMock()
    monkeypatch.setattr(route, "serve_model", create_pod)
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/serve",
            json={"capability": "llm.serve-test", "image": "not a Docker image"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_request",
        "detail": [{"type": "string_pattern_mismatch"}],
    }
    assert "not a Docker image" not in response.text
    create_pod.assert_not_awaited()


def test_serve_create_price_cap_and_exported_defaults_match_public_contract() -> None:
    required = {"capability": "llm.serve-test"}
    assert ServeCreate.model_validate(
        {**required, "max_usd_per_hour": "1.25"}
    ).max_usd_per_hour == Decimal("1.25")
    assert ServeCreate.model_validate({**required, "max_usd_per_hour": "1.2345"})
    with pytest.raises(ValidationError):
        ServeCreate.model_validate({**required, "max_usd_per_hour": "0.12345"})

    baseline_path = Path(__file__).resolve().parents[2] / "docs/api/openapi-baseline.json"
    schema = json.loads(baseline_path.read_text())["components"]["schemas"]["ServeCreate"][
        "properties"
    ]
    assert schema["gpu_count"] == {
        "default": 1,
        "minimum": 1.0,
        "title": "Gpu Count",
        "type": "integer",
    }
    assert schema["ttl_minutes"] == {
        "default": 120,
        "maximum": 10080.0,
        "minimum": 1.0,
        "title": "Ttl Minutes",
        "type": "integer",
    }


def test_serve_create_engine_schema_accepts_sglang_and_keeps_legacy_default() -> None:
    required = {
        "capability": "llm.serve-test",
        "model": "org/model",
        "gpu_class": "NVIDIA L4",
    }
    assert ServeCreate.model_validate({**required, "engine": "sglang"}).engine == "sglang"
    assert ServeCreate.model_validate(required).engine == "vllm"
    capability_only = ServeCreate.model_validate({"capability": "llm.serve-test"})
    assert capability_only.model_fields_set == {"capability"}

    engine_schema = ServeCreate.model_json_schema()["properties"]["engine"]
    engine_values = next(item["enum"] for item in engine_schema["anyOf"] if "enum" in item)
    assert engine_values == ["vllm", "llama.cpp", "sglang"]
    assert engine_schema["default"] == "vllm"
    assert "restores serve history" in engine_schema["description"]


@pytest.mark.anyio
async def test_post_serve_rejects_unsafe_shapes_and_invalid_gpu_before_insert() -> None:
    def reject_writes(query: str, *args: object) -> None:
        del args
        if query.lstrip().upper().startswith(("INSERT", "UPDATE")):
            raise AssertionError("persisted before validation")
        return None

    app_mod = _import_app(_env_for_app())

    async def api_error(_request: object, exc: serve.ServeInvalidGpuClass) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())

    app_mod.app.add_exception_handler(serve.ServeInvalidGpuClass, api_error)
    app_mod.app.middleware_stack = None
    pool = make_asyncpg_pool(fetchrow_side_effect=reject_writes)
    app_mod.app.state.pool = pool
    transport = httpx.ASGITransport(app=app_mod.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unsafe_shape = await client.post(
            "/v1/serve",
            json={
                "capability": "bad/name",
                "model": "bad\rmodel",
                "gpu_class": "not-a-gpu",
                "gpu_count": 405,
                "engine": "llama.cpp",
                "ttl_minutes": 4290,
                "image": "example/image:test",
                "rate_per_second": 0.00001,
                "start_args": ["LPT1"],
                "env": {"": ""},
            },
        )
        invalid_gpu = await client.post(
            "/v1/serve",
            json={
                "capability": "llm.valid-name",
                "model": "org/model",
                "gpu_class": "not-a-gpu",
                "image": "example/image:test",
                "rate_per_second": 0.00001,
            },
        )

    assert unsafe_shape.status_code == 422
    unsafe_body = unsafe_shape.json()
    assert unsafe_body["error"] == "invalid_request"
    assert unsafe_body["detail"]
    assert all(set(item) == {"type"} for item in unsafe_body["detail"])
    assert "bad/name" not in unsafe_shape.text
    assert "bad\\rmodel" not in unsafe_shape.text
    assert invalid_gpu.status_code == 422
    assert invalid_gpu.json()["error"] == "invalid_gpu_class"
    assert invalid_gpu.json()["gpu_class"] == "not-a-gpu"
    assert "suggestions" in invalid_gpu.json()


@pytest.mark.anyio
async def test_post_serve_secret_like_start_args_are_422_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Found by the schemathesis fuzz job: ServeRequest is stricter than ServeCreate.
    app_mod = _import_app(_env_for_app())
    import pitwall.api.routes.serve as route

    launch = AsyncMock()
    monkeypatch.setattr(route, "serve_model", launch)
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/serve",
            json={"capability": "llm.serve-test", "start_args": ["A=0"]},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert "A=0" not in response.text
    launch.assert_not_awaited()
