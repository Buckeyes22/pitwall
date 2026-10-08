"""OpenAI proxy fallback execution."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi.responses import StreamingResponse

from pitwall.config import PitwallSettings
from pitwall.core.enums import CapabilityClass, CapabilitySource, ProviderType, WorkloadState
from pitwall.core.models import Capability, Provider
from pitwall.cost.budget_gate import BudgetAdmission
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.routing.fallback import OpenAIProxyExecutionError, OpenAIProxyResult

_TEST_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


class _BlockingResponseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"delta":"started"}\n\n'
        self.waiting.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


# The bound is only a hang guard: readiness is event-driven, and a tight wall-clock limit
# misreports a CPU-starved worker as a stalled stream (ledger G-07).
async def _wait_for_stream_ready(
    request_task: asyncio.Task[httpx.Response],
    ready: asyncio.Event,
    *,
    timeout: float = 30,
) -> None:
    """Report early request failures instead of misdiagnosing them as stream stalls."""
    ready_task = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {request_task, ready_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if request_task in done:
            response = request_task.result()  # Preserve the actual request exception.
            raise AssertionError(f"request ended before cancellation: HTTP {response.status_code}")
        if ready_task not in done:
            stack = " -> ".join(
                f"{frame.f_code.co_name}:{frame.f_lineno}" for frame in request_task.get_stack()
            )
            raise TimeoutError(f"stream not ready after {timeout}s; request pending at {stack}")
    finally:
        ready_task.cancel()
        await asyncio.gather(ready_task, return_exceptions=True)


class _FailingResponseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"delta":"started"}\n\n'
        raise httpx.ReadError("stream-secret-canary")

    async def aclose(self) -> None:
        self.closed = True


def _make_capability() -> Capability:
    return Capability(
        id="cap_llm_qwen3_32b",
        name="llm.qwen3-32b",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        description="Qwen3 32B AWQ",
        cost_mode="per_request",
        source=CapabilitySource.API,
        enabled=True,
        created_at=_TEST_NOW,
        updated_at=_TEST_NOW,
    )


def _make_provider(
    *,
    id: str,
    endpoint_id: str,
    priority: int,
    fallback_chain: list[str] | None = None,
) -> Provider:
    config: dict[str, object] = {
        "openai_base_url": f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1",
        "per_request": "0.001000",
    }
    if fallback_chain is not None:
        config["fallback_chain"] = fallback_chain
    return Provider(
        id=id,
        capability_id="cap_llm_qwen3_32b",
        name=id,
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        runpod_endpoint_id=endpoint_id,
        config=config,
        priority=priority,
        enabled=True,
        health_status="healthy",
        updated_at=_TEST_NOW,
    )


def _env_for_app() -> dict[str, str]:
    return {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
    }


def test_attempt_component_names_are_stable_for_arbitrary_provider_ids() -> None:
    from pitwall.api.routes.openai import _provider_attempt_component_name

    assert (
        _provider_attempt_component_name("prov_primary", "requests")
        == "pa_prov_p_9790f4e684f734531fee3f074ce283_reques_ec72420df5dfbdce"
    )
    collision_vectors = (
        "9/provider abc collision candidate 38512",
        "9/provider abc collision candidate 74029",
        "9_provider_a_65eccd05",
    )
    rendered_vectors = {
        _provider_attempt_component_name(provider_id, "requests")
        for provider_id in collision_vectors
    }
    assert len(rendered_vectors) == len(collision_vectors)
    assert _provider_attempt_component_name(
        collision_vectors[0], "requests"
    ) == _provider_attempt_component_name(collision_vectors[0], "requests")

    for provider_id, component_name in (
        ("prov_01K5ABCDEF0123456789", "lease_covered"),
        ("9/provider with spaces", "x" * 64),
    ):
        rendered = _provider_attempt_component_name(provider_id, component_name)
        assert len(rendered) <= 64
        assert rendered[0].isalpha()
        assert all(
            character.islower() or character.isdigit() or character in "_-"
            for character in rendered
        )


@pytest.fixture(autouse=True)
def _clear_app_module():
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for key in to_remove:
        del sys.modules[key]
    yield
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for key in to_remove:
        del sys.modules[key]


def _import_app():
    old = os.environ.copy()
    env = _env_for_app()
    os.environ.update(env)
    for key in list(os.environ):
        if key not in env and key in (
            "RUNPOD_API_KEY",
            "DATABASE_URL",
            "REDIS_URL",
            "PITWALL_ADMIN_SECRET",
            "PITWALL_API_TOKEN",
            "PITWALL_INBOUND_RATE_LIMIT",
        ):
            del os.environ[key]
    try:
        return importlib.import_module("pitwall.api.app")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _setup_app_with_providers(providers: list[Provider]):
    mock_capability_repo = AsyncMock()
    mock_capability_repo.get_by_name.return_value = _make_capability()

    mock_provider_repo = AsyncMock()
    mock_provider_repo.list.return_value = providers

    app_mod = _import_app()
    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    app_mod.app.dependency_overrides[_capability_repo] = lambda: mock_capability_repo
    app_mod.app.dependency_overrides[_provider_repo] = lambda: mock_provider_repo
    budget_gate = AsyncMock()
    budget_gate.monthly_budget_usd = Decimal("100")
    budget_gate.per_request_max_usd = Decimal("10")
    budget_gate.effective_limits.return_value = BudgetLimits(
        Decimal("100"), Decimal("10"), "environment"
    )
    budget_gate.current_mtd_spend.return_value = Decimal("0")
    budget_gate.try_launch_admission.return_value = BudgetAdmission(
        workload_id="wkl_openai_fallback_test",
        is_new=True,
    )
    workload_repo = AsyncMock()
    workload_repo.guarded_transition.return_value = MagicMock()
    app_mod.app.dependency_overrides[_budget_gate] = lambda: budget_gate
    app_mod.app.dependency_overrides[_workload_repo] = lambda: workload_repo
    app_mod.app.state.pool = MagicMock()
    app_mod.app.state.test_budget_gate = budget_gate
    app_mod.app.state.test_workload_repo = workload_repo
    return app_mod


async def _post_chat(
    app_mod,
    *,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    if extra_headers is not None:
        headers.update(extra_headers)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        return await client.post(
            "/v1/openai/llm.qwen3-32b/v1/chat/completions",
            json={
                "model": "qwen3-32b-awq",
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers=headers,
        )


@respx.mock
@pytest.mark.anyio
async def test_primary_503_falls_through_inside_five_seconds():
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_secondary", "prov_tertiary"],
    )
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    tertiary = _make_provider(id="prov_tertiary", endpoint_id="tertiary", priority=3)
    app_mod = _setup_app_with_providers([primary, secondary, tertiary])

    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "primary unavailable"})
    )
    secondary_call = respx.post(
        "https://api.runpod.ai/v2/secondary/openai/v1/chat/completions"
    ).mock(return_value=httpx.Response(200, json={"id": "chatcmpl-secondary"}))
    tertiary_call = respx.post("https://api.runpod.ai/v2/tertiary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-tertiary"})
    )

    trace = MagicMock()
    trace.trace_id = "trace-fallback-success"
    with patch("pitwall.api.routes.openai.start_inference_trace", return_value=trace):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl-secondary"
    assert primary_call.called
    assert secondary_call.called
    assert not tertiary_call.called
    admission_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admission_quote.estimate() == Decimal("0.001000")
    assert admission_quote.upper_bound() == Decimal("0.003000")
    assert [
        component["name"] for component in admission_quote.to_serializable_dict()["components"]
    ] == [
        "pa_prov_p_9790f4e684f734531fee3f074ce283_reques_ec72420df5dfbdce",
        "pa_prov_s_4db331fc2278437e102b3349657d98_reques_ec72420df5dfbdce",
        "pa_prov_t_8a8e6485ff4cbc97de82228d5b3e8a_reques_ec72420df5dfbdce",
    ]
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "completed"
    )
    assert completion.kwargs["patch"]["provider_id"] == "prov_secondary"
    assert completion.kwargs["patch"]["langfuse_trace_id"] == "trace-fallback-success"
    assert "cost_actual_usd" not in completion.kwargs["patch"]
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.002000")
    assert completion.kwargs["patch"]["cost_quote"]["ceiling"] == "0.002000"
    assert len(completion.kwargs["patch"]["cost_quote"]["components"]) == 2

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_skip_primary_drill_preserves_all_remaining_fallback_attempts() -> None:
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_secondary", "prov_tertiary"],
    )
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    tertiary = _make_provider(id="prov_tertiary", endpoint_id="tertiary", priority=3)
    app_mod = _setup_app_with_providers([primary, secondary, tertiary])

    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "must-be-skipped"})
    )
    secondary_call = respx.post(
        "https://api.runpod.ai/v2/secondary/openai/v1/chat/completions"
    ).mock(return_value=httpx.Response(503, json={"error": "secondary unavailable"}))
    tertiary_call = respx.post("https://api.runpod.ai/v2/tertiary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-tertiary"})
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(
            app_mod,
            extra_headers={"x-pitwall-drill": "skip-primary"},
        )

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl-tertiary"
    assert not primary_call.called
    assert secondary_call.called
    assert tertiary_call.called
    admission_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admission_quote.route_plan.fallback_chain == ("prov_secondary", "prov_tertiary")
    assert admission_quote.upper_bound() == Decimal("0.002000")
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.COMPLETED
    )
    assert completion.kwargs["patch"]["provider_id"] == "prov_tertiary"
    assert completion.kwargs["patch"]["fallback_chain"] == [
        "prov_secondary",
        "prov_tertiary",
    ]
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.002000")

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_openai_plan_eliminates_unexecutable_prefix_before_admission() -> None:
    invalid = _make_provider(id="prov_invalid", endpoint_id="invalid", priority=1).model_copy(
        update={"runpod_endpoint_id": None, "config": {"per_request": "0.001000"}}
    )
    valid = _make_provider(id="prov_valid", endpoint_id="valid", priority=2)
    app_mod = _setup_app_with_providers([invalid, valid])
    valid_call = respx.post("https://api.runpod.ai/v2/valid/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-valid"})
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl-valid"
    assert valid_call.called
    admission_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admission_quote.route_plan.fallback_chain == ("prov_valid",)
    assert admission_quote.upper_bound() == Decimal("0.001000")
    assert [item.to_dict() for item in admission_quote.route_plan.eliminated] == [
        {
            "provider_id": "prov_invalid",
            "adapter_id": "runpod",
            "stage": "capability",
            "reason": "transport_unsupported",
        }
    ]
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.COMPLETED
    )
    assert completion.kwargs["patch"]["provider_id"] == "prov_valid"
    assert completion.kwargs["patch"]["fallback_chain"] == ["prov_valid"]

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_explicit_fallback_order_precedes_divergent_provider_priority() -> None:
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_tertiary"],
    )
    priority_second = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    explicit_fallback = _make_provider(id="prov_tertiary", endpoint_id="tertiary", priority=3)
    app_mod = _setup_app_with_providers([primary, priority_second, explicit_fallback])
    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "primary unavailable"})
    )
    secondary_call = respx.post(
        "https://api.runpod.ai/v2/secondary/openai/v1/chat/completions"
    ).mock(return_value=httpx.Response(200, json={"id": "must-not-run"}))
    tertiary_call = respx.post("https://api.runpod.ai/v2/tertiary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-explicit"})
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl-explicit"
    assert primary_call.called
    assert not secondary_call.called
    assert tertiary_call.called
    admission_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admission_quote.route_plan.fallback_chain == (
        "prov_primary",
        "prov_tertiary",
        "prov_secondary",
    )
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.COMPLETED
    )
    assert completion.kwargs["patch"]["provider_id"] == "prov_tertiary"
    assert completion.kwargs["patch"]["fallback_chain"] == [
        "prov_primary",
        "prov_tertiary",
    ]
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.002000")

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_lease_primary_reserves_paid_fallback_but_truths_up_zero_when_lease_wins():
    public_primary = _make_provider(
        id="prov_lease",
        endpoint_id="unused",
        priority=1,
        fallback_chain=["prov_paid"],
    )
    lease_primary = public_primary.model_copy(
        update={
            "provider_type": ProviderType.POD_LEASE,
            "runpod_endpoint_id": None,
            "config": {
                **public_primary.config,
                "active_pod_id": "pod-primary",
                "active_lease_id": "lease-primary",
                "openai_proxy_port": 8000,
            },
        }
    )
    paid_fallback = _make_provider(id="prov_paid", endpoint_id="paid", priority=2)
    app_mod = _setup_app_with_providers([lease_primary, paid_fallback])
    lease_call = respx.post("https://pod-primary-8000.proxy.runpod.net/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-lease"})
    )

    trace = MagicMock()
    trace.trace_id = "trace-lease-success"
    with patch("pitwall.api.routes.openai.start_inference_trace", return_value=trace):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert lease_call.called
    admission_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admission_quote.estimate() == Decimal("0")
    assert admission_quote.upper_bound() == Decimal("0.001000")
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "completed"
    )
    assert completion.kwargs["patch"]["provider_id"] == "prov_lease"
    assert completion.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert completion.kwargs["patch"]["cost_actual_provenance"] == "broker:lease_covered"
    assert "cost_reconciled_at" in completion.kwargs["patch"]
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0")
    assert completion.kwargs["patch"]["cost_quote"]["ceiling"] == "0"

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_pod_url_without_active_lease_remains_paid_on_success() -> None:
    public = _make_provider(id="prov_pod", endpoint_id="unused", priority=1)
    nonactive_pod = public.model_copy(
        update={
            "provider_type": ProviderType.POD_LEASE,
            "runpod_endpoint_id": None,
            "config": {
                **public.config,
                "active_pod_id": "pod-uncovered",
                "openai_proxy_port": 8000,
            },
        }
    )
    app_mod = _setup_app_with_providers([nonactive_pod])
    pod_call = respx.post("https://pod-uncovered-8000.proxy.runpod.net/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-paid-pod"})
    )

    trace = MagicMock()
    trace.trace_id = "trace-paid-pod"
    with patch("pitwall.api.routes.openai.start_inference_trace", return_value=trace):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert pod_call.called
    admitted_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admitted_quote.upper_bound() == Decimal("0.001000")
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.COMPLETED
    )
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.001000")
    assert "cost_actual_usd" not in completion.kwargs["patch"]

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_paid_attempt_before_lease_success_keeps_actual_unavailable() -> None:
    paid = _make_provider(
        id="prov_paid",
        endpoint_id="paid",
        priority=1,
        fallback_chain=["prov_lease"],
    )
    public_lease = _make_provider(id="prov_lease", endpoint_id="unused", priority=2)
    lease = public_lease.model_copy(
        update={
            "provider_type": ProviderType.POD_LEASE,
            "runpod_endpoint_id": None,
            "config": {
                **public_lease.config,
                "active_pod_id": "pod-fallback",
                "active_lease_id": "lease-fallback",
                "openai_proxy_port": 8000,
            },
        }
    )
    app_mod = _setup_app_with_providers([paid, lease])
    paid_call = respx.post("https://api.runpod.ai/v2/paid/openai/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "paid unavailable"})
    )
    lease_call = respx.post("https://pod-fallback-8000.proxy.runpod.net/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-lease-fallback"})
    )

    trace = MagicMock()
    trace.trace_id = "trace-paid-then-lease"
    with patch("pitwall.api.routes.openai.start_inference_trace", return_value=trace):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert paid_call.called
    assert lease_call.called
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.COMPLETED
    )
    assert completion.kwargs["patch"]["provider_id"] == "prov_lease"
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.001000")
    assert "cost_actual_usd" not in completion.kwargs["patch"]

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_lease_only_proxy_remains_routable_with_no_budget_headroom() -> None:
    public = _make_provider(id="prov_lease", endpoint_id="unused", priority=1)
    lease = public.model_copy(
        update={
            "provider_type": ProviderType.POD_LEASE,
            "runpod_endpoint_id": None,
            "config": {
                **public.config,
                "active_pod_id": "pod-primary",
                "active_lease_id": "lease-primary",
                "openai_proxy_port": 8000,
            },
        }
    )
    app_mod = _setup_app_with_providers([lease])
    budget = app_mod.app.state.test_budget_gate
    budget.monthly_budget_usd = Decimal("1")
    budget.current_mtd_spend.return_value = Decimal("1")
    workload_repo = app_mod.app.state.test_workload_repo
    workload_repo.insert.side_effect = lambda workload: workload
    lease_call = respx.post("https://pod-primary-8000.proxy.runpod.net/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-lease"})
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert lease_call.called
    budget.try_launch_admission.assert_not_awaited()
    completion = next(
        call
        for call in workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "completed"
    )
    assert completion.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0")

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_attempted_active_lease_failure_never_inherits_zero_actual() -> None:
    public = _make_provider(id="prov_lease", endpoint_id="unused", priority=1)
    lease = public.model_copy(
        update={
            "provider_type": ProviderType.POD_LEASE,
            "runpod_endpoint_id": None,
            "config": {
                **public.config,
                "active_pod_id": "pod-primary",
                "active_lease_id": "lease-primary",
                "openai_proxy_port": 8000,
            },
        }
    )
    app_mod = _setup_app_with_providers([lease])
    workload_repo = app_mod.app.state.test_workload_repo
    workload_repo.insert.side_effect = lambda workload: workload
    lease_call = respx.post("https://pod-primary-8000.proxy.runpod.net/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "lease unavailable"})
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace", return_value="trace-lease-fail"):
        response = await _post_chat(app_mod)

    assert response.status_code == 503
    assert lease_call.called
    inserted = workload_repo.insert.call_args.args[0]
    assert inserted.cost_actual_usd is None
    failure = next(
        call
        for call in workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert "cost_actual_usd" not in failure.kwargs["patch"]
    assert failure.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0")
    assert failure.kwargs["patch"]["langfuse_trace_id"] == "trace-lease-fail"

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_fallback_admission_rejects_provider_plan_mismatch() -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])

    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-primary"})
    )
    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert primary_call.called
    admitted_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    inactive_lease = primary.model_copy(
        update={"provider_type": ProviderType.POD_LEASE, "runpod_endpoint_id": None}
    )
    inactive_quote = type(admitted_quote)(
        admitted_quote.route_plan,
        (inactive_lease, secondary),
    )
    assert inactive_quote.upper_bound() == Decimal("0.002000")
    active_lease = inactive_lease.model_copy(
        update={
            "config": {
                **inactive_lease.config,
                "active_lease_id": "lease-active",
                "active_pod_id": "pod-active",
                "openai_proxy_port": 8000,
            }
        }
    )
    active_quote = type(admitted_quote)(
        admitted_quote.route_plan,
        (active_lease, secondary),
    )
    assert active_quote.upper_bound() == Decimal("0.001000")
    mismatched = type(admitted_quote)(admitted_quote.route_plan, (secondary,))
    with pytest.raises(ValueError, match="must match the planned attempt chain"):
        mismatched.upper_bound()

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_paid_primary_success_releases_unattempted_fallback_reservations() -> None:
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_secondary", "prov_tertiary"],
    )
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    tertiary = _make_provider(id="prov_tertiary", endpoint_id="tertiary", priority=3)
    app_mod = _setup_app_with_providers([primary, secondary, tertiary])
    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-primary"})
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert primary_call.called
    admission_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admission_quote.upper_bound() == Decimal("0.003000")
    completion = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "completed"
    )
    assert completion.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.001000")
    assert completion.kwargs["patch"]["cost_quote"]["ceiling"] == "0.001000"
    assert len(completion.kwargs["patch"]["cost_quote"]["components"]) == 1
    assert "cost_actual_usd" not in completion.kwargs["patch"]

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_cancellation_during_running_transition_releases_unattempted_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_secondary"],
    )
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])

    async def cancel_running(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("pitwall.api.routes.openai.transition_to_running", cancel_running)

    with (
        patch("pitwall.api.routes.openai.emit_inference_trace", return_value="trace-no-attempt"),
        pytest.raises(asyncio.CancelledError),
    ):
        await _post_chat(app_mod)

    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failure.kwargs["from_states"] == {
        WorkloadState.QUEUED,
        WorkloadState.RUNNING,
    }
    assert failure.kwargs["patch"]["fallback_chain"] is None
    assert failure.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert failure.kwargs["patch"]["cost_actual_provenance"] == ("broker:no_provider_invocation")
    assert failure.kwargs["patch"]["langfuse_trace_id"] == "trace-no-attempt"

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_executor_fallback_cancellation_attributes_last_attempted_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])

    async def cancel_fallback(
        _request: object,
        _providers: object,
        *,
        on_attempt: object = None,
    ) -> None:
        assert callable(on_attempt)
        on_attempt((primary.id, secondary.id))
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        cancel_fallback,
    )

    with (
        patch(
            "pitwall.api.routes.openai.emit_inference_trace",
            return_value="trace-fallback-cancel",
        ) as emit_trace,
        pytest.raises(asyncio.CancelledError),
    ):
        await _post_chat(app_mod)

    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.FAILED
    )
    assert failure.kwargs["patch"]["provider_id"] == "prov_secondary"
    assert failure.kwargs["patch"]["fallback_chain"] == ["prov_primary", "prov_secondary"]
    assert failure.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.002000")
    assert emit_trace.call_args.kwargs["provider_id"] == "prov_secondary"

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_upstream_client_construction_failure_releases_pre_egress_reservation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    execute = AsyncMock()

    def fail_client(_timeout: object) -> None:
        raise RuntimeError("client-construction-secret-canary")

    monkeypatch.setattr("pitwall.api.routes.openai._new_upstream_client", fail_client)
    monkeypatch.setattr("pitwall.api.routes.openai.execute_openai_with_fallback", execute)

    with patch(
        "pitwall.api.routes.openai.emit_inference_trace",
        return_value="trace-client-construction-failed",
    ):
        response = await _post_chat(app_mod)

    assert response.status_code == 503
    assert response.json() == {
        "error": "no_providers_available",
        "capability": "llm.qwen3-32b",
        "chain": [],
    }
    execute.assert_not_awaited()
    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.FAILED
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "upstream_client_initialization_failed",
        "attempted_providers": [],
    }
    assert failure.kwargs["patch"]["fallback_chain"] is None
    assert failure.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert failure.kwargs["patch"]["cost_actual_provenance"] == "broker:no_provider_invocation"
    assert "client-construction-secret-canary" not in str(failure.kwargs["patch"])
    assert "client-construction-secret-canary" not in caplog.text

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_upstream_client_construction_cancellation_records_no_attempt_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    execute = AsyncMock()

    def cancel_client(_timeout: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("pitwall.api.routes.openai._new_upstream_client", cancel_client)
    monkeypatch.setattr("pitwall.api.routes.openai.execute_openai_with_fallback", execute)

    with (
        patch(
            "pitwall.api.routes.openai.emit_inference_trace",
            return_value="trace-client-construction-cancelled",
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await _post_chat(app_mod)

    execute.assert_not_awaited()
    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.FAILED
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "provider_call_cancelled",
        "attempted_providers": [],
    }
    assert failure.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert failure.kwargs["patch"]["cost_actual_provenance"] == "broker:no_provider_invocation"

    app_mod.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "running_result",
    [None, RuntimeError("ledger unavailable")],
    ids=["guard-rejected", "transition-raised"],
)
@pytest.mark.anyio
async def test_unproven_running_transition_blocks_provider_egress_and_releases_reservation(
    monkeypatch: pytest.MonkeyPatch,
    running_result: object,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    workload_repo = app_mod.app.state.test_workload_repo
    workload_repo.guarded_transition.side_effect = [running_result, MagicMock()]
    execute = AsyncMock()
    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        execute,
    )

    with patch(
        "pitwall.api.routes.openai.emit_inference_trace",
        return_value="trace-running-failure",
    ):
        response = await _post_chat(app_mod)

    assert response.status_code == 503
    assert response.json() == {
        "error": "no_providers_available",
        "capability": "llm.qwen3-32b",
        "chain": [],
    }
    execute.assert_not_awaited()
    failure = workload_repo.guarded_transition.await_args_list[1]
    assert failure.kwargs["from_states"] == {
        WorkloadState.QUEUED,
        WorkloadState.RUNNING,
    }
    assert failure.kwargs["to_state"] == WorkloadState.FAILED
    assert failure.kwargs["patch"]["error"] == {
        "error": "workload_running_transition_failed",
        "attempted_providers": [],
    }
    assert failure.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert failure.kwargs["patch"]["cost_actual_provenance"] == ("broker:no_provider_invocation")
    assert failure.kwargs["patch"]["langfuse_trace_id"] == "trace-running-failure"

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_cancellation_during_cold_start_telemetry_releases_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])

    async def cancel_warming(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    profile = MagicMock()
    profile.cold_start_timeout_s = 600
    monkeypatch.setattr("pitwall.api.routes.openai.self_hosted_profile", lambda _provider: profile)
    monkeypatch.setattr("pitwall.api.routes.openai._model_is_resident", lambda *_args: False)
    monkeypatch.setattr("pitwall.api.routes.openai._record_upstream_outcome", cancel_warming)

    with (
        patch("pitwall.api.routes.openai.emit_inference_trace", return_value="trace-warming"),
        pytest.raises(asyncio.CancelledError),
    ):
        await _post_chat(app_mod)

    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failure.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert failure.kwargs["patch"]["cost_actual_provenance"] == ("broker:no_provider_invocation")
    assert failure.kwargs["patch"]["langfuse_trace_id"] == "trace-warming"

    app_mod.app.dependency_overrides.clear()


@pytest.mark.parametrize("attempted", [False, True], ids=["no-attempt", "attempted"])
@pytest.mark.anyio
async def test_executor_cancellation_with_close_failure_preserves_terminal_truth(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    attempted: bool,
) -> None:
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_secondary"],
    )
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])
    upstream_client = AsyncMock(spec=httpx.AsyncClient)
    upstream_client.aclose.side_effect = RuntimeError("close-secret-canary")

    async def cancel_executor(
        _request: object,
        _providers: object,
        *,
        on_attempt: object = None,
    ) -> None:
        assert on_attempt is not None
        assert callable(on_attempt)
        if attempted:
            on_attempt((primary.id,))
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        cancel_executor,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda _timeout: upstream_client,
    )

    with pytest.raises(asyncio.CancelledError):
        await _post_chat(app_mod)

    upstream_client.aclose.assert_awaited_once()
    failed = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failed.kwargs["patch"]["error"] == {
        "error": "provider_call_cancelled",
        "attempted_providers": ["prov_primary"] if attempted else [],
    }
    if attempted:
        assert failed.kwargs["patch"]["fallback_chain"] == ["prov_primary"]
        assert failed.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.001000")
        assert failed.kwargs["patch"]["cost_quote"]["ceiling"] == "0.001000"
        assert "cost_actual_usd" not in failed.kwargs["patch"]
    else:
        assert failed.kwargs["patch"]["fallback_chain"] is None
        assert failed.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
        assert failed.kwargs["patch"]["cost_actual_provenance"] == ("broker:no_provider_invocation")
    assert "close-secret-canary" not in caplog.text

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_post_headers_cancellation_closes_upstream_and_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    upstream = httpx.Response(
        200,
        request=httpx.Request("POST", "https://upstream.example/v1/chat/completions"),
        json={"id": "chatcmpl-cancelled"},
    )
    upstream_client = AsyncMock(spec=httpx.AsyncClient)

    async def return_headers(
        _request: object,
        _providers: object,
        *,
        on_attempt: object = None,
    ) -> OpenAIProxyResult:
        assert callable(on_attempt)
        on_attempt((primary.id,))
        return OpenAIProxyResult(
            response=upstream,
            provider=primary,
            attempted_provider_ids=(primary.id,),
            elapsed_s=0.01,
        )

    async def cancel_during_telemetry(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        return_headers,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda _timeout: upstream_client,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._record_self_hosted_concurrency",
        cancel_during_telemetry,
    )

    with pytest.raises(asyncio.CancelledError):
        await _post_chat(app_mod)

    assert upstream.is_closed
    upstream_client.aclose.assert_awaited_once()
    failed = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failed.kwargs["patch"]["fallback_chain"] == ["prov_primary"]
    assert "cost_actual_usd" not in failed.kwargs["patch"]

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_body_relay_cancellation_closes_upstream_and_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])
    stream = _BlockingResponseStream()
    upstream = httpx.Response(
        200,
        request=httpx.Request("POST", "https://upstream.example/v1/chat/completions"),
        headers={"content-type": "text/event-stream"},
        stream=stream,
    )
    upstream_client = AsyncMock(spec=httpx.AsyncClient)

    async def return_stream(
        _request: object,
        _providers: object,
        *,
        on_attempt: object = None,
    ) -> OpenAIProxyResult:
        assert callable(on_attempt)
        on_attempt((primary.id, secondary.id))
        return OpenAIProxyResult(
            response=upstream,
            provider=secondary,
            attempted_provider_ids=(primary.id, secondary.id),
            elapsed_s=0.01,
        )

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        return_stream,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda _timeout: upstream_client,
    )

    trace = MagicMock()
    trace.trace_id = "trace-body-cancel"
    with patch("pitwall.api.routes.openai.start_inference_trace", return_value=trace):
        request_task = asyncio.create_task(_post_chat(app_mod))
        try:
            await _wait_for_stream_ready(request_task, stream.waiting)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
        finally:
            # Readiness failure must not leak a request into later test teardown.
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)

    assert stream.closed
    assert upstream.is_closed
    upstream_client.aclose.assert_awaited_once()
    terminal_calls = [
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value in {"completed", "failed"}
    ]
    assert len(terminal_calls) == 1
    assert terminal_calls[0].kwargs["to_state"].value == "failed"
    assert terminal_calls[0].kwargs["patch"]["error"] == {
        "error": "provider_stream_cancelled",
        "attempted_providers": ["prov_primary", "prov_secondary"],
    }
    assert terminal_calls[0].kwargs["patch"]["provider_id"] == "prov_secondary"
    assert terminal_calls[0].kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.002000")
    assert "cost_actual_usd" not in terminal_calls[0].kwargs["patch"]
    assert terminal_calls[0].kwargs["patch"]["langfuse_trace_id"] == "trace-body-cancel"
    assert terminal_calls[0].kwargs["patch"]["output_bytes"] > 0
    assert trace.finish.call_args.kwargs["status"] == "error"
    assert trace.finish.call_args.kwargs["output_bytes"] > 0

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_body_relay_disconnect_runs_terminal_callback_before_response_ends() -> None:
    """A Starlette downstream disconnect must finish proxy terminal bookkeeping."""

    from pitwall.api.routes.openai import _relay_upstream_bytes

    stream = _BlockingResponseStream()
    upstream = httpx.Response(
        200,
        request=httpx.Request("POST", "https://upstream.example/v1/chat/completions"),
        headers={"content-type": "text/event-stream"},
        stream=stream,
    )
    upstream_client = AsyncMock(spec=httpx.AsyncClient)
    interrupted: list[tuple[str, int]] = []

    async def on_interrupted(reason: str, delivered_bytes: int) -> None:
        await asyncio.sleep(0)
        interrupted.append((reason, delivered_bytes))

    response = StreamingResponse(
        _relay_upstream_bytes(
            upstream,
            upstream_client,
            on_interrupted=on_interrupted,
        ),
        media_type="text/event-stream",
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream",
        "raw_path": b"/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("test", 1234),
        "server": ("test", 80),
    }
    first_receive = True
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        nonlocal first_receive
        if first_receive:
            first_receive = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await stream.waiting.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await response(scope, receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert stream.closed
    assert upstream.is_closed
    upstream_client.aclose.assert_awaited_once()
    assert interrupted == [("provider_stream_cancelled", len(b'data: {"delta":"started"}\n\n'))]


@pytest.mark.anyio
async def test_openai_proxy_asgi_disconnect_records_failed_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual proxy ASGI path must terminalize a workload on disconnect."""

    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    stream = _BlockingResponseStream()
    upstream = httpx.Response(
        200,
        request=httpx.Request("POST", "https://upstream.example/v1/chat/completions"),
        headers={"content-type": "text/event-stream"},
        stream=stream,
    )
    upstream_client = AsyncMock(spec=httpx.AsyncClient)

    async def return_stream(
        _request: object,
        _providers: object,
        *,
        on_attempt: object = None,
    ) -> OpenAIProxyResult:
        assert callable(on_attempt)
        on_attempt((primary.id,))
        return OpenAIProxyResult(
            response=upstream,
            provider=primary,
            attempted_provider_ids=(primary.id,),
            elapsed_s=0.01,
        )

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        return_stream,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda _timeout: upstream_client,
    )
    terminalized = asyncio.Event()

    async def record_transition(*args: object, **kwargs: object) -> MagicMock:
        await asyncio.sleep(0)
        if getattr(kwargs.get("to_state"), "value", None) == "failed":
            terminalized.set()
        return MagicMock()

    app_mod.app.state.test_workload_repo.guarded_transition.side_effect = record_transition
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/openai/llm.qwen3-32b/v1/chat/completions",
        "raw_path": b"/v1/openai/llm.qwen3-32b/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("test", 1234),
        "server": ("test", 80),
    }
    request_sent = False
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": b'{"model":"qwen3-32b-awq","messages":[]}',
                "more_body": False,
            }
        await stream.waiting.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await app_mod.app(scope, receive, send)

    assert terminalized.is_set()
    assert sent[0]["type"] == "http.response.start"
    assert stream.closed
    assert upstream.is_closed
    upstream_client.aclose.assert_awaited_once()
    failed = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failed.kwargs["patch"]["error"] == {
        "error": "provider_stream_cancelled",
        "attempted_providers": ["prov_primary"],
    }
    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_body_relay_failure_records_failed_trace_and_safe_audit(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])
    stream = _FailingResponseStream()
    upstream = httpx.Response(
        200,
        request=httpx.Request("POST", "https://upstream.example/v1/chat/completions"),
        headers={"content-type": "text/event-stream"},
        stream=stream,
    )
    upstream_client = AsyncMock(spec=httpx.AsyncClient)

    async def return_stream(
        _request: object,
        _providers: object,
        *,
        on_attempt: object = None,
    ) -> OpenAIProxyResult:
        assert callable(on_attempt)
        on_attempt((primary.id, secondary.id))
        return OpenAIProxyResult(
            response=upstream,
            provider=secondary,
            attempted_provider_ids=(primary.id, secondary.id),
            elapsed_s=0.01,
        )

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        return_stream,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda _timeout: upstream_client,
    )
    trace = MagicMock()
    trace.trace_id = "trace-body-failure"

    with patch("pitwall.api.routes.openai.start_inference_trace", return_value=trace):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert "upstream stream failure" in response.text
    assert stream.closed
    upstream_client.aclose.assert_awaited_once()
    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "provider_stream_failed",
        "attempted_providers": ["prov_primary", "prov_secondary"],
    }
    assert failure.kwargs["patch"]["provider_id"] == "prov_secondary"
    assert failure.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.002000")
    assert failure.kwargs["patch"]["langfuse_trace_id"] == "trace-body-failure"
    assert failure.kwargs["patch"]["output_bytes"] > 0
    assert "stream-secret-canary" not in str(failure.kwargs["patch"])
    assert "stream-secret-canary" not in caplog.text
    assert trace.finish.call_args.kwargs["status"] == "error"
    assert trace.finish.call_args.kwargs["output_bytes"] > 0

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_client_error_body_read_failure_records_stable_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    stream = _FailingResponseStream()
    upstream = httpx.Response(
        400,
        request=httpx.Request("POST", "https://upstream.example/v1/chat/completions"),
        headers={"content-type": "application/json"},
        stream=stream,
    )
    upstream_client = AsyncMock(spec=httpx.AsyncClient)

    async def return_stream(
        _request: object,
        _providers: object,
        *,
        on_attempt: object = None,
    ) -> OpenAIProxyResult:
        assert callable(on_attempt)
        on_attempt((primary.id,))
        return OpenAIProxyResult(
            response=upstream,
            provider=primary,
            attempted_provider_ids=(primary.id,),
            elapsed_s=0.01,
        )

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        return_stream,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda _timeout: upstream_client,
    )

    with patch(
        "pitwall.api.routes.openai.emit_inference_trace",
        return_value="trace-response-read-failure",
    ):
        response = await _post_chat(app_mod)

    assert response.status_code == 503
    assert response.json() == {
        "error": "no_providers_available",
        "capability": "llm.qwen3-32b",
        "chain": ["prov_primary"],
    }
    assert stream.closed
    assert upstream.is_closed
    assert upstream_client.aclose.await_count >= 1
    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "provider_response_read_failed",
        "attempted_providers": ["prov_primary"],
    }
    assert failure.kwargs["patch"]["fallback_chain"] == ["prov_primary"]
    assert failure.kwargs["patch"]["langfuse_trace_id"] == "trace-response-read-failure"
    assert failure.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.001000")
    assert "cost_actual_usd" not in failure.kwargs["patch"]
    assert "stream-secret-canary" not in str(failure.kwargs["patch"])
    assert "stream-secret-canary" not in caplog.text

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_primary_401_does_not_fallback():
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_secondary"],
    )
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])

    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    secondary_call = respx.post(
        "https://api.runpod.ai/v2/secondary/openai/v1/chat/completions"
    ).mock(return_value=httpx.Response(200, json={"id": "should-not-run"}))

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    assert primary_call.called
    assert not secondary_call.called

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_attempt_chain_capped_at_three(monkeypatch: pytest.MonkeyPatch):
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_second", "prov_third", "prov_fourth"],
    )
    second = _make_provider(id="prov_second", endpoint_id="second", priority=2)
    third = _make_provider(id="prov_third", endpoint_id="third", priority=3)
    fourth = _make_provider(id="prov_fourth", endpoint_id="fourth", priority=4)
    app_mod = _setup_app_with_providers([primary, second, third, fourth])
    monkeypatch.setattr(
        "pitwall.api.routes.openai.get_settings",
        lambda: PitwallSettings(pitwall_routing_max_attempts=5),
    )

    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"provider": "primary"})
    )
    second_call = respx.post("https://api.runpod.ai/v2/second/openai/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"provider": "second"})
    )
    third_call = respx.post("https://api.runpod.ai/v2/third/openai/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"provider": "third"})
    )
    fourth_call = respx.post("https://api.runpod.ai/v2/fourth/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"provider": "fourth"})
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 503
    assert response.json()["provider"] == "third"
    assert primary_call.called
    assert second_call.called
    assert third_call.called
    assert not fourth_call.called
    admission_quote = app_mod.app.state.test_budget_gate.try_launch_admission.call_args.kwargs[
        "estimate_usd"
    ]
    assert admission_quote.upper_bound() == Decimal("0.003000")
    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "provider_http_error",
        "status_code": 503,
        "attempted_providers": ["prov_primary", "prov_second", "prov_third"],
    }
    assert failure.kwargs["patch"]["fallback_chain"] == [
        "prov_primary",
        "prov_second",
        "prov_third",
    ]
    assert failure.kwargs["patch"]["provider_id"] == "prov_third"
    assert failure.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.003000")
    assert failure.kwargs["patch"]["cost_quote"]["ceiling"] == "0.003000"
    assert "cost_actual_usd" not in failure.kwargs["patch"]
    assert not any(
        call.kwargs["to_state"].value == "completed"
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
    )

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_transport_failure_retries_with_same_body():
    primary = _make_provider(
        id="prov_primary",
        endpoint_id="primary",
        priority=1,
        fallback_chain=["prov_secondary"],
    )
    secondary = _make_provider(id="prov_secondary", endpoint_id="secondary", priority=2)
    app_mod = _setup_app_with_providers([primary, secondary])
    seen_bodies: list[bytes] = []

    def primary_handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(request.content)
        raise httpx.ConnectError("dial failed", request=request)

    def secondary_handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(request.content)
        return httpx.Response(200, json={"id": "chatcmpl-secondary"})

    primary_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        side_effect=primary_handler
    )
    secondary_call = respx.post(
        "https://api.runpod.ai/v2/secondary/openai/v1/chat/completions"
    ).mock(side_effect=secondary_handler)

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        response = await _post_chat(app_mod)

    assert response.status_code == 200
    assert response.json()["id"] == "chatcmpl-secondary"
    assert primary_call.called
    assert secondary_call.called
    assert len(seen_bodies) == 2
    assert seen_bodies[0] == seen_bodies[1]

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_exhausted_transport_health_failure_does_not_mask_terminal_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    upstream_call = respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("transport-secret-canary")
    )

    async def health_failure(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("health-secret-canary")

    monkeypatch.setattr("pitwall.api.routes.openai._record_upstream_outcome", health_failure)

    with patch("pitwall.api.routes.openai.emit_inference_trace", return_value="trace-transport"):
        response = await _post_chat(app_mod)

    assert response.status_code == 503
    assert upstream_call.called
    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "provider_transport_error",
        "attempted_providers": ["prov_primary"],
        "attempted_failures": {"prov_primary": "transport_error"},
    }
    assert failure.kwargs["patch"]["langfuse_trace_id"] == "trace-transport"
    assert "secret-canary" not in str(failure.kwargs["patch"])

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_exhausted_transport_client_close_failure_does_not_mask_terminal_record(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    upstream_client = AsyncMock(spec=httpx.AsyncClient)
    upstream_client.aclose.side_effect = RuntimeError("close-secret-canary")

    async def fail_transport(*_args: object, **_kwargs: object) -> None:
        raise OpenAIProxyExecutionError(
            "transport-secret-canary",
            attempted_provider_ids=(primary.id,),
            cause=httpx.ConnectError("transport-secret-canary"),
            attempted_errors={primary.id: "transport-secret-canary"},
        )

    monkeypatch.setattr(
        "pitwall.api.routes.openai.execute_openai_with_fallback",
        fail_transport,
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda _timeout: upstream_client,
    )

    with patch(
        "pitwall.api.routes.openai.emit_inference_trace",
        return_value="trace-close-failure",
    ):
        response = await _post_chat(app_mod)

    assert response.status_code == 503
    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.FAILED
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "provider_transport_error",
        "attempted_providers": ["prov_primary"],
        "attempted_failures": {"prov_primary": "transport_error"},
    }
    assert "secret-canary" not in str(failure.kwargs["patch"])
    assert "secret-canary" not in caplog.text

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_cancellation_during_exhausted_transport_health_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = _make_provider(id="prov_primary", endpoint_id="primary", priority=1)
    app_mod = _setup_app_with_providers([primary])
    respx.post("https://api.runpod.ai/v2/primary/openai/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("transport failed")
    )

    async def cancel_health(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("pitwall.api.routes.openai._record_upstream_outcome", cancel_health)

    with (
        patch("pitwall.api.routes.openai.emit_inference_trace", return_value="trace-cancelled"),
        pytest.raises(asyncio.CancelledError),
    ):
        await _post_chat(app_mod)

    failure = next(
        call
        for call in app_mod.app.state.test_workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"].value == "failed"
    )
    assert failure.kwargs["patch"]["error"] == {
        "error": "provider_call_cancelled",
        "attempted_providers": ["prov_primary"],
    }
    assert failure.kwargs["patch"]["cost_ceiling_usd"] == Decimal("0.001000")
    assert "cost_actual_usd" not in failure.kwargs["patch"]

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("exception", [False, True])
async def test_stream_readiness_reports_early_request_failure(exception: bool) -> None:
    async def early_failure() -> httpx.Response:
        if exception:
            raise RuntimeError("request failed before relay")
        return httpx.Response(503)

    task = asyncio.create_task(early_failure())
    error = RuntimeError if exception else AssertionError
    message = "request failed before relay" if exception else "HTTP 503"
    with pytest.raises(error, match=message):
        await _wait_for_stream_ready(task, asyncio.Event())
    assert task.done()


@pytest.mark.anyio
async def test_stream_readiness_timeout_retains_bound_and_request_diagnostic() -> None:
    async def stuck_request() -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    task = asyncio.create_task(stuck_request())
    before = set(asyncio.all_tasks())
    try:
        with pytest.raises(TimeoutError, match="request pending at stuck_request"):
            await _wait_for_stream_ready(task, asyncio.Event(), timeout=0.01)
        assert not task.done()
        assert set(asyncio.all_tasks()) == before  # No leaked event-wait task.
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_body_readiness_failure_cancels_owned_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = asyncio.Event()

    async def stalled_request(_app_mod: object) -> httpx.Response:
        try:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        finally:
            stopped.set()

    monkeypatch.setattr(sys.modules[__name__], "_post_chat", stalled_request)
    try:
        with pytest.raises(TimeoutError):
            await test_body_relay_cancellation_closes_upstream_and_records_failure(monkeypatch)
        assert stopped.is_set(), "readiness failure leaked its owned request"
    finally:
        # Keep the negative regression hermetic even when exercised against the old test.
        for task in asyncio.all_tasks():
            if getattr(task.get_coro(), "__name__", "") == "stalled_request":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
