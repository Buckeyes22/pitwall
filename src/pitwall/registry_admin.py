"""Registry writes behind the operator CLI commands.

``pitwall.cli`` modules parse arguments and print results; the repository calls that back
them live here so the CLI never imports repositories or database internals directly.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pitwall.api.provider_schemas import warm_cache_matches
from pitwall.core.enums import CapabilitySource, CostMode, ProviderType
from pitwall.core.ids import ulid_new
from pitwall.core.models import Capability, Lease, Provider


class ProviderNameTaken(Exception):
    """A provider with the requested name is already registered."""

    def __init__(self, existing: Provider) -> None:
        super().__init__(existing.name)
        self.existing = existing


class CapabilityMissing(Exception):
    """The capability id named for a new provider does not exist."""


async def apply_provider_health(pool: Any, provider_id: str, health_status: str) -> Provider | None:
    """Set a provider's health; ``healthy`` also clears its failure and cooldown counters."""
    from pitwall.db.repository import ProviderRepository

    repo = ProviderRepository(pool)

    existing = await repo.get(provider_id)
    if existing is None:
        return None

    patch: dict[str, Any] = {"health_status": health_status}
    if health_status == "healthy":
        patch.update(
            {
                "consecutive_failures": 0,
                "cooldown_trips": 0,
                "recent_error_rate": 0.0,
                "cooldown_until": None,
            }
        )
    return await repo.patch(provider_id, **patch)


async def upsert_capability(
    pool: Any,
    *,
    name: str,
    class_: str,
    cost_mode: str,
    version: str,
    description: str | None,
    hints_supported: list[str] | None,
    openai_compatible: bool,
) -> Capability:
    from pitwall.db.repository import CapabilityRepository

    return await CapabilityRepository(pool).upsert(
        name=name,
        class_=class_,
        cost_mode=cost_mode,
        version=version,
        description=description,
        hints_supported=hints_supported,
        openai_compatible=openai_compatible,
    )


async def renew_operator_lease(
    pool: Any, redis_client: Any, lease_id: str, extends_minutes: int
) -> Lease:
    from pitwall.db.repository import LeaseRepository
    from pitwall.leases.mutations import lease_capability_name, renew_lease

    repo = LeaseRepository(pool)
    capability_name = await lease_capability_name(pool, repo, lease_id)
    return await renew_lease(
        repo,
        lease_id,
        extends_minutes=extends_minutes,
        actor="cli:lease",
        renewed_by="operator",
        pool=pool,
        redis=redis_client,
        capability_name=capability_name,
    )


async def serve_cache_state(pool: Any, capability_name: str, variant_id: str) -> str:
    """``warm`` when the serve provider's cache record matches the variant, else ``cold``."""
    from pitwall.db.repository import ProviderRepository

    provider = await ProviderRepository(pool).get_by_name(f"serve-{capability_name}")
    if provider is None:
        return "cold"
    return "warm" if warm_cache_matches(provider.config, variant=variant_id) else "cold"


async def register_endpoint_provider(
    pool: Any,
    *,
    name: str,
    capability_id: str,
    capability_name: str | None,
    provider_type: ProviderType,
    endpoint_id: str,
    region: str | None,
    config: dict[str, Any],
    priority: int,
    health: str,
    cost_mode: str | None,
) -> Provider:
    """Register a RunPod endpoint as a provider, creating its capability by name when asked.

    Raises ``ProviderNameTaken`` or ``CapabilityMissing`` before anything is written.
    """
    from pitwall.db.repository import CapabilityRepository, ProviderRepository

    cap_repo = CapabilityRepository(pool)
    prov_repo = ProviderRepository(pool)

    existing = await prov_repo.get_by_name(name)
    if existing is not None:
        raise ProviderNameTaken(existing)

    if capability_name:
        try:
            resolved_cost_mode = CostMode(cost_mode or "per_second")
        except ValueError:
            resolved_cost_mode = CostMode.PER_SECOND
        capability = await cap_repo.upsert(
            name=capability_name,
            class_="llm",
            cost_mode=resolved_cost_mode.value,
            openai_compatible=True,
        )
        capability_id = capability.id
    elif await cap_repo.get(capability_id) is None:
        raise CapabilityMissing(capability_id)

    provider = Provider(
        id=f"prov_{ulid_new()}",
        capability_id=capability_id,
        name=name,
        provider_type=provider_type,
        runpod_endpoint_id=endpoint_id,
        runpod_template_id=None,
        region=region,
        cloud_type=None,
        config=config,
        priority=priority,
        enabled=True,
        health_status=health,
        consecutive_failures=0,
        cooldown_trips=0,
        cold_start_p50_ms=None,
        cold_start_p95_ms=None,
        recent_error_rate=0.0,
        cooldown_until=None,
        source=CapabilitySource.API,
        last_applied_yaml_hash=None,
        updated_at=dt.datetime.now(dt.UTC),
    )
    return await prov_repo.create(provider)
