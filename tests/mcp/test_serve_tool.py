from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, create_autospec

import pytest

from pitwall.cli.serve_model import RouteRegistration
from pitwall.config import PitwallSettings
from pitwall.personal.state import PersonalLease
from pitwall.serve import ServeRequest, ServeResult


async def async_selfhosted_serve_result(*args: Any, **kwargs: Any) -> ServeResult:
    del args, kwargs
    return ServeResult(
        capability="llm.selfhosted",
        lease_id=None,
        expires_at=None,
        model_id="<model-id>",
        proxy_base_url="http://test/v1/openai/llm.selfhosted/v1",
        engine="vllm",
        variant=None,
        gpu_count=1,
        workload_id=None,
        template_id=None,
        provider_id="prov_selfhosted",
        provider_kind="self_hosted",
        dry_run=False,
        created=True,
        cost_estimate_usd=None,
    )


@pytest.mark.anyio
@pytest.mark.usefixtures("registry_backend")
async def test_mcp_capability_only_returns_self_hosted_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pitwall.mcp.tools.serve as serve_tool

    async def fake_pool() -> object:
        return object()

    monkeypatch.setattr(serve_tool, "get_pool", fake_pool)
    monkeypatch.setattr(serve_tool, "get_settings", PitwallSettings)
    monkeypatch.setattr(serve_tool, "load_catalogue", object)
    monkeypatch.setattr(serve_tool, "serve_model", async_selfhosted_serve_result)
    result = await serve_tool.pitwall_serve_model("llm.selfhosted")
    assert result["provider_kind"] == "self_hosted"
    assert result["lease_id"] is None and result["created"] is True


@pytest.mark.anyio
async def test_serve_tool_uses_personal_service_without_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import datetime as dt

    import pitwall.mcp.tools.serve as tool

    lease = PersonalLease(
        route="ornith",
        pod_id="pod-1",
        model="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        served_model_id="ornith",
        engine="llama.cpp",
        variant=None,
        image="example/llama-server:image",
        gpu_class="NVIDIA GeForce RTX 3090",
        gpu_count=1,
        cloud="community",
        price_per_hour_usd="0.50",
        endpoint_url="https://pod-1-8000.proxy.runpod.net/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        deadline_at=dt.datetime(2026, 9, 2, 1, tzinfo=dt.UTC),
        state="ready",
    )

    class FakeService:
        async def serve(self, *args: Any, **kwargs: Any) -> PersonalLease:
            del args, kwargs
            return lease

    monkeypatch.setattr(tool, "select_backend", lambda: "personal")
    monkeypatch.setattr(tool, "get_settings", PitwallSettings)
    monkeypatch.setattr(tool, "build_personal_service", lambda settings: FakeService())

    result = await tool.pitwall_serve_model(
        "llm.serve-test",
        model="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        gpu_class="NVIDIA GeForce RTX 3090",
        max_usd_per_hour=Decimal("1.00"),
        route="ornith",
    )

    assert result["route"] == "ornith"
    assert result["try"] == "route-shim.sh ornith prompt.md"


