"""The one lease-creation path REST and MCP share: resolve, plan, launch, record the plan."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.core.enums import LeaseState
from tests.leases.test_teardown import _lease

pytestmark = pytest.mark.anyio


class _Plan:
    plan_id = "plan_routed"
    selected_provider_id = "prov_routed"

    def to_dict(self) -> dict[str, object]:
        return {"plan_id": self.plan_id, "selected_provider_id": self.selected_provider_id}


def _world(monkeypatch: pytest.MonkeyPatch, *, launch_result: dict[str, Any]) -> Any:
    launch_module = importlib.import_module("pitwall.api.leases.launch")
    cap = SimpleNamespace(id="cap_routed", name="llm.routed")
    provider = SimpleNamespace(id="prov_routed")
    stored = _lease(LeaseState.ACTIVE).model_copy(
        update={"provider_id": provider.id, "workload_id": "wkl_routed"}
    )
    world = SimpleNamespace(
        cap=cap,
        provider=provider,
        stored=stored,
        caps=SimpleNamespace(
            get_by_name=AsyncMock(return_value=None), get=AsyncMock(return_value=cap)
        ),
        providers=SimpleNamespace(get=AsyncMock(return_value=provider)),
        leases=SimpleNamespace(
            get=AsyncMock(return_value=stored), get_by_workload=AsyncMock(return_value=stored)
        ),
        workloads=SimpleNamespace(
            attach_route_plan=AsyncMock(),
            get_by_idempotency_key=AsyncMock(return_value=None),
            get=AsyncMock(return_value=None),
        ),
        routing=SimpleNamespace(
            preview=AsyncMock(return_value=_Plan()),
            plan_execution=AsyncMock(return_value=(_Plan(), {})),
        ),
        launch=AsyncMock(return_value=launch_result),
        module=launch_module,
    )
    monkeypatch.setattr(launch_module, "run_launch", world.launch)
    return world


async def _create(
    world: Any,
    *,
    dry_run: bool,
    capability: str = "cap_routed",
    provider_id: str | None = None,
) -> Any:
    return await world.module.create_routed_lease(
        pool=object(),
        capability_repo=world.caps,
        provider_repo=world.providers,
        lease_repo=world.leases,
        workload_repo=world.workloads,
        routing_service=world.routing,
        capability_ref=capability,
        provider_id=provider_id,
        idempotency_key="routed-key",
        dry_run=dry_run,
    )


async def test_a_registry_id_resolves_and_a_dry_run_previews_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={"template_id": "tpl"})

    routed = await _create(world, dry_run=True)

    world.caps.get.assert_awaited_once_with("cap_routed")
    assert world.routing.preview.await_args.kwargs["operation"].value == "compute"
    world.routing.plan_execution.assert_not_awaited()
    assert routed.provider is world.provider and routed.lease is None
    assert routed.launch_result == {"template_id": "tpl"}
    world.workloads.attach_route_plan.assert_not_awaited()


async def test_a_launch_records_the_route_plan_on_the_lease_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={"lease_id": "lease_routed"})

    routed = await _create(world, dry_run=False)

    world.routing.plan_execution.assert_awaited_once()
    assert routed.lease is world.stored
    world.workloads.attach_route_plan.assert_awaited_once_with(
        "wkl_routed", route_plan_id="plan_routed", route_plan=_Plan().to_dict()
    )


async def test_an_unknown_capability_is_capability_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={})
    world.caps.get.return_value = None
    exceptions = importlib.import_module("pitwall.api.exceptions")

    with pytest.raises(exceptions.CapabilityNotFound):
        await _create(world, dry_run=True, capability="missing.cap")
    world.launch.assert_not_awaited()


async def test_a_vanished_selected_provider_is_provider_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={})
    world.providers.get.return_value = None
    exceptions = importlib.import_module("pitwall.api.exceptions")

    with pytest.raises(exceptions.ProviderNotFound):
        await _create(world, dry_run=True)
    world.launch.assert_not_awaited()


async def test_a_capability_without_an_executable_route_is_no_providers_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.routing.production import NoExecutableRouteError

    world = _world(monkeypatch, launch_result={})
    world.routing.preview.side_effect = NoExecutableRouteError("llm.routed", [])
    exceptions = importlib.import_module("pitwall.api.exceptions")

    with pytest.raises(exceptions.ProviderUnavailable) as raised:
        await _create(world, dry_run=True)
    assert raised.value.error_code == "no_providers_available"
    world.launch.assert_not_awaited()


# --- A repeated idempotency key returns the existing lease (Task 7c) -------------------------

_ORIGINAL_PLAN = "plan_" + "0" * 32


def _keyed_workload(world: Any, *, fingerprint: str, state: str = "running") -> Any:
    from pitwall.core.models import Workload

    return Workload(
        id="wkl_routed",
        capability_id="cap_routed",
        provider_id="prov_routed",
        type="inference",
        state=state,
        idempotency_key="routed-key",
        input={world.module.LAUNCH_REQUEST_DIGEST_KEY: fingerprint},
        route_plan_id=_ORIGINAL_PLAN,
        route_plan={"plan_id": _ORIGINAL_PLAN, "selected_provider_id": "prov_routed"},
        submitted_at=world.stored.created_at,
    )


async def test_a_launch_passes_the_callers_request_digest_to_the_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={"lease_id": "lease_routed"})

    await _create(world, dry_run=False)

    world.workloads.get_by_idempotency_key.assert_awaited_once_with("routed-key")
    assert world.launch.await_args.kwargs["request_fingerprint"] == (
        world.module.routed_lease_fingerprint("cap_routed", None)
    )


async def test_a_repeated_key_returns_the_existing_lease_without_planning_or_launching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={})
    fingerprint = world.module.routed_lease_fingerprint("cap_routed", None)
    world.workloads.get_by_idempotency_key.return_value = _keyed_workload(
        world, fingerprint=fingerprint
    )

    routed = await _create(world, dry_run=False)

    world.routing.plan_execution.assert_not_awaited()
    world.launch.assert_not_awaited()
    world.workloads.attach_route_plan.assert_not_awaited()
    world.leases.get_by_workload.assert_awaited_once_with("wkl_routed")
    assert routed.replayed is True
    assert routed.lease is world.stored
    assert routed.route_plan_fields() == {
        "route_plan_id": _ORIGINAL_PLAN,
        "route_plan": {"plan_id": _ORIGINAL_PLAN, "selected_provider_id": "prov_routed"},
    }


async def test_a_repeated_key_with_a_different_request_is_idempotency_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={})
    world.workloads.get_by_idempotency_key.return_value = _keyed_workload(
        world, fingerprint=world.module.routed_lease_fingerprint("cap_routed", None)
    )
    exceptions = importlib.import_module("pitwall.api.exceptions")

    with pytest.raises(exceptions.IdempotencyMismatch):
        await _create(world, dry_run=False, provider_id="prov_pinned")
    world.routing.plan_execution.assert_not_awaited()
    world.launch.assert_not_awaited()


async def test_a_repeated_key_while_the_first_launch_has_no_lease_is_mutation_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(monkeypatch, launch_result={})
    world.workloads.get_by_idempotency_key.return_value = _keyed_workload(
        world, fingerprint=world.module.routed_lease_fingerprint("cap_routed", None), state="queued"
    )
    world.leases.get_by_workload.return_value = None
    exceptions = importlib.import_module("pitwall.api.exceptions")

    with pytest.raises(exceptions.LeaseLaunchInProgress):
        await _create(world, dry_run=False)
    world.launch.assert_not_awaited()


async def test_a_lost_admission_race_returns_the_winners_lease_without_reattaching_a_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _world(
        monkeypatch,
        launch_result={"lease_id": "lease_routed", "workload_id": "wkl_routed", "replayed": True},
    )
    world.workloads.get.return_value = _keyed_workload(
        world, fingerprint=world.module.routed_lease_fingerprint("cap_routed", None)
    )

    routed = await _create(world, dry_run=False)

    world.launch.assert_awaited_once()
    world.workloads.attach_route_plan.assert_not_awaited()
    assert routed.replayed is True and routed.lease is world.stored
    assert routed.route_plan_fields()["route_plan_id"] == _ORIGINAL_PLAN


async def test_a_dry_run_never_consults_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(monkeypatch, launch_result={"template_id": "tpl"})

    await _create(world, dry_run=True)

    world.workloads.get_by_idempotency_key.assert_not_awaited()


async def test_a_digestless_keyed_workload_falls_through_to_the_launch_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workload admitted before digests (any backend) reaches the adapter's own replay."""
    world = _world(
        monkeypatch,
        launch_result={
            "lease_id": "lease_routed",
            "workload_id": "wkl_routed",
            "idempotent_replay": True,
        },
    )
    world.workloads.get_by_idempotency_key.return_value = _keyed_workload(
        world, fingerprint="unused"
    ).model_copy(update={"input": None})
    world.workloads.get.return_value = world.workloads.get_by_idempotency_key.return_value

    routed = await _create(world, dry_run=False)

    world.routing.plan_execution.assert_awaited_once()
    world.launch.assert_awaited_once()
    world.workloads.attach_route_plan.assert_not_awaited()
    assert routed.replayed is True and routed.lease is world.stored
