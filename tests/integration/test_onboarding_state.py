"""RP-05 persistence evidence against the migrated PostgreSQL schema."""

from __future__ import annotations

import pytest

from pitwall.onboarding import (
    EndpointSelection,
    OnboardingError,
    OnboardingEvent,
    PostgresOnboardingState,
    RunPodOnboardingRequest,
    StepState,
    TemplateSelection,
)
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


def _request() -> RunPodOnboardingRequest:
    return RunPodOnboardingRequest(
        name="integration-onboarding",
        capability_name="embedding.integration-onboarding",
        capability_class="embedding",
        provider_name="integration-runpod",
        image="docker.io/example/worker:sha-integration",
        public_image=True,
        gpu_type_ids=("NVIDIA L4",),
        rate_per_hour_usd="0.50",
        template=TemplateSelection(mode="existing", resource_id="template_integration"),
        endpoint=EndpointSelection(mode="existing", resource_id="endpoint_integration"),
    )


async def test_existing_registry_and_audit_store_are_sufficient_for_resume(pg_pool) -> None:
    state = PostgresOnboardingState(pg_pool, actor="system")
    request = _request()
    plan_id = "runpod_onboard_0123456789abcdef01234567"
    event = OnboardingEvent(
        step="template",
        state=StepState.COMPLETED,
        resource_type="template",
        resource_id="template_integration",
        created_by_plan=True,
        detail="created template",
    )

    await state.append_event(plan_id, event)
    assert await state.load_events(plan_id) == (event,)

    capability = await state.create_capability(request)
    assert await state.get_capability(request.capability_name) == capability
    assert await state.get_capability_by_id(capability.id) == capability

    provider = await state.create_provider(
        request,
        capability=capability,
        endpoint_id="endpoint_integration",
        template_id="template_integration",
        volume_id=None,
    )
    assert await state.get_provider(request.provider_name) == provider
    assert await state.get_provider_by_id(provider.id) == provider

    await state.disable_provider(provider.id)
    await state.disable_capability(capability.id)
    disabled_provider = await state.get_provider_by_id(provider.id)
    disabled_capability = await state.get_capability_by_id(capability.id)
    assert disabled_provider is not None and disabled_provider.enabled is False
    assert disabled_capability is not None and disabled_capability.enabled is False
    enabled_provider = await state.enable_provider(provider.id)
    enabled_capability = await state.enable_capability(capability.id)
    assert enabled_provider is not None and enabled_provider.enabled is True
    assert enabled_capability is not None and enabled_capability.enabled is True

    async with pg_pool.acquire() as conn:
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM pitwall.config_audit "
            "WHERE entity_type = 'provider' AND entity_id = $1 "
            "AND new_value->>'kind' = 'runpod_onboarding_event'",
            plan_id,
        )
    assert audit_count == 1


async def test_apply_lock_serializes_independent_postgres_state_instances(pg_pool) -> None:
    holder = PostgresOnboardingState(pg_pool, actor="system")
    contender = PostgresOnboardingState(pg_pool, actor="system", lock_timeout_s=0.05)

    async with holder.apply_lock("runpod_onboard_aaaaaaaaaaaaaaaaaaaaaaaa"):
        with pytest.raises(OnboardingError) as caught:
            async with contender.apply_lock("runpod_onboard_bbbbbbbbbbbbbbbbbbbbbbbb"):
                raise AssertionError("contending onboarding lock unexpectedly acquired")
        assert caught.value.code == "onboarding_lock_timeout"

    async with contender.apply_lock("runpod_onboard_bbbbbbbbbbbbbbbbbbbbbbbb"):
        pass
