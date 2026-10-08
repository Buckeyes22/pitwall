"""Registry tests for primary custom provider and public endpoint fallback registration.

Covers:
    - Primary custom vLLM provider (serverless_lb/serverless_queue) registration with fallback_chain
    - Public endpoint fallback registration with fallback_for
    - Routing plan verification for fallback chains

The "registry" is the provider registry: the system that stores and manages
provider registrations. These tests verify the end-to-end flow of registering
providers with fallback configurations and the production planner's correct
handling of those configurations (``build_production_plan``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pitwall.config import RoutingWeights
from pitwall.core.enums import ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.providers.registry import get_default_registry
from pitwall.routing.production import (
    ProductionRoutePlan,
    RoutingOperation,
    build_production_plan,
)

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _capability(
    cap_id: str = "cap_llm_qwen3_32b",
    name: str = "llm.qwen3-32b",
) -> Capability:
    return Capability(
        id=cap_id,
        name=name,
        version="1.0.0",
        class_="llm",
        cost_mode="per_second",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _primary_custom_provider(
    provider_id: str = "prov_custom_qwen3_serverless",
    capability_id: str = "cap_llm_qwen3_32b",
    fallback_chain: list[str] | None = None,
    priority: int = 1,
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id=capability_id,
        name=provider_id,
        provider_type=ProviderType.SERVERLESS_LB,
        runpod_endpoint_id="qwen3-32b-awq",
        region="US-KS-2",
        config={
            "gpu_type": "NVIDIA L4",
            "lb_base_url": "https://qwen3-32b-awq.api.runpod.ai",
            "cost": {"per_second_active": "0.001"},
            "fallback_chain": fallback_chain or [],
        },
        priority=priority,
        enabled=True,
        health_status="healthy",
        cold_start_p50_ms=2000,
        recent_error_rate=0.0,
        updated_at=_NOW,
    )


def _public_endpoint_fallback(
    provider_id: str = "prov_qwen3_32b_public",
    capability_id: str = "cap_llm_qwen3_32b",
    fallback_for: list[str] | None = None,
    priority: int = 2,
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id=capability_id,
        name=provider_id,
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        runpod_endpoint_id="qwen3-32b-awq",
        region="US-KS-2",
        config={
            "gpu_type": "NVIDIA L4",
            "openai_base_url": "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1",
            "cost": {"per_second_active": "0.002"},
            "fallback_for": fallback_for or [],
        },
        priority=priority,
        enabled=True,
        health_status="healthy",
        cold_start_p50_ms=0,
        recent_error_rate=0.0,
        updated_at=_NOW,
    )


class TestPrimaryCustomProviderRegistration:
    """Tests for primary custom provider registration."""

    def test_primary_custom_provider_with_empty_fallback_chain(self) -> None:
        """A primary custom provider can be registered with an empty fallback chain."""
        primary = _primary_custom_provider(fallback_chain=[])

        assert primary.provider_type == ProviderType.SERVERLESS_LB
        assert primary.priority == 1
        assert primary.config["fallback_chain"] == []

    def test_primary_custom_provider_with_explicit_fallback_chain(self) -> None:
        """A primary custom provider can specify an explicit fallback chain."""
        primary = _primary_custom_provider(fallback_chain=["prov_qwen3_32b_public"])

        assert primary.config["fallback_chain"] == ["prov_qwen3_32b_public"]

    def test_primary_custom_provider_priority_is_lower_than_fallback(self) -> None:
        """Primary providers should have priority 1, fallbacks priority 2+."""
        primary = _primary_custom_provider(priority=1)
        fallback = _public_endpoint_fallback(priority=2)

        assert primary.priority < fallback.priority


class TestPublicEndpointFallbackRegistration:
    """Tests for public endpoint fallback registration."""

    def test_public_endpoint_fallback_with_empty_fallback_for(self) -> None:
        """A public endpoint can be registered with an empty fallback_for list."""
        fallback = _public_endpoint_fallback(fallback_for=[])

        assert fallback.provider_type == ProviderType.PUBLIC_ENDPOINT
        assert fallback.config["fallback_for"] == []

    def test_public_endpoint_fallback_declares_primary(self) -> None:
        """A public endpoint fallback declares which primary it is a fallback for."""
        fallback = _public_endpoint_fallback(fallback_for=["prov_custom_qwen3_serverless"])

        assert fallback.config["fallback_for"] == ["prov_custom_qwen3_serverless"]

    def test_public_endpoint_has_zero_cold_start(self) -> None:
        """Public endpoints have 0 cold start since they are always-on."""
        fallback = _public_endpoint_fallback()

        assert fallback.cold_start_p50_ms == 0


def _plan(
    providers: list[Provider],
    *,
    capability: Capability | None = None,
    max_attempts: int = 3,
) -> ProductionRoutePlan:
    return build_production_plan(
        capability=capability or _capability(),
        providers=providers,
        payload={},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=get_default_registry(),
        now=_NOW,
        mode="priority",
        weights=RoutingWeights(),
        max_attempts=max_attempts,
    )


def _unhealthy(provider: Provider) -> Provider:
    return provider.model_copy(update={"health_status": "unhealthy"})


class TestFallbackChainRouting:
    """Tests for routing with primary + fallback provider pairs."""

    def test_primary_selected_when_healthy(self) -> None:
        """The primary provider is selected when healthy; the fallback follows it."""
        primary = _primary_custom_provider(fallback_chain=["prov_qwen3_32b_public"])
        fallback = _public_endpoint_fallback(fallback_for=["prov_custom_qwen3_serverless"])

        plan = _plan([fallback, primary])

        assert plan.selected_provider_id == "prov_custom_qwen3_serverless"
        assert plan.fallback_chain == (
            "prov_custom_qwen3_serverless",
            "prov_qwen3_32b_public",
        )

    def test_fallback_used_when_primary_unhealthy(self) -> None:
        """The fallback provider is used when the primary is unhealthy."""
        primary = _primary_custom_provider(fallback_chain=["prov_qwen3_32b_public"])
        fallback = _public_endpoint_fallback(fallback_for=["prov_custom_qwen3_serverless"])

        plan = _plan([_unhealthy(primary), fallback])

        assert plan.selected_provider_id == "prov_qwen3_32b_public"
        assert plan.fallback_chain == ("prov_qwen3_32b_public",)
        assert plan.dropped_provider_reasons == {
            "prov_custom_qwen3_serverless": ["health:health_unavailable"]
        }

    def test_fallback_chain_respects_max_attempts(self) -> None:
        """The attempt chain is capped at max_attempts and ordered by priority."""
        primary = _primary_custom_provider()
        fallbacks = [
            _public_endpoint_fallback(provider_id=f"prov_fallback_{name}", priority=priority)
            for name, priority in (("a", 2), ("b", 3), ("c", 4))
        ]

        plan = _plan([primary, *fallbacks], max_attempts=2)

        assert plan.fallback_chain == ("prov_custom_qwen3_serverless", "prov_fallback_a")


class TestProviderRegistryIntegration:
    """Each capability plans against its own providers."""

    def test_multiple_capabilities_with_separate_fallbacks(self) -> None:
        qwen_primary = _primary_custom_provider(provider_id="prov_qwen_primary")
        qwen_fallback = _public_endpoint_fallback(provider_id="prov_qwen_public")
        bge_capability = Capability(
            id="cap_embedding_bge_m3",
            name="embedding.bge-m3",
            version="1.0.0",
            class_="embedding",
            cost_mode="per_second",
            created_at=_NOW,
            updated_at=_NOW,
        )
        bge_primary = Provider(
            id="prov_bge_primary",
            capability_id="cap_embedding_bge_m3",
            name="prov_bge_primary",
            provider_type=ProviderType.SERVERLESS_QUEUE,
            runpod_endpoint_id="bge-m3-xyz",
            region="US-KS-2",
            config={"gpu_type": "NVIDIA L4", "cost": {"per_second_active": "0.001"}},
            priority=1,
            enabled=True,
            health_status="healthy",
            updated_at=_NOW,
        )
        bge_fallback = bge_primary.model_copy(
            update={"id": "prov_bge_public", "name": "prov_bge_public", "priority": 2}
        )

        qwen_plan = _plan([qwen_primary, qwen_fallback])
        bge_plan = _plan([bge_primary, bge_fallback], capability=bge_capability)

        assert qwen_plan.fallback_chain == ("prov_qwen_primary", "prov_qwen_public")
        assert bge_plan.fallback_chain == ("prov_bge_primary", "prov_bge_public")
        assert qwen_plan.capability_id == "cap_llm_qwen3_32b"
        assert bge_plan.capability_id == "cap_embedding_bge_m3"
