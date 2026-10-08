"""Task 5 D6(b) — non-pod mutations are budget-gated and recorded; reads are neither.

Every non-pod mutating tool (update/action/delete/terminate paths) must run
the budget admission check before provider I/O and must wire the audit pool
so the control-plane service records the mutation in ``config_audit`` (no new
table — the existing audit write proved suitable). Read-only tools must do
neither. ``pitwall_runpod_create_pod`` is excluded here: Task 4 gave it its
own TTL-based admission (covered by ``test_runpod_create_pod_lease.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import pytest
from mcp.shared.exceptions import MCPError

from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.mcp.tools import runpod_resources
from pitwall.runpod_control_plane import (
    EndpointCreateRequest,
    EndpointGpuRequest,
    EndpointScalingRequest,
    EndpointUpdateRequest,
    EndpointWorkersRequest,
    IdentifiedMutationRequest,
    MutationResult,
    PodActionRequest,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthReplaceRequest,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
)

pytestmark = pytest.mark.anyio

_KEY = "mcp-gate-0001"


def _mutation_result(*, dry_run: bool = False) -> MutationResult:
    return MutationResult(
        operation="test.mutation",
        resource_type="test",
        resource_id="res_test123",
        dry_run=dry_run,
        changed=True,
        effect="applied in test",
        idempotency_key=_KEY,
        resource={"id": "res_test123"},
    )


def _apply_requests() -> list[Any]:
    """One valid apply-intent request per non-pod mutating tool (15)."""
    identified = lambda rid: {"intent": "apply", "idempotency_key": _KEY, "resource_id": rid}  # noqa: E731  # reason: inline one-line helper local to this test; a def would add lines without changing behaviour
    return [
        PodUpdateRequest(**identified("pod12345"), env={"MODE": "test"}),
        PodActionRequest(**identified("pod12345"), action="restart"),
        IdentifiedMutationRequest(**identified("pod12345")),
        EndpointCreateRequest(
            intent="apply",
            idempotency_key=_KEY,
            name="gate-ep",
            image="example/image:1",
            gpu=EndpointGpuRequest(pools=["pool-a"]),
        ),
        EndpointUpdateRequest(
            **identified("ep123456"),
            workers=EndpointWorkersRequest(),
            scaling=EndpointScalingRequest(),
        ),
        IdentifiedMutationRequest(**identified("ep123456")),
        TemplateCreateRequest(
            intent="apply", idempotency_key=_KEY, name="gate-tmpl", image="example/image:1"
        ),
        TemplateUpdateRequest(**identified("tmpl1234"), name="gate-tmpl-2"),
        IdentifiedMutationRequest(**identified("tmpl1234")),
        VolumeCreateRequest(
            intent="apply",
            idempotency_key=_KEY,
            name="gate-vol",
            size_gb=10,
            data_center_id="dc123456",
        ),
        VolumeGrowRequest(**identified("vol12345"), size_gb=20),
        IdentifiedMutationRequest(**identified("vol12345")),
        RegistryAuthCreateRequest(
            intent="apply",
            idempotency_key=_KEY,
            name="gate-reg",
            username="user",
            password_env="GATE_TEST_PASSWORD",
        ),
        RegistryAuthReplaceRequest(
            **identified("reg12345"),
            name="gate-reg",
            username="user",
            password_env="GATE_TEST_PASSWORD",
        ),
        IdentifiedMutationRequest(**identified("reg12345")),
    ]


_APPLY_HANDLERS: list[Callable[..., Awaitable[dict[str, Any]]]] = [
    runpod_resources.pitwall_runpod_update_pod,
    runpod_resources.pitwall_runpod_action_pod,
    runpod_resources.pitwall_runpod_terminate_pod,
    runpod_resources.pitwall_runpod_create_endpoint,
    runpod_resources.pitwall_runpod_update_endpoint,
    runpod_resources.pitwall_runpod_delete_endpoint,
    runpod_resources.pitwall_runpod_create_template,
    runpod_resources.pitwall_runpod_update_template,
    runpod_resources.pitwall_runpod_delete_template,
    runpod_resources.pitwall_runpod_create_volume,
    runpod_resources.pitwall_runpod_grow_volume,
    runpod_resources.pitwall_runpod_delete_volume,
    runpod_resources.pitwall_runpod_create_registry_auth,
    runpod_resources.pitwall_runpod_replace_registry_auth,
    runpod_resources.pitwall_runpod_delete_registry_auth,
]

_READ_HANDLERS: list[Callable[..., Awaitable[dict[str, Any]]]] = [
    lambda: runpod_resources.pitwall_runpod_list_pods(),
    lambda: runpod_resources.pitwall_runpod_get_pod("pod12345"),
    lambda: runpod_resources.pitwall_runpod_list_endpoints(),
    lambda: runpod_resources.pitwall_runpod_get_endpoint("ep123456"),
    lambda: runpod_resources.pitwall_runpod_list_templates(),
    lambda: runpod_resources.pitwall_runpod_get_template("tmpl1234"),
    lambda: runpod_resources.pitwall_runpod_list_volumes(),
    lambda: runpod_resources.pitwall_runpod_get_volume("vol12345"),
    lambda: runpod_resources.pitwall_runpod_list_registry_auths(),
    lambda: runpod_resources.pitwall_runpod_get_registry_auth("reg12345"),
    lambda: runpod_resources.pitwall_runpod_list_hub_templates(),
    lambda: runpod_resources.pitwall_runpod_get_hub_template("hub123456"),
    lambda: runpod_resources.pitwall_runpod_search_hub_templates("gate"),
]


class _TrackingService:
    """Generic stub: every method returns a canned MutationResult; calls tracked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        if name.startswith("_"):
            raise AttributeError(name)

        async def _method(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            request = args[0] if args else None
            dry_run = bool(getattr(request, "dry_run", False))
            if name.startswith(("list_", "search_")):
                return []
            return _mutation_result(dry_run=dry_run)

        return _method


def _patch_hermetic(
    monkeypatch: pytest.MonkeyPatch,
    service: _TrackingService,
    *,
    admit_calls: list[Any],
    reject: bool = False,
) -> list[bool]:
    service_kinds: list[bool] = []

    async def fake_service(*, mutation: bool = False) -> Any:
        service_kinds.append(mutation)
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return object()

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    async def fake_admit(pool: Any, **kwargs: Any) -> None:
        admit_calls.append(pool)
        if reject:
            raise BudgetRejected(
                "monthly_budget",
                BudgetSnapshot(
                    monthly_budget_usd=Decimal("10"),
                    per_request_max_usd=Decimal("5"),
                    mtd_spend_usd=Decimal("9"),
                    estimate_usd=Decimal("5"),
                    budget_remaining_usd=Decimal("1"),
                ),
            )

    monkeypatch.setattr(runpod_resources, "admit_resource_mutation", fake_admit)
    return service_kinds


_SPEND_REDUCING = {
    "pitwall_runpod_terminate_pod",
    "pitwall_runpod_delete_endpoint",
    "pitwall_runpod_delete_template",
    "pitwall_runpod_delete_volume",
    "pitwall_runpod_delete_registry_auth",
}


async def test_all_fifteen_non_pod_mutations_are_audited_and_spend_creating_ones_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every non-pod apply mutation wires the audit pool; the spend-creating ones admit budget first."""
    assert len(_APPLY_HANDLERS) == 15
    requests = _apply_requests()
    assert len(requests) == 15

    for handler, request in zip(_APPLY_HANDLERS, requests, strict=True):
        service = _TrackingService()
        admit_calls: list[Any] = []
        service_kinds = _patch_hermetic(monkeypatch, service, admit_calls=admit_calls)

        await handler(request)

        expected_admissions = 0 if handler.__name__ in _SPEND_REDUCING else 1
        assert len(admit_calls) == expected_admissions, (
            f"{handler.__name__} budget admissions: {len(admit_calls)}, want {expected_admissions}"
        )
        assert service_kinds == [True], (
            f"{handler.__name__} must wire the audit pool (mutation=True) so the "
            f"service records the mutation; got {service_kinds}"
        )
        assert service.calls, f"{handler.__name__} never reached the provider service"


async def test_non_pod_mutation_over_budget_surfaces_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _TrackingService()
    admit_calls: list[Any] = []
    _patch_hermetic(monkeypatch, service, admit_calls=admit_calls, reject=True)

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_update_pod(
            PodUpdateRequest(
                intent="apply", idempotency_key=_KEY, resource_id="pod12345", env={"MODE": "x"}
            )
        )

    assert admit_calls, "budget gate was not consulted"
    assert service.calls == [], "provider was called despite budget rejection"
    assert excinfo.value.error.code == -31002
    assert (excinfo.value.error.data or {}).get("error") == "budget_rejected"


async def test_preview_mutations_skip_budget_and_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Previews validate only: no admission, no audit pool."""
    preview_requests = [
        PodUpdateRequest(
            intent="preview",
            idempotency_key=_KEY,
            resource_id="pod12345",
            env={"MODE": "test"},
        ),
        EndpointCreateRequest(
            intent="preview",
            idempotency_key=_KEY,
            name="gate-ep",
            image="example/image:1",
            gpu=EndpointGpuRequest(pools=["pool-a"]),
        ),
        VolumeGrowRequest(
            intent="preview", idempotency_key=_KEY, resource_id="vol12345", size_gb=20
        ),
    ]
    handlers = [
        runpod_resources.pitwall_runpod_update_pod,
        runpod_resources.pitwall_runpod_create_endpoint,
        runpod_resources.pitwall_runpod_grow_volume,
    ]
    for handler, request in zip(handlers, preview_requests, strict=True):
        service = _TrackingService()
        admit_calls: list[Any] = []
        service_kinds = _patch_hermetic(monkeypatch, service, admit_calls=admit_calls)

        result = await handler(request)

        assert admit_calls == [], f"{handler.__name__} preview must not admit budget"
        assert service_kinds == [False], f"{handler.__name__} preview must not wire audit"
        assert result["dry_run"] is True


async def test_read_only_tools_do_neither(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 13 read-only tools: no budget admission, no audit pool."""
    assert len(_READ_HANDLERS) == 13
    for thunk in _READ_HANDLERS:
        service = _TrackingService()
        admit_calls: list[Any] = []
        service_kinds = _patch_hermetic(monkeypatch, service, admit_calls=admit_calls)

        await thunk()

        assert admit_calls == [], "read-only tool must not admit budget"
        assert service_kinds == [False], "read-only tool must not wire audit"


def _spend_reducing_requests() -> list[tuple[Callable[..., Awaitable[dict[str, Any]]], Any]]:
    """Apply-intent requests whose only effect is to stop or remove billing."""
    identified = lambda rid: {"intent": "apply", "idempotency_key": _KEY, "resource_id": rid}  # noqa: E731  # reason: inline one-line helper local to this test; a def would add lines without changing behaviour
    return [
        (
            runpod_resources.pitwall_runpod_terminate_pod,
            IdentifiedMutationRequest(**identified("pod12345")),
        ),
        (
            runpod_resources.pitwall_runpod_action_pod,
            PodActionRequest(**identified("pod12345"), action="stop"),
        ),
        (
            runpod_resources.pitwall_runpod_delete_endpoint,
            IdentifiedMutationRequest(**identified("ep123456")),
        ),
        (
            runpod_resources.pitwall_runpod_delete_template,
            IdentifiedMutationRequest(**identified("tmpl1234")),
        ),
        (
            runpod_resources.pitwall_runpod_delete_volume,
            IdentifiedMutationRequest(**identified("vol12345")),
        ),
        (
            runpod_resources.pitwall_runpod_delete_registry_auth,
            IdentifiedMutationRequest(**identified("reg12345")),
        ),
    ]


async def test_exhausted_budget_never_blocks_spend_reducing_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: terminate, stop, and delete must run when the budget is gone; they are how spend stops."""
    for handler, request in _spend_reducing_requests():
        service = _TrackingService()
        admit_calls: list[Any] = []
        service_kinds = _patch_hermetic(monkeypatch, service, admit_calls=admit_calls, reject=True)

        await handler(request)

        assert admit_calls == [], f"{handler.__name__} consulted the budget gate"
        assert service.calls, f"{handler.__name__} never reached the provider service"
        assert service_kinds == [True], f"{handler.__name__} must still be audited"


async def test_exhausted_budget_still_blocks_spend_creating_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R1: the gate stays in front of everything that can add cost, including start/restart."""
    identified = lambda rid: {"intent": "apply", "idempotency_key": _KEY, "resource_id": rid}  # noqa: E731  # reason: inline one-line helper local to this test; a def would add lines without changing behaviour
    creating = [
        (
            runpod_resources.pitwall_runpod_action_pod,
            PodActionRequest(**identified("pod12345"), action="start"),
        ),
        (
            runpod_resources.pitwall_runpod_action_pod,
            PodActionRequest(**identified("pod12345"), action="restart"),
        ),
        (
            runpod_resources.pitwall_runpod_grow_volume,
            VolumeGrowRequest(**identified("vol12345"), size_gb=20),
        ),
        (
            runpod_resources.pitwall_runpod_create_endpoint,
            EndpointCreateRequest(
                intent="apply",
                idempotency_key=_KEY,
                name="gate-ep",
                image="example/image:1",
                gpu=EndpointGpuRequest(pools=["pool-a"]),
            ),
        ),
    ]
    for handler, request in creating:
        service = _TrackingService()
        admit_calls: list[Any] = []
        _patch_hermetic(monkeypatch, service, admit_calls=admit_calls, reject=True)

        with pytest.raises(MCPError) as excinfo:
            await handler(request)

        assert excinfo.value.error.code == -31002
        assert service.calls == [], f"{handler.__name__} reached the provider over budget"
