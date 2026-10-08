"""Routing types for the 4-stage provider selection algorithm."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

type CapacityReason = Literal[
    "missing_capacity_key",
    "available",
    "capacity_unknown",
    "capacity_unavailable",
    "resident",
    "slot_free",
    "would_evict",
    "strict_slots_blocked",
    "concurrency_limited",
]


class EliminationReason(StrEnum):
    """Reasons a provider is eliminated during Stage 1 hard-constraint filtering.

    Ordered to match Stage 1 hard-constraint filtering.
    """

    CAPABILITY_MISMATCH = "capability_mismatch"
    REGION_MISMATCH = "region_mismatch"
    CUDA_MISMATCH = "cuda_mismatch"
    GPU_CLASS_MISMATCH = "gpu_class_mismatch"
    PAYLOAD_TOO_LARGE = "payload_too_large"


@dataclass(frozen=True, slots=True)
class Hints:
    """Consumer hints used to rank providers in Stage 3 scoring.

    Corresponds to the Hints type used in score_provider (§7 spec).
    """

    latency_sensitive: bool = False
    cost_sensitive: bool = False
    region_preference: str | None = None
    cache_key: str | None = None


@dataclass(frozen=True, slots=True)
class ObservedMetrics:
    """Observed runtime metrics for providers, used in Stage 3 scoring."""

    recent_error_rate: float = 0.0
    last_cache_key: str | None = None


@dataclass(frozen=True, slots=True)
class ScoreExplanation:
    """Deterministic breakdown for the Stage 3 score formula."""

    provider_id: str
    base_score: float = 100.0
    latency_penalty: float = 0.0
    warm_worker_bonus: float = 0.0
    cost_penalty: float = 0.0
    region_bonus: float = 0.0
    recent_error_penalty: float = 0.0
    quota_headroom_bonus: float = 0.0
    reset_proximity_bonus: float = 0.0
    affinity_bonus: float = 0.0
    quota_reason: str | None = None
    trains_on_prompts: bool | None = None
    priority_multiplier: float = 1.0
    score_before_multiplier: float = 100.0
    final_score: float = 100.0

    @property
    def score(self) -> float:
        """Final score after priority multiplier."""

        return self.final_score

    @property
    def error_penalty(self) -> float:
        """Backward-compatible alias for recent-error score penalty."""

        return self.recent_error_penalty

    def to_dict(self) -> dict[str, float | str | bool | None]:
        return {
            "provider_id": self.provider_id,
            "base_score": self.base_score,
            "latency_penalty": self.latency_penalty,
            "warm_worker_bonus": self.warm_worker_bonus,
            "cost_penalty": self.cost_penalty,
            "region_bonus": self.region_bonus,
            "recent_error_penalty": self.recent_error_penalty,
            "quota_headroom_bonus": self.quota_headroom_bonus,
            "reset_proximity_bonus": self.reset_proximity_bonus,
            "affinity_bonus": self.affinity_bonus,
            "quota_reason": self.quota_reason,
            "trains_on_prompts": self.trains_on_prompts,
            "priority_multiplier": self.priority_multiplier,
            "score_before_multiplier": self.score_before_multiplier,
            "final_score": self.final_score,
        }


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    """Input to the routing algorithm — what the consumer is asking for."""

    capability_name: str
    payload_bytes: int | None = None
    required_gpu_class: str | None = None
    required_region: str | None = None
    required_volume_id: str | None = None
    hints: Hints | None = None
    capability_id: str | None = None
    required_cuda_min: str | None = None
    required_cuda_version: str | None = None
    stream: bool = False

    @property
    def payload_mb(self) -> float | None:
        """Payload size in megabytes, or None if unknown."""
        if self.payload_bytes is None:
            return None
        return self.payload_bytes / (1024 * 1024)


@dataclass(frozen=True, slots=True)
class ConstraintResult:
    """Result of Stage 1 hard-constraint evaluation on a single provider."""

    provider_id: str
    passed: bool
    reason: EliminationReason | None = None
    reasons: tuple[EliminationReason, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        normalized = tuple(self.reasons)
        reason = self.reason

        if self.passed:
            reason = None
            normalized = ()
        elif reason is None and normalized:
            reason = normalized[0]
        elif reason is not None and not normalized:
            normalized = (reason,)

        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "reasons", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "passed": self.passed,
            "reason": self.reason.value if self.reason else None,
            "reasons": [reason.value for reason in self.reasons],
        }


__all__ = [
    "ConstraintResult",
    "EliminationReason",
    "Hints",
    "ObservedMetrics",
    "RoutingRequest",
    "ScoreExplanation",
]
