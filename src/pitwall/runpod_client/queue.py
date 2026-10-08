"""RunPod Serverless queue-based client.

Wraps the classic RunPod serverless surface:

    POST /v2/{ENDPOINT_ID}/runsync
    POST /v2/{ENDPOINT_ID}/run
    GET  /v2/{ENDPOINT_ID}/status/{JOB_ID}
    GET  /v2/{ENDPOINT_ID}/health
    POST /v2/{ENDPOINT_ID}/cancel/{JOB_ID}
    POST /v2/{ENDPOINT_ID}/purge-queue

Authorization: ``Bearer {RUNPOD_API_KEY}``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import httpx
from pydantic import BaseModel

from pitwall.rate_limits.retry_after import (
    DEFAULT_MAX_RETRY_AFTER_DELAY_S,
    parse_retry_after,
)
from pitwall.runpod_client.retry import (
    ClockFunc,
    RetryPolicy,
    SleepFunc,
    retry_connect_failure,
    retry_rate_limit_only,
    send_with_retry,
    utc_now,
)

log = logging.getLogger("pitwall.runpod_client.queue")

RUNPOD_API_BASE = "https://api.runpod.ai/v2"


class QueueJob(BaseModel):
    """Parsed response from /runsync or /run."""

    id: str
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None
    raw: dict[str, Any]


class QueueHealth(BaseModel):
    """Parsed response from /health."""

    jobs: dict[str, int]
    raw: dict[str, Any]


class QueueCancelResult(BaseModel):
    """Parsed response from /cancel/{job_id}."""

    cancelled: bool
    raw: dict[str, Any]


class QueuePurgeResult(BaseModel):
    """Parsed response from /purge-queue."""

    purged: int
    raw: dict[str, Any]


def _endpoint_url(endpoint_id: str) -> str:
    return f"{RUNPOD_API_BASE}/{endpoint_id}"


class QueueClient:
    """Async httpx wrapper for RunPod queue-based Serverless endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_s: int = 600,
        retry_delays: tuple[float, ...] = (1.0, 3.0, 9.0),
        max_retry_after_s: float = DEFAULT_MAX_RETRY_AFTER_DELAY_S,
        sleep: SleepFunc = asyncio.sleep,
        clock: ClockFunc = utc_now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s
        # Job submission is billable: replay only failures that never reached the server.
        self._submit_policy = RetryPolicy(
            retry_exception=retry_connect_failure,
            retry_status=retry_rate_limit_only,
            delays=retry_delays,
            max_retry_after_s=max_retry_after_s,
            sleep=sleep,
            clock=clock,
        )
        self._transport = transport

    def _client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            timeout=self._timeout_s,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            transport=self._transport,
        )

    async def _retry_post(
        self, base_url: str, path: str, *, json: dict[str, Any]
    ) -> dict[str, Any]:
        async def send() -> httpx.Response:
            async with self._client(base_url) as client:
                return await client.post(path, json=json)

        response = await send_with_retry(send, self._submit_policy)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def runsync(
        self,
        endpoint_id: str,
        *,
        input: dict[str, Any],
        webhook: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> QueueJob:
        """POST /v2/{endpoint_id}/runsync — synchronous execution."""
        base = _endpoint_url(endpoint_id)
        payload: dict[str, Any] = {"input": input}
        if webhook is not None:
            payload["webhook"] = webhook
        if policy is not None:
            payload["policy"] = policy
        response = await self._retry_post(base, "/runsync", json=payload)
        return _parse_queue_job(response)

    async def run(
        self,
        endpoint_id: str,
        *,
        input: dict[str, Any],
        webhook: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> QueueJob:
        """POST /v2/{endpoint_id}/run — asynchronous execution."""
        base = _endpoint_url(endpoint_id)
        payload: dict[str, Any] = {"input": input}
        if webhook is not None:
            payload["webhook"] = webhook
        if policy is not None:
            payload["policy"] = policy
        response = await self._retry_post(base, "/run", json=payload)
        return _parse_queue_job(response)

    async def status(self, endpoint_id: str, job_id: str) -> QueueJob:
        """GET /v2/{endpoint_id}/status/{job_id}."""
        base = _endpoint_url(endpoint_id)
        async with self._client(base) as client:
            response = await client.get(f"/status/{job_id}")
            response.raise_for_status()
            data = response.json()
        return _parse_queue_job(data)

    async def health(self, endpoint_id: str) -> QueueHealth:
        """GET /v2/{endpoint_id}/health."""
        base = _endpoint_url(endpoint_id)
        async with self._client(base) as client:
            response = await client.get("/health")
            response.raise_for_status()
            data = response.json()
        jobs = {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
        return QueueHealth(jobs=jobs, raw=data)

    async def cancel(self, endpoint_id: str, job_id: str) -> QueueCancelResult:
        """POST /v2/{endpoint_id}/cancel/{job_id}."""
        base = _endpoint_url(endpoint_id)
        async with self._client(base) as client:
            response = await client.post(f"/cancel/{job_id}")
            response.raise_for_status()
            data = response.json()
        return QueueCancelResult(
            cancelled=bool(data.get("cancelled", False)),
            raw=dict(data),
        )

    async def purge_queue(self, endpoint_id: str) -> QueuePurgeResult:
        """POST /v2/{endpoint_id}/purge-queue."""
        base = _endpoint_url(endpoint_id)
        async with self._client(base) as client:
            response = await client.post("/purge-queue")
            response.raise_for_status()
            data = response.json()
        return QueuePurgeResult(
            purged=int(data.get("purged", 0)),
            raw=dict(data),
        )


def _extract_endpoint_id(base_url: str) -> str:
    """Extract the endpoint ID from a base URL like ``https://api.runpod.ai/v2/abc123``."""
    stripped = base_url.rstrip("/")
    return stripped.rsplit("/", maxsplit=1)[-1]


def _status_error(response: httpx.Response, message: str) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        message=message,
        request=response.request,
        response=response,
    )


def _parse_queue_job(data: dict[str, Any]) -> QueueJob:
    return QueueJob(
        id=str(data.get("id", "")),
        status=str(data.get("status", "")),
        output=data.get("output"),
        error=data.get("error"),
        raw=data,
    )


def queue_url(endpoint_id: str, path: str = "") -> str:
    """Build a RunPod queue-based serverless URL.

    >>> queue_url("abc123", "/runsync")
    'https://api.runpod.ai/v2/abc123/runsync'
    """
    base = _endpoint_url(endpoint_id)
    if path:
        return f"{base}{path}"
    return base


__all__ = [
    "DEFAULT_MAX_RETRY_AFTER_DELAY_S",
    "RUNPOD_API_BASE",
    "QueueCancelResult",
    "QueueClient",
    "QueueHealth",
    "QueueJob",
    "QueuePurgeResult",
    "parse_retry_after",
    "queue_url",
]
