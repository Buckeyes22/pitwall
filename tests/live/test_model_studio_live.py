"""Live Model Studio checks; run with PITWALL_MODEL_STUDIO_LIVE=1 and --run-live."""

from __future__ import annotations

import datetime as dt
import os

import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider as ProviderRecord
from pitwall.providers.interface import (
    CredentialReference,
    InferenceRequest,
    ProviderOperationContext,
)
from pitwall.providers.model_studio import ModelStudioProvider, openapi

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("PITWALL_MODEL_STUDIO_LIVE") != "1", reason="live gate off"),
]


def _record() -> ProviderRecord:
    return ProviderRecord(
        id="live_ms",
        capability_id="cap_live",
        name="live",
        adapter_id=ProviderAdapterId.MODEL_STUDIO,
        credential_ref="MODEL_STUDIO_API_KEY",
        provider_type=ProviderType.MODEL_STUDIO,
        config={
            "model_studio": {
                "plan": "token-plan-personal",
                "tier": "pro",
                "model": "qwen3.8-flash",
                "automation": "accept",
            },
            "cost": {"kind": "zero"},
        },
        priority=0,
        updated_at=dt.datetime.now(dt.UTC),
    )


@pytest.mark.anyio
async def test_live_inference_streams_usage() -> None:
    result = await ModelStudioProvider().infer(
        InferenceRequest(
            context=ProviderOperationContext(pool=None),
            capability=None,  # type: ignore[arg-type]  # reason: the adapter never reads it
            provider_record=_record(),
            credentials=CredentialReference(name="MODEL_STUDIO_API_KEY"),
            payload={
                "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
                "max_tokens": 64,
                "enable_thinking": False,
            },
        )
    )
    assert result.content and "pong" in result.content.lower()
    assert result.prompt_tokens and result.completion_tokens
    print(
        f"usage: prompt_tokens={result.prompt_tokens} completion_tokens={result.completion_tokens}"
    )


@pytest.mark.anyio
async def test_live_subscription_stats_or_documented_absence() -> None:
    stats = await openapi.get_subscription_stats(os.environ, now=dt.datetime.now(dt.UTC))
    if stats is None:
        pytest.skip("AccessKey not configured or Personal Edition exposes no stats")
    assert stats.total_credits > 0
