"""A-20 and A-25: teardown result honesty and provider-neutral missing-provider fallback."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.api.leases import teardown
from pitwall.core.enums import (
    CapabilitySource,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderType,
)
from pitwall.core.models import Lease, LeaseEndpoints, LeaseReadiness, Provider
from tests.fakes.teardown import UnlockedTeardown

pytestmark = pytest.mark.anyio

_CREATED_AT = dt.datetime(2026, 5, 28, 12, 0, tzinfo=dt.UTC)
_TERMINATED_AT = dt.datetime(2026, 5, 28, 12, 10, tzinfo=dt.UTC)


def _lease(**overrides: Any) -> Lease:
    fields: dict[str, Any] = {
        "id": "lease-target",
        "provider_id": "provider-target",
        "runpod_pod_id": "pod-target",
        "state": LeaseState.ACTIVE,
        "created_at": _CREATED_AT,
        "expires_at": _CREATED_AT + dt.timedelta(hours=2),
        "renewal_policy": LeaseRenewalPolicy.MANUAL,
        "endpoints": LeaseEndpoints(http={"8000": "https://pod-8000.example.test"}),
        "readiness": LeaseReadiness(
            runtime_seen_at=_CREATED_AT,
            port_mappings_seen_at=_CREATED_AT,
            probe_passed_at=_CREATED_AT,
            probe_method="ssh_localhost",
        ),
    }
    fields.update(overrides)
    return Lease(**fields)


def _provider() -> Provider:
    return Provider(
        id="provider-target",
        capability_id="cap-target",
        name="target-provider",
        provider_type=ProviderType.POD_LEASE,
        config={},
        priority=1,
        source=CapabilitySource.API,
        updated_at=_CREATED_AT,
    )


def _repos(monkeypatch: pytest.MonkeyPatch, current: Lease, provider: Provider | None) -> None:
    class LeaseRepo(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            return current

        async def capability_name(self, lease_id: str) -> str:
            return "llm.target"

        async def update_state(self, lease_id: str, state: str) -> Lease:
            return current.model_copy(update={"state": state})

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            return current.model_copy(update=changes)

    class ProviderRepo:
        async def get(self, provider_id: str) -> Provider | None:
            return provider

    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: ProviderRepo())
    monkeypatch.setattr(teardown, "publish_lease_event", AsyncMock())


async def test_audit_error_does_not_fail_completed_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repos(monkeypatch, _lease(), _provider())
    terminate = AsyncMock()
    monkeypatch.setattr(teardown, "terminate_pod", terminate)
    monkeypatch.setattr(
        teardown, "insert_audit", AsyncMock(side_effect=RuntimeError("audit unavailable"))
    )
    monkeypatch.setattr(
        teardown, "disarm_serve_provider", AsyncMock(side_effect=RuntimeError("disarm broke"))
    )

    result = await teardown.run_teardown(
        "lease-target", pool=object(), reason="idle", now=_TERMINATED_AT
    )

    terminate.assert_awaited_once()
    assert result.lease.state == LeaseState.STOPPED
    assert result.event is not None
    assert result.errors == ("audit: RuntimeError", "disarm: RuntimeError")


async def test_missing_provider_uses_lease_provider_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A lease recorded without a RunPod pod id belongs to another provider type.
    other = _lease(runpod_pod_id=None, external_resource_id="vm-123")
    _repos(monkeypatch, other, None)
    runpod_terminate = AsyncMock()
    monkeypatch.setattr(teardown, "terminate_pod", runpod_terminate)
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())

    with pytest.raises(teardown.TeardownFailed):
        await teardown.run_teardown("lease-target", pool=object(), now=_TERMINATED_AT)
    runpod_terminate.assert_not_awaited()

    # A lease that recorded a RunPod pod id still falls back to RunPod.
    _repos(monkeypatch, _lease(), None)
    await teardown.run_teardown("lease-target", pool=object(), now=_TERMINATED_AT)
    runpod_terminate.assert_awaited_once_with("pod-target")
