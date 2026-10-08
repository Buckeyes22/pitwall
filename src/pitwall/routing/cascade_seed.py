"""Tier ladder, and the prong-3 escape hatch (research §9.6).

Pure functions shared by the extractor (``tools/gateway/sync_catalog``) and the
runtime planner (``pitwall.routing.production``).  No I/O is performed here;
``production.py`` resolves ``own_pod_usd_per_hour`` and ``gpu_class`` via
``pitwall.models.fit_options`` before calling ``escape_hatch_message``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from pitwall.cost.estimator import parse_pricing_model
from pitwall.routing.quota import QuotaSnapshot, _provider_id


def _catalog(provider: Any) -> Mapping[str, Any]:
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    gateway = config.get("gateway", {}) if isinstance(config, Mapping) else {}
    return gateway.get("catalog", {}) if isinstance(gateway, Mapping) else {}


def ladder_rank(provider: Any) -> int:
    """Return the pricing-tier rank: own-serve < keyless < budgeted free < cheap metered < other."""

    provider_type = str(
        getattr(provider, "provider_type", None)
        or (provider.get("provider_type") if isinstance(provider, Mapping) else "")
    )
    if provider_type == "pod_lease":
        return 0
    try:
        kind = parse_pricing_model(provider).kind
    except TypeError, ValueError:
        return 4
    catalog = _catalog(provider)
    if kind == "zero" and catalog.get("free_type") == "keyless":
        return 1
    if kind == "zero":
        return 2
    if kind == "per_token":
        return 3
    return 4


def _bare_reason(reason: str) -> str:
    """Strip an optional ``stage:`` prefix so both plan shapes read the same."""
    return reason.rsplit(":", 1)[-1]


def order_ladder(providers: Sequence[Any]) -> tuple[Any, ...]:
    """Stable ascending tier ordering with provider-id tie-break."""

    return tuple(sorted(providers, key=lambda p: (ladder_rank(p), _provider_id(p))))


def escape_hatch_message(
    plan: Any,
    *,
    quota_snapshot: QuotaSnapshot,
    now: dt.datetime,
    own_pod_usd_per_hour: Decimal,
    gpu_class: str,
) -> str | None:
    """Return the prong-3 ``pitwall serve`` proposal when every free pool is exhausted.

    ``build_production_plan`` eliminates a quota-ineligible pool at Stage 2, so it never
    appears in ``ranked_candidates``: the exhausted set is read from the plan's dropped
    reasons. Any executable candidate that is free-tier or metered means the request can
    still be served, so no proposal is made.
    """

    exhausted_ids = {
        provider_id
        for provider_id, reasons in plan.dropped_provider_reasons.items()
        if "quota_ineligible" in {_bare_reason(r) for r in reasons}
    }
    for candidate in plan.ranked_candidates:
        rank = ladder_rank(candidate.provider)
        if rank == 3 or (rank in (1, 2) and candidate.provider_id not in exhausted_ids):
            return None
    if not exhausted_ids:
        return None
    resets: list[dt.datetime] = []
    for record in quota_snapshot.records:
        if record.provider_id in exhausted_ids and record.reset_at is not None:
            resets.append(record.reset_at)
    until = min(resets).astimezone(dt.UTC).strftime("%H:%M") if resets else "unknown"
    model = plan.capability_snapshot.served_model_id or plan.capability_snapshot.name
    return (
        f"all free pools exhausted until {until} UTC; own-pod at "
        f"${own_pod_usd_per_hour:.2f}/hr would cover the gap "
        f"— pitwall serve --model {model} --gpu-class {gpu_class}"
    )


__all__ = [
    "escape_hatch_message",
    "ladder_rank",
    "order_ladder",
]
