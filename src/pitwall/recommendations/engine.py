"""Recommendations engine — aggregate signals into ranked, actionable operator guidance.

The engine is deterministic and read-only given input snapshots.  It never mutates
state or auto-applies changes.  Output is a sorted list of :class:`Recommendation`
objects ordered by priority (most urgent first).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pitwall.finops.burn_rate import BurnRateForecast
from pitwall.finops.reservations import ReservationRecommendation

_USD_QUANTUM = Decimal("0.000001")

# ---------------------------------------------------------------------------
# Dimension score contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DimensionScore:
    """One dimension's score against its benchmark for a (capability, provider) pair.

    Not ``observability.scorecards.EntityScorecard``, which is the normalized
    cost×latency×quality scorecard of one pair. This carries the single-dimension
    ``score`` and ``benchmark`` the copilot supplies (both clamped to 0..1), so the two
    types have different fields and are not interchangeable.
    """

    capability_id: str
    provider_id: str
    dimension: str
    score: Decimal
    benchmark: Decimal
    message: str = ""

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id must be non-empty")
        if not self.provider_id:
            raise ValueError("provider_id must be non-empty")
        if not self.dimension:
            raise ValueError("dimension must be non-empty")
        object.__setattr__(self, "score", _quantize(_clamp_01(self.score), "score"))
        object.__setattr__(self, "benchmark", _quantize(_clamp_01(self.benchmark), "benchmark"))


# ---------------------------------------------------------------------------
# Recommendation types
# ---------------------------------------------------------------------------


class RecommendationCategory(StrEnum):
    """High-level bucket for a recommendation."""

    BUDGET = "budget"
    CAPACITY = "capacity"
    SCORECARD = "scorecard"


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One actionable, prioritized operator recommendation."""

    action: str
    category: RecommendationCategory
    target_provider_id: str | None
    target_capability_id: str | None
    rationale: str
    estimated_impact_usd: Decimal
    confidence: Decimal
    source_signals: tuple[str, ...]
    priority: int

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("action must be non-empty")
        if not self.rationale:
            raise ValueError("rationale must be non-empty")
        object.__setattr__(
            self,
            "estimated_impact_usd",
            _signed_usd(self.estimated_impact_usd, "estimated_impact_usd"),
        )
        object.__setattr__(self, "confidence", _quantize(_clamp_01(self.confidence), "confidence"))
        if self.priority < 1:
            raise ValueError("priority must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "category": self.category.value,
            "target_provider_id": self.target_provider_id,
            "target_capability_id": self.target_capability_id,
            "rationale": self.rationale,
            "estimated_impact_usd": _decimal_to_str(self.estimated_impact_usd),
            "confidence": _decimal_to_str(self.confidence),
            "source_signals": list(self.source_signals),
            "priority": self.priority,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecommendationEngine:
    """Pure, deterministic recommender aggregating four signal planes."""

    runway_critical_days: Decimal = Decimal("3")
    runway_warning_days: Decimal = Decimal("7")
    scorecard_threshold: Decimal = Decimal("0.15")

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runway_critical_days",
            _positive_decimal(self.runway_critical_days, "runway_critical_days"),
        )
        object.__setattr__(
            self,
            "runway_warning_days",
            _positive_decimal(self.runway_warning_days, "runway_warning_days"),
        )
        object.__setattr__(
            self,
            "scorecard_threshold",
            _quantize(_clamp_01(self.scorecard_threshold), "scorecard_threshold"),
        )

    def recommend(
        self,
        *,
        scorecards: Sequence[DimensionScore] = (),
        burn_rate: BurnRateForecast | None = None,
        reservation: ReservationRecommendation | None = None,
    ) -> list[Recommendation]:
        """Return ranked recommendations from the supplied signal snapshots.

        Results are sorted by *priority* ascending (most urgent first) and are
        deterministic given identical inputs.
        """
        recommendations: list[Recommendation] = []
        recommendations.extend(_from_burn_rate(burn_rate, self))
        recommendations.extend(_from_reservation(reservation))
        recommendations.extend(_from_scorecards(scorecards, self))
        recommendations.sort(key=_recommendation_sort_key)
        return recommendations


# ---------------------------------------------------------------------------
# Signal → recommendation mappers
# ---------------------------------------------------------------------------


