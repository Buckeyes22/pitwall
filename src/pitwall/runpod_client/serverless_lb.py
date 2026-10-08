"""RunPod Serverless Load-Balancer embedding client.

Wraps the LB surface that routes HTTP directly to worker pods:

    https://{ENDPOINT_ID}.api.runpod.ai/{CUSTOM_PATH}

The BGE-M3 worker exposes ``/embed`` on the LB endpoint.

Supports "Pitwall mode" via the ``PITWALL_EMBEDDING_VIA_PITWALL`` feature flag, for callers
outside the broker. When enabled, embedding requests are routed through the Pitwall
``/v1/inference`` endpoint instead of going directly to the RunPod load balancer, authenticated
with ``PITWALL_API_TOKEN``; the RunPod key is only ever sent to the RunPod load balancer. The
broker's own RunPod adapter constructs the client with ``via_pitwall=False``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel

from pitwall.config import load_settings_from_env
from pitwall.runpod_client.retry import (
    RetryPolicy,
    retry_any_transport_failure,
    retry_rate_limit_or_server_error,
    send_with_retry,
)

MAX_REQUEST_BODY_BYTES = 30_000_000
_DEFAULT_RETRY_ATTEMPTS = 4
_DEFAULT_RETRY_BACKOFF_S = 2.0


class EmbeddingResponse(BaseModel):
    """Structured response from a BGE-M3 embedding call."""

    dense: list[list[float]] | None = None
    sparse: list[dict[int, float]] | None = None
    colbert: list[list[list[float]]] | None = None
    raw: dict[str, Any]


class ServerlessLBClient:
    """Async httpx wrapper for RunPod load-balancing Serverless BGE-M3 endpoints.

    ``via_pitwall`` selects the route: ``None`` follows ``PITWALL_EMBEDDING_VIA_PITWALL`` (for
    callers outside the broker), ``False`` always calls the RunPod load balancer directly (the
    broker's own adapter), and ``True`` routes through the Pitwall ``/v1/inference`` endpoint when
    ``PITWALL_BASE_URL`` is set. Pitwall mode authenticates with ``PITWALL_API_TOKEN``; the RunPod
    ``api_key`` is sent only to the load balancer.
    """

    def __init__(
        self,
        *,
        lb_base_url: str,
        api_key: str | None = None,
        via_pitwall: bool | None = None,
        timeout_s: float = 330.0,
        retry_attempts: int = _DEFAULT_RETRY_ATTEMPTS,
        retry_backoff_s: float = _DEFAULT_RETRY_BACKOFF_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if retry_attempts < 1:
            raise ValueError(f"retry_attempts must be >= 1, got {retry_attempts}")
        if retry_backoff_s < 0:
            raise ValueError(f"retry_backoff_s must be >= 0, got {retry_backoff_s}")
        normalized_url = lb_base_url.rstrip("/")
        if normalized_url.endswith("/embed"):
            normalized_url = normalized_url[: -len("/embed")]
        # The RunPod key is attached per direct request, never as a client default: a default
        # header would also ride along on the Pitwall-mode request.
        self._api_key = api_key
        self._via_pitwall = via_pitwall
        self._client = httpx.AsyncClient(
            base_url=normalized_url,
            timeout=timeout_s,
            transport=transport,
        )
        # Embedding requests are idempotent, so every transport failure and 429/5xx replays.
        self._retry_policy = RetryPolicy(
            retry_exception=retry_any_transport_failure,
            retry_status=retry_rate_limit_or_server_error,
            delays=tuple(retry_backoff_s * (2**index) for index in range(retry_attempts - 1)),
            sleep=lambda seconds: asyncio.sleep(seconds),
        )

    async def embed(
        self,
        texts: list[str],
        *,
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert: bool = False,
    ) -> dict[str, Any]:
        settings = load_settings_from_env()
        use_pitwall = (
            settings.pitwall_embedding_via_pitwall
            if self._via_pitwall is None
            else self._via_pitwall
        )
        if use_pitwall and settings.pitwall_base_url:
            return await self._embed_via_pitwall(
                texts,
                return_dense=return_dense,
                return_sparse=return_sparse,
                return_colbert=return_colbert,
            )
        return await self._embed_direct(
            texts,
            return_dense=return_dense,
            return_sparse=return_sparse,
            return_colbert=return_colbert,
        )

    async def _embed_via_pitwall(
        self,
        texts: list[str],
        *,
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert: bool = False,
    ) -> dict[str, Any]:
        settings = load_settings_from_env()
        payload = {
            "capability": "embedding.bge-m3",
            "texts": texts,
            "return_dense": return_dense,
            "return_sparse": return_sparse,
            "return_colbert": return_colbert,
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) >= MAX_REQUEST_BODY_BYTES:
            raise ValueError(
                "Embedding request payload exceeds the RunPod load-balancer 30 MB limit; "
                "chunk smaller before embedding."
            )

        pitwall_url = settings.pitwall_base_url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.pitwall_api_token:
            headers["Authorization"] = f"Bearer {settings.pitwall_api_token}"

        response = await send_with_retry(
            lambda: self._client.post(
                f"{pitwall_url}/v1/inference",
                content=body,
                headers=headers,
            ),
            self._retry_policy,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return {
            "dense": result.get("dense") if return_dense else None,
            "sparse": result.get("sparse") if return_sparse else None,
            "colbert": result.get("colbert") if return_colbert else None,
            "raw": result,
        }

    async def _embed_direct(
        self,
        texts: list[str],
        *,
        return_dense: bool = True,
        return_sparse: bool = True,
        return_colbert: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "texts": texts,
            "return_dense": return_dense,
            "return_sparse": return_sparse,
            "return_colbert": return_colbert,
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) >= MAX_REQUEST_BODY_BYTES:
            raise ValueError(
                "Embedding request payload exceeds the RunPod load-balancer 30 MB limit; "
                "chunk smaller before embedding."
            )

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = await send_with_retry(
            lambda: self._client.post(
                "/embed",
                content=body,
                headers=headers,
            ),
            self._retry_policy,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "dense": data.get("dense") if return_dense else None,
            "sparse": data.get("sparse") if return_sparse else None,
            "colbert": data.get("colbert") if return_colbert else None,
            "raw": data,
        }

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = [
    "EmbeddingResponse",
    "MAX_REQUEST_BODY_BYTES",
    "ServerlessLBClient",
]
