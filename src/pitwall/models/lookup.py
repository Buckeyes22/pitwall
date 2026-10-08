from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

Engine = Literal["vllm", "llama.cpp", "sglang"]
CompanionKind = Literal["mmproj", "mtp", "draft"]
EvidenceKind = Literal["measured", "research"]


@dataclass(frozen=True, slots=True)
class CompanionInfo:
    kind: CompanionKind
    repo: str
    file: str
    flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceInfo:
    kind: EvidenceKind
    gpu_class: str
    observed_vram_gb: float | None
    observed_startup_s: float | None
    date: str


@dataclass(frozen=True, slots=True)
class VariantInfo:
    variant_id: str
    engine: Engine
    image: str
    repo: str
    file: str | None
    flags: tuple[str, ...]
    companions: tuple[CompanionInfo, ...]
    evidence: EvidenceInfo | None
    openai_chat: bool
    env: Mapping[str, str]
    container_disk_gb: int | None
    startup_min: int | None
    gated: bool
    min_cuda: str | None = None
    served_model_name: str | None = None


class CatalogueLookup(Protocol):
    def variant(self, model_id: str, variant_id: str | None) -> VariantInfo | None: ...
