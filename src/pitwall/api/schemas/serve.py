from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from pitwall.core.enums import LeaseRenewalPolicy
from pitwall.core.models import PitwallModel
from pitwall.models.lookup import Engine
from pitwall.runpod_client.templates import validate_image_ref

CapabilityName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
SafeToken = Annotated[str, Field(pattern=r"^[^\x00-\x20\x7f]+$")]
EnvironmentValue = Annotated[str, Field(pattern=r"^[^\x00-\x1f\x7f]*$")]


class ServeCreate(PitwallModel):
    capability: CapabilityName
    model: SafeToken | None = None
    gpu_class: Annotated[str, Field(min_length=1)] | None = None
    gpu_count: Annotated[int, Field(ge=1)] = 1
    engine: Annotated[
        Engine | None,
        Field(
            description="Serving engine override; omitted restores serve history.",
            json_schema_extra={"default": "vllm"},
        ),
    ] = None
    variant: SafeToken | None = None
    template_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")] | None = None
    ttl_minutes: Annotated[int, Field(ge=1, le=10_080)] = 120
    idle_timeout_min: Annotated[int, Field(ge=5, le=10_080)] | None = None
    max_usd_per_hour: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    renewal_policy: LeaseRenewalPolicy | None = None
    image: SafeToken | None = None
    served_model_name: SafeToken | None = None
    datacenter: SafeToken | None = None
    container_disk_gb: Annotated[int, Field(ge=1)] | None = None
    rate_per_second: Annotated[Decimal, Field(ge=0)] | None = None
    gated: bool = False
    env: dict[SafeToken, EnvironmentValue] = Field(default_factory=dict)
    start_args: list[SafeToken] = Field(default_factory=list)
    dry_run: bool = False
    idempotency_key: SafeToken | None = None

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str | None) -> str | None:
        return validate_image_ref(value) if value is not None else None

    @model_validator(mode="after")
    def _default_renewal_policy(self) -> ServeCreate:
        if self.engine is None and self.model is not None and self.gpu_class is not None:
            object.__setattr__(self, "engine", "vllm")
        if self.renewal_policy is None:
            object.__setattr__(
                self,
                "renewal_policy",
                LeaseRenewalPolicy.ACTIVITY
                if self.idle_timeout_min is not None
                else LeaseRenewalPolicy.MANUAL,
            )
        return self


class ServeResponse(PitwallModel):
    """Serve result; ``created`` is unknown for model-agnostic readiness oracles."""

    capability: str
    lease_id: str | None
    expires_at: str | None
    model_id: str
    proxy_base_url: str
    engine: Engine
    variant: str | None
    gpu_count: int
    workload_id: str | None
    template_id: str | None
    provider_id: str
    dry_run: bool
    created: bool | None
    cost_estimate_usd: str | None
    price_age_seconds: int | None = None
    price_source: Literal["live", "fallback"] | None = None
    price_stale: bool | None = None
    provider_kind: Literal["pod_lease", "self_hosted"] = "pod_lease"


__all__ = ["ServeCreate", "ServeResponse"]
