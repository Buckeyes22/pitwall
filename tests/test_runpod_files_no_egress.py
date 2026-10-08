"""RP-04 provider seams must remain hermetic with official hosts in the URL."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import httpx
import pytest

from pitwall.runpod_client.mounts import NetworkVolumeClient
from pitwall.runpod_client.pod_logs import BoundedPodLogClient

pytestmark = pytest.mark.anyio


async def test_official_control_plane_log_url_uses_mock_transport_without_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_dns(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("hermetic RP-04 log test attempted DNS")

    monkeypatch.setattr(socket, "getaddrinfo", deny_dns)
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"ready")),
    )
    try:
        result = await client.read("pod-1", max_lines=1, max_bytes=32)
    finally:
        await client.aclose()

    assert result.text == "ready"


async def test_official_s3_endpoint_uses_fake_boto_client_without_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_dns(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("hermetic RP-04 S3 test attempted DNS")

    fake_client = MagicMock()
    fake_client.list_objects_v2.return_value = {"Contents": []}
    monkeypatch.setattr(socket, "getaddrinfo", deny_dns)
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = NetworkVolumeClient(
        s3_access_key="test-s3-access",
        s3_secret_key="s3-sk",  # pragma: allowlist secret
        resolve_env_credentials=False,
    )
    try:
        page = await client.list_objects_page("vol-1", "US-KS-2", max_items=1)
    finally:
        await client.aclose()

    assert page.objects == ()
    assert fake_client.list_objects_v2.call_args.kwargs["Bucket"] == "vol-1"
