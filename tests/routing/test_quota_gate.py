"""Stage-2 quota gate and Stage-3 score terms (research §9.3)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.routing.quota import QuotaRecord, QuotaSnapshot, quota_eligible, quota_score_terms

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


@pytest.mark.parametrize(
    ("provider", "record", "expected"),
    [
        (_provider("per_token"), None, (True, None)),  # metered: budget gate owns it
        (_provider(), None, (False, "zero-priced without catalog evidence")),  # fail closed
        (_provider(), _record(tos_verdict="avoid"), (False, "tos-avoid")),
        (_provider(), _record(used_units=Decimal("5000000")), (False, "quota-exhausted")),
        (_provider(), _record(), (True, None)),
        (
            _provider(),
            _record(budget_units=None, free_type="keyless"),
            (True, None),
        ),  # uncapped keyless
    ],
)
def test_quota_eligible_truth_table(provider, record, expected) -> None:
    snapshot = QuotaSnapshot(records=(record,) if record else ())
    assert quota_eligible(provider, snapshot, NOW) == expected


def test_score_terms_are_pure_and_bounded() -> None:
    snapshot = QuotaSnapshot(records=(_record(),))
    terms = quota_score_terms(_provider(), snapshot, NOW)
    assert terms.headroom == pytest.approx(0.8)  # (5M - 1M) / 5M
    assert terms.reset_proximity == pytest.approx(1 / 3)  # 10 of 30 days elapsed
    assert quota_score_terms(_provider("per_token"), snapshot, NOW) == quota_score_terms(
        _provider("per_token"), QuotaSnapshot.empty(), NOW
    )


def test_headroom_is_monotone_in_used_units() -> None:
    last = 2.0
    for used in range(0, 5_000_001, 500_000):
        snapshot = QuotaSnapshot(records=(_record(used_units=Decimal(used)),))
        headroom = quota_score_terms(_provider(), snapshot, NOW).headroom
        assert headroom <= last
        last = headroom