def _from_burn_rate(
    burn_rate: BurnRateForecast | None,
    engine: RecommendationEngine,
) -> list[Recommendation]:
    if burn_rate is None:
        return []
    recommendations: list[Recommendation] = []
    runway = burn_rate.runway_days
    if runway is not None and runway <= engine.runway_critical_days:
        recommendations.append(
            Recommendation(
                action="reduce_spend_or_increase_budget",
                category=RecommendationCategory.BUDGET,
                target_provider_id=None,
                target_capability_id=None,
                rationale=(
                    f"Budget runway is critically short: {runway} days "
                    f"(burn rate {burn_rate.burn_rate_usd_per_day} USD/day)"
                ),
                estimated_impact_usd=burn_rate.remaining_budget_usd,
                confidence=burn_rate.confidence,
                source_signals=("burn_rate:runway",),
                priority=2,
            )
        )
    elif runway is not None and runway <= engine.runway_warning_days:
        recommendations.append(
            Recommendation(
                action="review_spend_trend",
                category=RecommendationCategory.BUDGET,
                target_provider_id=None,
                target_capability_id=None,
                rationale=(
                    f"Budget runway is {runway} days; monitor closely (trend: {burn_rate.trend})"
                ),
                estimated_impact_usd=burn_rate.remaining_budget_usd,
                confidence=burn_rate.confidence,
                source_signals=("burn_rate:runway",),
                priority=6,
            )
        )
    if burn_rate.trend == "increasing":
        recommendations.append(
            Recommendation(
                action="investigate_spend_acceleration",
                category=RecommendationCategory.BUDGET,
                target_provider_id=None,
                target_capability_id=None,
                rationale=(
                    f"Spend is accelerating (burn rate {burn_rate.burn_rate_usd_per_day} USD/day)"
                ),
                estimated_impact_usd=burn_rate.remaining_budget_usd,
                confidence=burn_rate.confidence,
                source_signals=("burn_rate:trend",),
                priority=8,
            )
        )
    return recommendations


def _from_reservation(
    reservation: ReservationRecommendation | None,
) -> list[Recommendation]:
    if reservation is None:
        return []
    recommendations: list[Recommendation] = []
    if reservation.action == "reserve":
        savings = reservation.projected_savings_usd
        recommendations.append(
            Recommendation(
                action="reserve_capacity",
                category=RecommendationCategory.CAPACITY,
                target_provider_id=None,
                target_capability_id=None,
                rationale=(
                    f"Reservation plan {reservation.recommended.plan_id!r} "
                    f"saves {savings} USD vs on-demand"
                ),
                estimated_impact_usd=savings,
                confidence=Decimal("0.9"),
                source_signals=("reservation:recommendation",),
                priority=4,
            )
        )
    elif reservation.action == "blocked":
        recommendations.append(
            Recommendation(
                action="review_provider_pool",
                category=RecommendationCategory.CAPACITY,
                target_provider_id=None,
                target_capability_id=None,
                rationale=(
                    "No viable reservation plan meets demand; review provider pool and pricing"
                ),
                estimated_impact_usd=Decimal("0"),
                confidence=Decimal("0.7"),
                source_signals=("reservation:blocked",),
                priority=3,
            )
        )
    return recommendations


def _from_scorecards(
    scorecards: Sequence[DimensionScore],
    engine: RecommendationEngine,
) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for metric in scorecards:
        gap = metric.benchmark - metric.score
        if gap > engine.scorecard_threshold:
            recommendations.append(
                Recommendation(
                    action="switch_to_better_provider",
                    category=RecommendationCategory.SCORECARD,
                    target_provider_id=metric.provider_id,
                    target_capability_id=metric.capability_id,
                    rationale=(
                        f"{metric.dimension} score {metric.score} is "
                        f"{gap} below benchmark {metric.benchmark}"
                        + (f"; {metric.message}" if metric.message else "")
                    ),
                    estimated_impact_usd=Decimal("0"),
                    confidence=_quantize(Decimal(min(1.0, float(gap) * 2)), "confidence"),
                    source_signals=(f"scorecard:{metric.dimension}",),
                    priority=12,
                )
            )
    return recommendations


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def _recommendation_sort_key(rec: Recommendation) -> tuple[int, Decimal, str]:
    return rec.priority, Decimal("1") - rec.confidence, rec.action


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _quantize(value: Decimal, name: str) -> Decimal:
    try:
        return value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
    except Exception as exc:  # reason: convert any quantize failure to a named ValueError
        raise ValueError(f"{name} is out of representable range: {value}") from exc


def _clamp_01(value: Decimal) -> Decimal:
    try:
        d = Decimal(str(value))
    except Exception as exc:  # reason: convert any Decimal parse failure to a named ValueError
        raise ValueError("value must be a decimal between 0 and 1") from exc
    if not d.is_finite():
        raise ValueError("value must be finite")
    if d < 0:
        return Decimal("0")
    if d > 1:
        return Decimal("1")
    return d


def _positive_decimal(value: object, name: str) -> Decimal:
    d = _decimal(value, name)
    if d <= 0:
        raise ValueError(f"{name} must be positive")
    return d


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a decimal value")
    try:
        d = Decimal(str(value))
    except Exception as exc:  # reason: convert any Decimal parse failure to a named ValueError
        raise ValueError(f"{name} must be a decimal value") from exc
    if not d.is_finite():
        raise ValueError(f"{name} must be finite")
    return d


def _signed_usd(value: object, name: str) -> Decimal:
    return _quantize(_decimal(value, name), name)


def _decimal_to_str(value: Decimal) -> str:
    return format(value, "f")


__all__ = [
    "Recommendation",
    "RecommendationCategory",
    "RecommendationEngine",
    "DimensionScore",
]
