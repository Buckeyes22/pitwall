from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, Self, TypedDict, cast

from pydantic import Field, ValidationError, model_validator

from pitwall.api.leases.launch import InvalidProviderConfig
from pitwall.core.enums import ProviderType
from pitwall.core.models import PitwallModel, Provider


class SelfHostedState(TypedDict, total=False):
    """Persisted ``config.self_hosted_state`` keys shared by all writers.

    Probe observations own readiness, residency, observation time, cold-start
    telemetry, tool support, and warming episode markers. Request telemetry
    owns the concurrency capacity marker.
    """

    readiness: Literal["ready", "starting", "absent"]
    resident: list[str]
    observed_at: str
    cold_start_s: dict[str, float | None]
    tool_calling: Literal["enabled", "disabled", "unknown"]
    warming_since: str
    warming_timeout_charged: bool
    concurrency_limited: bool


def self_hosted_state(value: object) -> SelfHostedState:
    """Copy a persisted state mapping without dropping another writer's keys."""
    if not isinstance(value, Mapping):
        return {}
    return cast(SelfHostedState, dict(value))


class ReadinessConfig(PitwallModel):
    kind: Literal["llama-swap", "openai-models", "http-health"]
    path: str | None = None

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if self.kind == "http-health":
            if self.path is None or not self.path.startswith("/"):
                raise ValueError("http-health readiness requires path beginning with '/'")
        elif self.path is not None:
            raise ValueError("path is only valid for http-health readiness")
        return self


class WarmupConfig(PitwallModel):
    prompt: str = Field(min_length=1)
    max_tokens: int = Field(default=1, ge=1)


class SelfHostedModel(PitwallModel):
    id: str = Field(min_length=1)
    slot_group: str | None = None
    exclusive: bool = False
    vram_gb: Decimal | None = Field(default=None, gt=0)
    context_length: int | None = Field(default=None, ge=1)
    tool_calling: Literal["enabled", "disabled", "unknown"] = "unknown"
    tool_call_parser: str | None = None


class SelfHostedCost(PitwallModel):
    mode: Literal["zero"]
    watts: Decimal | None = Field(default=None, ge=0)
    usd_per_kwh: Decimal | None = Field(default=None, ge=0)


class SelfHostedProfile(PitwallModel):
    readiness: ReadinessConfig
    cold_start_timeout_s: int = Field(default=330, ge=1, le=3600)
    warmup: WarmupConfig | None = None
    models: tuple[SelfHostedModel, ...] = ()
    idle_unload_s: int | None = Field(default=None, ge=1)
    strict_slots: bool = False
    cost: SelfHostedCost | None = None

    @model_validator(mode="after")
    def validate_models(self) -> Self:
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("self-hosted model ids must be unique")
        return self


def self_hosted_profile(provider: Provider) -> SelfHostedProfile | None:
    if provider.provider_type != ProviderType.PUBLIC_ENDPOINT:
        return None
    raw = provider.config.get("self_hosted")
    if raw is None:
        return None
    try:
        return SelfHostedProfile.model_validate(raw)
    except ValidationError as exc:
        raise InvalidProviderConfig(
            f"provider {provider.id!r} has invalid config.self_hosted: {exc}"
        ) from exc
