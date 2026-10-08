"""Stage-3 explain_score carries weighted quota headroom and reset proximity terms."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.routing.quota import QuotaRecord, QuotaSnapshot
from pitwall.routing.scoring import explain_score

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _provider(mode: str = "zero", pool_key: str | None = "alpha-pool") -> Provider:
    return Provider(
        id="prov_gw",
        capability_id="cap",
        name="gw",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=50,
        updated_at=NOW,
        config={
            "cost": {"kind": mode}
            | (
                {}
                if mode == "zero"
                else {"per_million_input_tokens": "0.1", "per_million_output_tokens": "0.2"}
            ),
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "m",
                "catalog": {
                    "pool_key": pool_key,
                    "free_type": "recurring-monthly",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                },
            },
        },
    )


def _record(**overrides) -> QuotaRecord:
    base = {
        "provider_id": "prov_gw",
        "pool_key": "alpha-pool",
        "free_type": "recurring-monthly",
        "window_start": NOW - dt.timedelta(days=10),
        "reset_at": NOW + dt.timedelta(days=20),
        "budget_units": Decimal("5000000"),
        "used_units": Decimal("1000000"),
        "tos_verdict": "ok",
        "evidence": {},
        "updated_at": NOW,
    }
    base.update(overrides)
    return QuotaRecord(**base)


def test_explain_score_carries_quota_terms_weighted_by_settings() -> None:
    snapshot = QuotaSnapshot(records=(_record(),))
    explanation = explain_score(
        _provider(),
        quota_snapshot=snapshot,
        now=NOW,
        w_quota=10.0,
        w_reset=2.5,
    )
    assert explanation.quota_headroom_bonus == pytest.approx(8.0)
    assert explanation.reset_proximity_bonus == pytest.approx(2.5 / 3)
    assert explanation.to_dict()["quota_headroom_bonus"] == pytest.approx(8.0)
    baseline = explain_score(_provider(), now=NOW)
    assert baseline.quota_headroom_bonus == 0.0 and baseline.final_score < explanation.final_score
