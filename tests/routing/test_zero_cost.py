"""Strict zero-cost evidence filter — ADR 0007 rule 2 (research §9.5)."""

from __future__ import annotations

import datetime as dt

import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _provider(mode: str = "zero", catalog: dict | None = None) -> Provider:
    catalog = catalog or {}
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
                "catalog": catalog,
            },
        },
    )


@pytest.mark.parametrize(
    ("catalog", "allowed", "reason"),
    [
        ({"free_type": "keyless", "tos": "caution"}, True, None),
        (
            {"free_type": "recurring-monthly", "tos": "ok", "hard_stop_guaranteed": True},
            True,
            None,
        ),
        (
            {"free_type": "recurring-monthly", "tos": "ok"},
            False,
            "hard-stop-not-guaranteed",
        ),
        ({"free_type": "keyless", "tos": "avoid"}, False, "tos-avoid"),
        ({"free_type": "keyless", "tos": "unknown"}, False, "tos-unknown"),
        (
            {
                "free_type": "recurring-monthly",
                "tos": "ok",
                "hard_stop_guaranteed": True,
                "eligibility_gate": "regional-identity",
            },
            False,
            "eligibility-gated",
        ),
        ({"free_type": "discontinued", "tos": "ok"}, False, "discontinued"),
        ({}, False, "no-catalog-evidence"),
    ],
)
def test_zero_cost_evidence_matrix(catalog, allowed, reason) -> None:
    from pitwall.routing.zero_cost import zero_cost_verdict

    verdict = zero_cost_verdict(_provider(catalog=catalog))
    assert (verdict.allowed, verdict.reason) == (allowed, reason)


def test_metered_provider_is_not_subject_to_the_filter() -> None:
    from pitwall.routing.zero_cost import zero_cost_verdict

    assert zero_cost_verdict(_provider(mode="per_token", catalog={})).allowed is True


def test_trains_on_prompts_is_surfaced_in_plan_explanation() -> None:
    from pitwall.routing.scoring import explain_score

    explanation = explain_score(
        _provider(catalog={"free_type": "keyless", "tos": "ok", "trains_on_prompts": True})
    )
    assert explanation.trains_on_prompts is True
    assert explanation.to_dict()["trains_on_prompts"] is True