def _result() -> ServeResult:
    return ServeResult(
        capability="llm.serve-test",
        lease_id="lease-serve-1",
        expires_at="2026-08-27T14:00:00+00:00",
        model_id="served-model",
        proxy_base_url="http://127.0.0.1:8080/v1/openai/llm.serve-test/v1",
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
@pytest.mark.usefixtures("registry_backend")
async def test_serve_tool_accepts_capability_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pitwall.mcp.tools.serve as tool

    seen: dict[str, ServeRequest] = {}

    async def fake_pool() -> object:
        return object()

    async def fake(
        pool: Any,
        request: ServeRequest,
        **kwargs: Any,
    ) -> ServeResult:
        del pool, kwargs
        seen["request"] = request
        return _result()

    monkeypatch.setattr(tool, "get_pool", fake_pool)
    monkeypatch.setattr(tool, "get_settings", PitwallSettings)
    monkeypatch.setattr(tool, "load_catalogue", object)
    monkeypatch.setattr(tool, "serve_model", fake)

    await tool.pitwall_serve_model("llm.serve-test")

    assert seen["request"].model is None
    assert seen["request"].gpu_class is None
    assert seen["request"].model_fields_set == {"capability_name"}


@pytest.mark.anyio
@pytest.mark.usefixtures("registry_backend")
async def test_serve_tool_explicit_selection_without_engine_defaults_to_vllm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pitwall.mcp.tools.serve as tool

    seen: dict[str, ServeRequest] = {}

    async def fake_pool() -> object:
        return object()

    async def fake(
        pool: Any,
        request: ServeRequest,
        **kwargs: Any,
    ) -> ServeResult:
        del pool, kwargs
        seen["request"] = request
        return _result()

    monkeypatch.setattr(tool, "get_pool", fake_pool)
    monkeypatch.setattr(tool, "get_settings", PitwallSettings)
    monkeypatch.setattr(tool, "load_catalogue", object)
    monkeypatch.setattr(tool, "serve_model", fake)

    await tool.pitwall_serve_model(
        "llm.serve-test",
        "org/model",
        "NVIDIA L4",
    )

    assert seen["request"].engine == "vllm"


def test_serve_tool_has_the_stable_wire_signature() -> None:
    from pitwall.mcp.tools.serve import pitwall_serve_model

    parameters = inspect.signature(pitwall_serve_model).parameters
    assert list(parameters) == [
        "capability",
        "model",
        "gpu_class",
        "gpu_count",
        "engine",
        "variant",
        "template_id",
        "ttl_minutes",
        "idle_timeout_min",
        "max_usd_per_hour",
        "renewal_policy",
        "route",
        "image",
        "served_model_name",
        "rate_per_second",
        "gated",
        "dry_run",
        "idempotency_key",
    ]
    assert parameters["gpu_count"].default == 1
    assert parameters["engine"].default is None
    assert parameters["ttl_minutes"].default == 120
    assert parameters["gated"].default is False
    assert parameters["dry_run"].default is False


@pytest.mark.anyio
@pytest.mark.usefixtures("registry_backend")
async def test_serve_tool_delegates_every_field_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pitwall.mcp.tools.serve as tool

    pool = object()
    catalogue = object()
    settings = PitwallSettings(pitwall_base_url="http://127.0.0.1:8080/")
    seen: dict[str, Any] = {}

    async def fake_serve_model(
        actual_pool: Any,
        request: ServeRequest,
        *,
        base_url: str,
        settings: PitwallSettings,
        catalogue: Any = None,
    ) -> ServeResult:
        seen.update(
            pool=actual_pool,
            request=request,
            base_url=base_url,
            settings=settings,
            catalogue=catalogue,
        )
        return _result()

    async def fake_pool() -> object:
        return pool

    monkeypatch.setattr(tool, "get_pool", fake_pool)
    monkeypatch.setattr(tool, "get_settings", lambda: settings)
    monkeypatch.setattr(tool, "load_catalogue", lambda: catalogue)
    monkeypatch.setattr(tool, "serve_model", fake_serve_model)

    output = await tool.pitwall_serve_model(
        "llm.serve-test",
        "org/model",
        "NVIDIA H100 80GB HBM3",
        gpu_count=2,
        engine="llama.cpp",
        variant="gguf:q4",
        template_id="template-serve",
        ttl_minutes=90,
        idle_timeout_min=20,
        max_usd_per_hour=Decimal("2.50"),
        renewal_policy="manual",
        image="example/llama-server:test",
        served_model_name="served-model",
        rate_per_second=Decimal("0.004"),
        gated=True,
        dry_run=False,
        idempotency_key="serve-request-1",
    )

    request = seen["request"]
    assert request == ServeRequest(
        capability_name="llm.serve-test",
        model="org/model",
        gpu_class="NVIDIA H100 80GB HBM3",
        gpu_count=2,
        engine="llama.cpp",
        variant="gguf:q4",
        template_id="template-serve",
        ttl_minutes=90,
        idle_timeout_min=20,
        max_usd_per_hour=Decimal("2.50"),
        renewal_policy="manual",
        image="example/llama-server:test",
        served_model_name="served-model",
        rate_per_second=Decimal("0.004"),
        gated=True,
        dry_run=False,
        idempotency_key="serve-request-1",
    )
    assert seen["pool"] is pool and seen["catalogue"] is catalogue
    assert seen["base_url"] == "http://127.0.0.1:8080"
    assert output == _result().to_dict()


@pytest.mark.anyio
@pytest.mark.usefixtures("registry_backend")
async def test_serve_tool_returns_failed_route_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pitwall.mcp.tools.serve as tool

    monkeypatch.setattr(tool, "get_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        tool,
        "get_settings",
        lambda: PitwallSettings(pitwall_routing_cli="model-routing"),
    )
    monkeypatch.setattr(tool, "load_catalogue", object)
    monkeypatch.setattr(tool, "serve_model", AsyncMock(return_value=_result()))
    monkeypatch.setattr(
        tool,
        "register_route",
        lambda **kwargs: RouteRegistration(
            ok=False,
            action="failed",
            stderr="routing receiver unavailable",
        ),
    )

    output = await tool.pitwall_serve_model(
        "llm.serve-test",
        "org/model",
        "NVIDIA L4",
        route="glimmer",
    )

    assert output["lease_id"] == "lease-serve-1"
    assert output["route_registration"] == {
        "ok": False,
        "action": "failed",
        "stderr": "routing receiver unavailable",
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("registry_backend")
async def test_serve_tool_preserves_mapped_service_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pitwall.mcp.tools.serve as tool
    from pitwall.api.exceptions import ServeConflict

    async def fake_pool() -> object:
        return object()

    async def fail(*args: Any, **kwargs: Any) -> ServeResult:
        raise ServeConflict("llm.serve-test", "org/other")

    monkeypatch.setattr(tool, "get_pool", fake_pool)
    monkeypatch.setattr(tool, "get_settings", PitwallSettings)
    monkeypatch.setattr(tool, "load_catalogue", object)
    monkeypatch.setattr(tool, "serve_model", fail)
    with pytest.raises(ServeConflict) as exc_info:
        await tool.pitwall_serve_model(
            "llm.serve-test",
            "org/model",
            "NVIDIA L4",
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "serve_conflict"


@pytest.mark.anyio
@pytest.mark.parametrize("refusal", ["budget", "kill_switch"])
@pytest.mark.usefixtures("registry_backend")
async def test_serve_tool_maps_admission_refusals_to_422(
    monkeypatch: pytest.MonkeyPatch,
    refusal: str,
) -> None:
    import pitwall.mcp.tools.serve as tool
    from pitwall.serve import ServeBudgetExhausted, ServeKillSwitchEngaged

    async def fake_pool() -> object:
        return object()

    error = (
        ServeBudgetExhausted(
            reason="monthly_budget",
            snapshot={"estimate_usd": "1"},
        )
        if refusal == "budget"
        else ServeKillSwitchEngaged()
    )
    monkeypatch.setattr(tool, "get_pool", fake_pool)
    monkeypatch.setattr(tool, "get_settings", PitwallSettings)
    monkeypatch.setattr(tool, "load_catalogue", object)
    monkeypatch.setattr(tool, "serve_model", AsyncMock(side_effect=error))

    expected_type = ServeBudgetExhausted if refusal == "budget" else ServeKillSwitchEngaged
    with pytest.raises(expected_type) as raised:
        await tool.pitwall_serve_model(
            "llm.serve-test",
            "org/model",
            "NVIDIA L4",
        )

    assert raised.value.status_code == 422
    expected_code = "budget_exhausted" if refusal == "budget" else "kill_switch_engaged"
    assert raised.value.to_response_body()["error"] == expected_code


@pytest.mark.anyio
async def test_serve_without_a_key_is_a_tool_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import pitwall.mcp.tools.serve as tool
    from pitwall.personal.service import RunPodCredentialMissing
    from pitwall.runpod_credentials import MISSING_CREDENTIAL_MESSAGE

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setattr(tool, "select_backend", lambda: "personal")
    monkeypatch.setattr(tool, "get_settings", PitwallSettings)

    with pytest.raises(RunPodCredentialMissing) as raised:
        await tool.pitwall_serve_model(
            "llm.nokey",
            model="org/model",
            gpu_class="NVIDIA GeForce RTX 3090",
            max_usd_per_hour=Decimal("1.00"),
            route="nokey-route",
        )

    assert raised.value.to_response_body() == {
        "error": "credential_reference_unset",
        "detail": MISSING_CREDENTIAL_MESSAGE,
    }
    assert not (tmp_path / "state").exists()

    from pitwall.mcp.error_adapter import adapt_error
    from pitwall.mcp.error_codes import AUTHZ

    wire = adapt_error(raised.value)
    assert wire.error.code == AUTHZ == -31001
    assert wire.error.data == {
        "error": "credential_reference_unset",
        "detail": MISSING_CREDENTIAL_MESSAGE,
    }


@pytest.mark.anyio
async def test_personal_dry_run_previews_without_launch_or_registry(monkeypatch) -> None:
    import pitwall.mcp.tools.serve as tool
    from pitwall.personal.service import PersonalServeService
    from tests.tui.test_personal_screens import _preview

    preview = _preview()
    service = create_autospec(PersonalServeService, instance=True, spec_set=True)
    service.plan.return_value = preview
    service.serve = AsyncMock(side_effect=AssertionError("dry run must never launch"))
    pool = AsyncMock(side_effect=AssertionError("personal preview must not open a registry"))
    monkeypatch.setattr(tool, "select_backend", lambda: "personal")
    monkeypatch.setattr(tool, "get_settings", PitwallSettings)
    monkeypatch.setattr(tool, "get_pool", pool)
    monkeypatch.setattr(tool, "build_personal_service", lambda settings: service)
    result = await tool.pitwall_serve_model(
        "llm.preview",
        model="org/model",
        gpu_class="NVIDIA GeForce RTX 3090",
        max_usd_per_hour=Decimal("1.00"),
        route="preview-route",
        dry_run=True,
    )
    assert result["dry_run"] is True and result["state"] == "dry_run"
    assert result["route"] == "preview-route"
    assert result["plan"]["model_id"] == preview.plan.model_id
    assert result["max_spend_usd"] == str(preview.max_spend_usd)
    service.plan.assert_awaited_once()
    spec = service.plan.await_args.args[0]
    assert spec.route == "preview-route" and spec.model == "org/model"
    service.serve.assert_not_awaited()
    pool.assert_not_awaited()
