"""Thin MCP adapter for the shared serve-model service."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field

from pitwall.cli.base_url import configured_base_url
from pitwall.cli.serve_model import register_route
from pitwall.config import get_settings
from pitwall.core.enums import LeaseRenewalPolicy
from pitwall.db import get_pool
from pitwall.models import load_catalogue
from pitwall.models.lookup import Engine
from pitwall.personal.backend import select_backend
from pitwall.personal.service import ServeSpec, build_personal_service
from pitwall.serve import ServeRequest, serve_model

Capability = Annotated[str, Field(description="Capability name to create or replay.")]
ModelId = Annotated[str | None, Field(description="Model ID; omitted restores serve history.")]
GpuClass = Annotated[
    str | None,
    Field(description="Canonical RunPod GPU name; omitted restores serve history."),
]
GpuCount = Annotated[int, Field(ge=1, description="Number of GPUs to attach.")]
EngineOverride = Annotated[
    Engine,
    Field(description="Optional engine override; catalogue variants otherwise select it."),
]
VariantId = Annotated[
    str | None,
    Field(description="Catalogue variant selected after pitwall_models_fit; omit for the default."),
]
TemplateId = Annotated[str | None, Field(description="Existing RunPod template ID to reuse.")]
TtlMinutes = Annotated[
    int, Field(ge=1, le=10_080, description="Lease duration used for expiry and cost estimates.")
]
IdleTimeoutMinutes = Annotated[
    int | None,
    Field(ge=5, le=10_080, description="Minutes without proxy traffic before stop."),
]
MaxUsdPerHour = Annotated[
    Decimal | None,
    Field(
        gt=0,
        max_digits=12,
        decimal_places=4,
        description="Maximum accepted live GPU price in USD/hour.",
    ),
]
RenewalPolicy = Annotated[
    LeaseRenewalPolicy | None,
    Field(description="manual or activity; omitted derives from idle timeout."),
]
RouteName = Annotated[
    str | None,
    Field(description="Routing route to add or refresh after a live serve succeeds."),
]
Image = Annotated[
    str | None,
    Field(description="Container image; required for models without a catalogue dossier."),
]
ServedModelName = Annotated[
    str | None,
    Field(description="OpenAI model name exposed by the launched server."),
]
RatePerSecond = Annotated[
    Decimal | None,
    Field(description="USD per second; required when the selected GPU price is unpriced."),
]
Gated = Annotated[bool, Field(description="Use configured gated-model launch credentials.")]
DryRun = Annotated[
    bool,
    Field(description="Validate and return the resolved launch plan without launching a pod."),
]
IdempotencyKey = Annotated[str | None, Field(description="Stable key for safe launch retries.")]


class _OmittedDefault(int):
    """An int-like public default that retains omission information at the adapter boundary."""


_OMITTED_GPU_COUNT = _OmittedDefault(1)
_OMITTED_TTL_MINUTES = _OmittedDefault(120)


async def pitwall_serve_model(
    capability: Capability,
    model: ModelId = None,
    gpu_class: GpuClass = None,
    gpu_count: GpuCount = _OMITTED_GPU_COUNT,
    engine: EngineOverride | None = None,
    variant: VariantId = None,
    template_id: TemplateId = None,
    ttl_minutes: TtlMinutes = _OMITTED_TTL_MINUTES,
    idle_timeout_min: IdleTimeoutMinutes = None,
    max_usd_per_hour: MaxUsdPerHour = None,
    renewal_policy: RenewalPolicy = None,
    route: RouteName = None,
    image: Image = None,
    served_model_name: ServedModelName = None,
    rate_per_second: RatePerSecond = None,
    gated: Gated = False,
    dry_run: DryRun = False,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Launch a catalogue variant or an image-backed custom model-serving pod lease."""
    values: dict[str, Any] = {"capability_name": capability}
    optional = {
        "model": model,
        "gpu_class": gpu_class,
        "engine": engine,
        "variant": variant,
        "template_id": template_id,
        "idle_timeout_min": idle_timeout_min,
        "max_usd_per_hour": max_usd_per_hour,
        "renewal_policy": renewal_policy,
        "image": image,
        "served_model_name": served_model_name,
        "rate_per_second": rate_per_second,
        "idempotency_key": idempotency_key,
    }
    if gpu_count is not _OMITTED_GPU_COUNT:
        optional["gpu_count"] = gpu_count
    if ttl_minutes is not _OMITTED_TTL_MINUTES:
        optional["ttl_minutes"] = ttl_minutes
    if model is not None and gpu_class is not None and engine is None:
        optional["engine"] = "vllm"
    values.update({key: value for key, value in optional.items() if value is not None})
    if gated:
        values["gated"] = True
    if dry_run:
        values["dry_run"] = True
    settings = get_settings()
    if select_backend() == "personal":
        spec = ServeSpec.model_validate(
            {
                "model": model,
                "variant": variant,
                "gpu_class": gpu_class,
                "gpu_count": gpu_count,
                "ttl_minutes": ttl_minutes,
                "max_usd_per_hour": max_usd_per_hour,
                "rate_per_second": rate_per_second,
                "route": route,
            }
        )
        service = build_personal_service(settings)
        if dry_run:
            preview = await service.plan(spec)
            return {
                **preview.model_dump(mode="json"),
                "dry_run": True,
                "state": "dry_run",
                "route": spec.route,
            }
        lease = await service.serve(spec)
        data = lease.model_dump(mode="json")
        data["try"] = f"route-shim.sh {lease.route} prompt.md"
        return data
    result = await serve_model(
        await get_pool(),
        ServeRequest.model_validate(values),
        base_url=configured_base_url(settings.pitwall_base_url),
        settings=settings,
        catalogue=load_catalogue(),
    )
    data = result.to_dict()
    if route is not None and not result.dry_run:
        registration = await asyncio.to_thread(
            register_route,
            routing_cli=settings.pitwall_routing_cli,
            route=route,
            capability=result.capability,
            base_url=configured_base_url(settings.pitwall_base_url),
            env=os.environ,
        )
        data["route_registration"] = asdict(registration)
    return data


__all__ = ["pitwall_serve_model"]
