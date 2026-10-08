"""Read-only client for RunPod's v2-only hardware catalogue."""

from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pitwall.runpod_client.pods import RunPodError, _rest_headers, _rest_v2_base_url


class RunPodCatalogV2Error(RunPodError):
    """RunPod's v2 catalogue returned an unexpected response."""


class CudaVersionAvailability(BaseModel):
    """One CUDA version offered for a GPU type and its current capacity."""

    model_config = ConfigDict(extra="ignore")

    version: str
    available: bool


class GpuTypeV2(BaseModel):
    """The v2 catalogue fields Pitwall needs for CUDA-aware pod selection."""

    model_config = ConfigDict(extra="ignore")

    id: str
    memory: int
    pool: str | None = None
    cuda_versions: list[CudaVersionAvailability] = Field(
        default_factory=list,
        alias="cudaVersions",
    )


class ListGpuTypesV2Response(BaseModel):
    """The ``{"gpus": [...]}`` response envelope for ``/catalog/gpus``."""

    model_config = ConfigDict(extra="ignore")

    gpus: list[GpuTypeV2]


class RunPodCatalogV2Client:
    """Synchronous, read-only client for the RunPod v2 GPU catalogue."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        rest_api_url: str | None = None,
        timeout_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=_rest_v2_base_url(rest_api_url),
            headers=_rest_headers(api_key),
            timeout=timeout_s,
            transport=transport,
        )

    def __enter__(self) -> RunPodCatalogV2Client:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""

        self._client.close()

    def list_gpu_types_v2(
        self,
        *,
        product: Literal["POD", "SERVERLESS"] = "POD",
    ) -> list[GpuTypeV2]:
        """List POD GPU types with each type's offered CUDA versions.

        v2 returns the collection inside a required ``gpus`` envelope.  CUDA
        versions are populated by the AVAILABILITY expansion and are scoped to
        POD placement.
        """

        response = self._client.get(
            "/catalog/gpus",
            params={"include": "AVAILABILITY", "product": product},
        )
        response.raise_for_status()
        try:
            return ListGpuTypesV2Response.model_validate(response.json()).gpus
        except (ValidationError, ValueError) as exc:
            raise RunPodCatalogV2Error(
                "GET /catalog/gpus returned an unexpected v2 catalogue response"
            ) from exc


def list_gpu_types_v2(
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
    timeout_s: float = 60.0,
    transport: httpx.BaseTransport | None = None,
    product: Literal["POD", "SERVERLESS"] = "POD",
) -> list[GpuTypeV2]:
    """List RunPod v2 POD GPU types without changing any v1 write path."""

    with RunPodCatalogV2Client(
        api_key=api_key,
        rest_api_url=rest_api_url,
        timeout_s=timeout_s,
        transport=transport,
    ) as client:
        return client.list_gpu_types_v2(product=product)


__all__ = [
    "CudaVersionAvailability",
    "GpuTypeV2",
    "ListGpuTypesV2Response",
    "RunPodCatalogV2Client",
    "RunPodCatalogV2Error",
    "list_gpu_types_v2",
]
