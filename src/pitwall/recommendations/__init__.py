"""Recommendations engine for Pitwall.

Aggregates signals from scorecards, burn-rate forecasting,
and reservation planning into prioritized, actionable operator recommendations.
"""

from __future__ import annotations

from pitwall.recommendations.engine import (
    DimensionScore,
    Recommendation,
    RecommendationCategory,
    RecommendationEngine,
)

__all__ = [
    "Recommendation",
    "RecommendationCategory",
    "RecommendationEngine",
    "DimensionScore",
]
