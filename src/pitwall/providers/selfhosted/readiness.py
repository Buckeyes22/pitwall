from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from pitwall.providers.selfhosted.profile import SelfHostedProfile

ReadinessState = Literal["ready", "starting", "absent", "unreachable", "unauthorized"]
ModelReadinessState = Literal["ready", "starting"]


@dataclass(frozen=True)
class ReadinessObservation:
    state: ReadinessState
    models: Mapping[str, ModelReadinessState]
    observed_at: datetime
    latency_ms: int


class ReadinessOracle(Protocol):
    async def observe(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        headers: Mapping[str, str],
        model_id: str | None,
    ) -> ReadinessObservation: ...


class LlamaSwapOracle:
    async def observe(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        headers: Mapping[str, str],
        model_id: str | None,
    ) -> ReadinessObservation:
        started = time.perf_counter()
        response = await _get(client, f"{_origin(base_url)}/running", headers)
        if not isinstance(response, httpx.Response):
            return _observation(response, started=started)
        terminal = _response_state(response)
        if terminal is not None:
            return _observation(terminal, started=started)
        try:
            payload = response.json()
            models = _parse_llama_swap_payload(payload)
        except TypeError, ValueError:
            return _observation("unreachable", started=started)
        state = _model_state(models, model_id)
        return _observation(state, models=models, started=started)


class OpenAIModelsOracle:
    """Weak readiness: catalogue presence may begin before model startup completes."""

    async def observe(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        headers: Mapping[str, str],
        model_id: str | None,
    ) -> ReadinessObservation:
        started = time.perf_counter()
        response = await _get(client, f"{base_url.rstrip('/')}/models", headers)
        if not isinstance(response, httpx.Response):
            return _observation(response, started=started)
        terminal = _response_state(response)
        if terminal is not None:
            return _observation(terminal, started=started)
        try:
            ids = {str(item["id"]) for item in response.json()["data"]}
        except TypeError, ValueError, KeyError:
            return _observation("unreachable", started=started)
        models: Mapping[str, ModelReadinessState] = dict.fromkeys(ids, "ready")
        state: ReadinessState = "ready" if model_id is None or model_id in ids else "absent"
        if not ids and model_id is None:
            state = "ready"
        return _observation(state, models=models, started=started)


class HttpHealthOracle:
    def __init__(self, path: str) -> None:
        self.path = path

    async def observe(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        headers: Mapping[str, str],
        model_id: str | None,
    ) -> ReadinessObservation:
        del model_id
        started = time.perf_counter()
        response = await _get(client, f"{_origin(base_url)}{self.path}", headers)
        if not isinstance(response, httpx.Response):
            return _observation(response, started=started)
        state = _response_state(response) or "ready"
        return _observation(state, started=started)


def oracle_for(profile: SelfHostedProfile | None) -> ReadinessOracle:
    if profile is None or profile.readiness.kind == "openai-models":
        return OpenAIModelsOracle()
    if profile.readiness.kind == "llama-swap":
        return LlamaSwapOracle()
    assert profile.readiness.path is not None
    return HttpHealthOracle(profile.readiness.path)


async def _get(
    client: httpx.AsyncClient,
    url: str,
    headers: Mapping[str, str],
) -> httpx.Response | Literal["unreachable"]:
    try:
        return await client.get(url, headers=headers)
    except httpx.HTTPError:
        return "unreachable"


def _response_state(response: httpx.Response) -> ReadinessState | None:
    if response.status_code in {401, 403}:
        return "unauthorized"
    if not response.is_success:
        return "unreachable"
    return None


def _model_state(
    models: Mapping[str, ModelReadinessState],
    model_id: str | None,
) -> ReadinessState:
    if model_id is not None:
        return models.get(model_id, "absent")
    if "starting" in models.values():
        return "starting"
    if "ready" in models.values():
        return "ready"
    return "absent"


def _parse_llama_swap_payload(payload: object) -> dict[str, ModelReadinessState]:
    if not isinstance(payload, list):
        raise TypeError("llama-swap readiness payload must be a list")
    models: dict[str, ModelReadinessState] = {}
    for item in payload:
        if not isinstance(item, Mapping):
            raise TypeError("llama-swap readiness rows must be objects")
        model = item.get("model")
        state = item.get("state")
        if not isinstance(model, str) or not isinstance(state, str):
            raise TypeError("llama-swap readiness model and state must be strings")
        if state == "ready":
            models[model] = "ready"
        elif state == "starting":
            models[model] = "starting"
        else:
            raise ValueError("llama-swap readiness state is invalid")
    return models


def _origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _observation(
    state: ReadinessState,
    *,
    started: float,
    models: Mapping[str, ModelReadinessState] | None = None,
) -> ReadinessObservation:
    return ReadinessObservation(
        state=state,
        models={} if models is None else models,
        observed_at=datetime.now(UTC),
        latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
    )
