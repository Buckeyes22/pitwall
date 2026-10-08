"""RunPodProvider.infer serves embedding requests only and refuses everything else typed."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.providers import InferenceRequest, ProviderOperationContext, RunPodProvider
from pitwall.providers import runpod as runpod_provider
from pitwall.providers.runpod import UnsupportedInferenceOperationError
from tests.providers.test_runpod_adapter import _capability, _credentials, _provider_record


def _request(payload: dict[str, Any]) -> InferenceRequest:
    return InferenceRequest(
        context=ProviderOperationContext(pool="pool"),
        capability=_capability(),
        provider_record=_provider_record().model_copy(update={"runpod_endpoint_id": "endpoint-1"}),
        credentials=_credentials(),
        payload=payload,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"messages": [{"role": "user", "content": "hi"}]},
        {"prompt": "hello"},
        {},
    ],
)
async def test_non_embedding_request_rejected(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    def forbidden(**kwargs: Any) -> None:
        raise AssertionError("a non-embedding request must not reach RunPod")

    monkeypatch.setattr(runpod_provider, "ServerlessLBClient", forbidden)

    with pytest.raises(UnsupportedInferenceOperationError, match="embedding"):
        await RunPodProvider().infer(_request(payload))


def test_capability_description_says_embeddings_only() -> None:
    description = RunPodProvider.sync_inference_description.lower()

    assert "embedding" in description
    assert "only" in description
