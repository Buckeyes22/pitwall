from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from pitwall.core.models import PitwallModel
from pitwall.models.lookup import CompanionKind, Engine, EvidenceKind
from pitwall.runpod_client.gpu import is_canonical_gpu_name

PositiveIntOrUnverified = Annotated[int, Field(gt=0)] | Literal["unverified"]
PositiveNumberOrUnverified = Annotated[float, Field(gt=0)] | Literal["unverified"]
Confidence = Literal["low", "medium", "high"]
ModelFormat = Literal["bf16", "fp8", "int4", "awq", "nvfp4", "gptq", "gguf"]


class LicenseInfo(PitwallModel):
    name: str
    url: str
    gated: bool


class ArchitectureInfo(PitwallModel):
    kind: Literal["dense", "moe"]
    params_total_b: PositiveNumberOrUnverified
    params_active_b: PositiveNumberOrUnverified
    context_length_max: PositiveIntOrUnverified
    modalities: tuple[str, ...]
    thinking_mode: str
    layers: int | None = Field(default=None, gt=0)


class CapabilitiesInfo(PitwallModel):
    tool_calling: str
    structured_outputs: str
    vision: bool
    languages: str


class PitwallInfo(PitwallModel):
    capability_name: str
    served_model_name: str


class DossierConfidence(PitwallModel):
    overall: Confidence
    notes: str


class Companion(PitwallModel):
    kind: CompanionKind
    repo: str
    file: str
    flags: tuple[str, ...] = ()


class Evidence(PitwallModel):
    kind: EvidenceKind
    gpu_class: str
    observed_vram_gb: PositiveNumberOrUnverified
    observed_startup_s: PositiveNumberOrUnverified
    date: str

    @field_validator("gpu_class")
    @classmethod
    def canonical_gpu_class(cls, value: str) -> str:
        if not is_canonical_gpu_name(value):
            raise ValueError("evidence GPU name must be canonical")
        return value


class Variant(PitwallModel):
    id: str
    default: bool = False
    engine: Engine
    image: str
    min_cuda: str | None = None
    """Minimum CUDA runtime the image needs, e.g. "12.8".

    RunPod allocates a pod onto a host with a fixed driver. A container whose image
    requires a newer CUDA than the host provides never starts and retries until the
    lease expires, billing the whole time.
    """
    repo: str
    file: str | None = None
    format: ModelFormat
    min_vram_gb: PositiveIntOrUnverified
    context: PositiveIntOrUnverified
    container_disk_gb: PositiveIntOrUnverified
    startup_min: PositiveIntOrUnverified
    flags: tuple[str, ...] = ()
    companions: tuple[Companion, ...] = ()
    evidence: Evidence | None = None
    env: dict[str, str] = Field(default_factory=dict)
    recommended_gpu_classes: tuple[str, ...] = ()
    tool_call_parser: str | None = None
    reasoning_parser: str | None = None
    confidence: Confidence
    sources: tuple[str, ...]
    params_total_b: Decimal | None = Field(default=None, gt=0, exclude=True)
    layers: int | None = Field(default=None, gt=0, exclude=True)

    @field_validator("min_cuda")
    @classmethod
    def cuda_major_minor(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"\d+\.\d+", value) is None:
            raise ValueError(f"min_cuda must be MAJOR.MINOR, got {value!r}")
        return value

    @field_validator("recommended_gpu_classes")
    @classmethod
    def canonical_gpu_hints(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        invalid = [value for value in values if not is_canonical_gpu_name(value)]
        if invalid:
            raise ValueError(f"recommended GPU names must be canonical: {invalid}")
        return values

    @model_validator(mode="after")
    def engine_file_contract(self) -> Self:
        if self.engine == "llama.cpp" and self.file is None:
            raise ValueError("file is required for llama.cpp")
        if self.engine != "llama.cpp" and self.file is not None:
            raise ValueError(f"file must be null for {self.engine}")
        return self


class ModelDossier(PitwallModel):
    model_id: str
    vendor: str
    family: str
    release_date: str
    license: LicenseInfo
    architecture: ArchitectureInfo
    capabilities: CapabilitiesInfo
    openai_chat: bool
    pitwall: PitwallInfo
    variants: tuple[Variant, ...]
    confidence: DossierConfidence
    accessed: str
    body: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        default="",
        exclude=True,
    )

    @model_validator(mode="after")
    def variant_contract(self) -> Self:
        ids = [variant.id for variant in self.variants]
        if not ids:
            raise ValueError("variants must be non-empty")
        if len(ids) != len(set(ids)):
            raise ValueError("variant ids must be unique")
        if sum(variant.default for variant in self.variants) != 1:
            raise ValueError("exactly one variant must have default true")
        return self

    def resolve_variant(self, variant_id: str | None) -> Variant | None:
        if variant_id is None:
            return next(variant for variant in self.variants if variant.default)
        return next((variant for variant in self.variants if variant.id == variant_id), None)
