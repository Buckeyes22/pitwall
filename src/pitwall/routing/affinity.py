"""Cache-affinity pinning for prompt-cache-heavy pools (research §14 Phase 4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AFFINITY_BONUS = 5.0


def affinity_bonus(provider: Any, hints: Any, observed: Any) -> float:
    if hints is None or getattr(hints, "cache_key", None) is None:
        return 0.0
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    catalog = (
        ((config.get("gateway") or {}).get("catalog") or {}) if isinstance(config, Mapping) else {}
    )
    if not bool(catalog.get("prompt_cache", False)):
        return 0.0
    last_key = getattr(observed, "last_cache_key", None)
    return AFFINITY_BONUS if last_key == hints.cache_key else 0.0


__all__ = ["AFFINITY_BONUS", "affinity_bonus"]
