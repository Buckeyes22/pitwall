"""Strict read-only client for RunPod REST v1 billing history.

The current official contract exposes account billing buckets for Pods,
Serverless endpoints, and network volumes.  Only Pod history has both a
resource filter and a resource grouping that can identify one broker-owned
Pod; higher layers decide whether that Pod maps authoritatively to a Pitwall
workload.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal, InvalidOperation
from typing import Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from pitwall.runpod_client.pods import RunPodError, _legacy_rest_base_url, _rest_headers

type BillingBucketSize = Literal["hour", "day", "week", "month", "year"]
type BillingCategory = Literal["pods", "endpoints", "networkvolumes"]
type BillingFailureReason = Literal[
    "authentication_failed",
    "invalid_provider_response",
    "provider_unavailable",
    "timeout",
]


class RunPodBillingError(RunPodError):
    """A RunPod billing read failed without exposing its response body."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: BillingFailureReason,
        status_code: int | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(message)


class RunPodBillingRecord(BaseModel):
    """One provider-reported billing bucket from the official REST contract."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, frozen=True)

    amount: Decimal
    time: dt.datetime
    disk_space_billed_gb: int | None = Field(default=None, alias="diskSpaceBilledGb")
    endpoint_id: str | None = Field(default=None, alias="endpointId")
    gpu_type_id: str | None = Field(default=None, alias="gpuTypeId")
    pod_id: str | None = Field(default=None, alias="podId")
    time_billed_ms: int | None = Field(default=None, alias="timeBilledMs")
    high_performance_storage_amount: Decimal | None = Field(
        default=None,
        alias="highPerformanceStorageAmount",
    )
    high_performance_storage_disk_space_billed_gb: int | None = Field(
        default=None,
        alias="highPerformanceStorageDiskSpaceBilledGb",
    )

    @field_validator("amount", "high_performance_storage_amount", mode="before")
    @classmethod
    def _validate_money(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, (bool, float)):
            raise ValueError("billing money must be decoded without binary floats")
        try:
            amount = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("billing money must be decimal-compatible") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("billing money must be finite and non-negative")
        return amount

    @field_validator("time")
    @classmethod
    def _validate_time(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("billing time must be timezone-aware")
        return value.astimezone(dt.UTC)


class RunPodBillingClient:
    """Async header-authenticated client for billing history GETs only."""

    def __init__(
        self,
        *,
        api_key: str,
        rest_v1_api_url: str | None = None,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=_legacy_rest_base_url(rest_v1_api_url),
            headers=_rest_headers(api_key),
            timeout=timeout_s,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def pod_history(
        self,
        *,
        start_time: dt.datetime,
        end_time: dt.datetime,
        pod_id: str,
        bucket_size: BillingBucketSize = "day",
    ) -> tuple[RunPodBillingRecord, ...]:
        """Return buckets filtered and grouped by one exact Pod id."""

        resource_id = _resource_id(pod_id, "pod_id")
        return await self._history(
            "pods",
            start_time=start_time,
            end_time=end_time,
            bucket_size=bucket_size,
            extra_params={"grouping": "podId", "podId": resource_id},
        )

    async def endpoint_history(
        self,
        *,
        start_time: dt.datetime,
        end_time: dt.datetime,
        endpoint_id: str,
        bucket_size: BillingBucketSize = "day",
    ) -> tuple[RunPodBillingRecord, ...]:
        """Return account buckets grouped by endpoint id.

        These rows are useful read-only account history, but they do not carry
        a Serverless job id and therefore are not workload actuals.
        """

        resource_id = _resource_id(endpoint_id, "endpoint_id")
        return await self._history(
            "endpoints",
            start_time=start_time,
            end_time=end_time,
            bucket_size=bucket_size,
            extra_params={"grouping": "endpointId", "endpointId": resource_id},
        )

    async def network_volume_history(
        self,
        *,
        start_time: dt.datetime,
        end_time: dt.datetime,
        bucket_size: BillingBucketSize = "day",
    ) -> tuple[RunPodBillingRecord, ...]:
        """Return aggregate network-volume billing buckets.

        The official response has no network-volume identifier, so callers
        must not map these account aggregates to individual workloads.
        """

        return await self._history(
            "networkvolumes",
            start_time=start_time,
            end_time=end_time,
            bucket_size=bucket_size,
            extra_params={},
        )

    async def _history(
        self,
        category: BillingCategory,
        *,
        start_time: dt.datetime,
        end_time: dt.datetime,
        bucket_size: BillingBucketSize,
        extra_params: dict[str, str],
    ) -> tuple[RunPodBillingRecord, ...]:
        start, end = _time_window(start_time, end_time)
        params = {
            "bucketSize": bucket_size,
            "startTime": _rfc3339(start),
            "endTime": _rfc3339(end),
            **extra_params,
        }
        path = f"/billing/{category}"
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise RunPodBillingError(
                f"GET {path} timed out",
                reason_code="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise RunPodBillingError(
                f"GET {path} failed",
                reason_code="provider_unavailable",
            ) from exc
        if response.status_code >= 400:
            reason: BillingFailureReason = (
                "authentication_failed"
                if response.status_code in {401, 403}
                else "provider_unavailable"
            )
            raise RunPodBillingError(
                f"GET {path} failed with HTTP {response.status_code}",
                reason_code=reason,
                status_code=response.status_code,
            )
        records = _decode_records(response.content, path=path)
        return tuple(
            sorted(
                records,
                key=lambda item: (
                    item.time,
                    item.pod_id or "",
                    item.endpoint_id or "",
                    item.gpu_type_id or "",
                ),
            )
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""

        await self._client.aclose()


def _decode_records(content: bytes, *, path: str) -> list[RunPodBillingRecord]:
    try:
        payload = json.loads(content, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunPodBillingError(
            f"GET {path} returned invalid JSON",
            reason_code="invalid_provider_response",
        ) from exc
    if not isinstance(payload, list):
        raise RunPodBillingError(
            f"GET {path} returned an invalid billing envelope",
            reason_code="invalid_provider_response",
        )
    try:
        return [RunPodBillingRecord.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise RunPodBillingError(
            f"GET {path} returned invalid billing records",
            reason_code="invalid_provider_response",
        ) from exc


def _time_window(
    start_time: dt.datetime,
    end_time: dt.datetime,
) -> tuple[dt.datetime, dt.datetime]:
    start = _aware_utc(start_time, "start_time")
    end = _aware_utc(end_time, "end_time")
    if start >= end:
        raise ValueError("start_time must be before end_time")
    return start, end


def _aware_utc(value: dt.datetime, field_name: str) -> dt.datetime:
    if not isinstance(value, dt.datetime):
        raise TypeError(f"{field_name} must be datetime.datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(dt.UTC)


def _rfc3339(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _resource_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()


__all__ = [
    "BillingBucketSize",
    "BillingCategory",
    "BillingFailureReason",
    "RunPodBillingClient",
    "RunPodBillingError",
    "RunPodBillingRecord",
]
