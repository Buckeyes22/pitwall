"""Tests for NetworkVolumeClient — RunPod network volume CRUD + S3 file access."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from botocore.exceptions import ClientError

from pitwall.runpod_client.mounts import (
    NetworkVolume,
    NetworkVolumeClient,
    S3Object,
    S3ObjectPreconditionFailed,
)
from pitwall.runpod_client.pods import RunPodError, RunPodRestError

pytestmark = pytest.mark.anyio

BASE_URL = "https://api.runpod.test/v2"


def _client(*, transport: httpx.AsyncBaseTransport | None = None) -> NetworkVolumeClient:
    return NetworkVolumeClient(
        api_key="test-key",
        rest_base_url=BASE_URL,
        s3_access_key="s3-ak",
        s3_secret_key="s3-sk",
        transport=transport,
    )


@respx.mock
async def test_create_returns_network_volume() -> None:
    route = respx.post(f"{BASE_URL}/network-volumes")
    route.mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "vol-1",
                "name": "my-volume",
                "size": 100,
                "dataCenter": "US-KS-2",
            },
        )
    )

    client = _client()
    volume = await client.create(name="my-volume", size_gb=100, dc="US-KS-2")
    await client.aclose()

    assert volume.id == "vol-1"
    assert volume.name == "my-volume"
    assert volume.size == 100
    assert volume.data_center_id == "US-KS-2"
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body == {"name": "my-volume", "size": 100, "dataCenter": "US-KS-2"}


@respx.mock
async def test_create_raises_on_non_dict_response() -> None:
    respx.post(f"{BASE_URL}/network-volumes").mock(return_value=httpx.Response(200, json=[1, 2, 3]))

    client = _client()
    with pytest.raises(RunPodError, match="unexpected type"):
        await client.create(name="v", size_gb=10, dc="US-KS-2")
    await client.aclose()


@respx.mock
async def test_get_returns_network_volume() -> None:
    respx.get(f"{BASE_URL}/network-volumes/vol-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "vol-1",
                "name": "my-volume",
                "size": 100,
                "dataCenter": "US-KS-2",
            },
        )
    )

    client = _client()
    volume = await client.get("vol-1")
    await client.aclose()

    assert isinstance(volume, NetworkVolume)
    assert volume.id == "vol-1"


@respx.mock
async def test_get_raises_rest_error_on_404() -> None:
    respx.get(f"{BASE_URL}/network-volumes/missing").mock(
        return_value=httpx.Response(404, text="not found")
    )

    client = _client()
    with pytest.raises(RunPodRestError) as exc_info:
        await client.get("missing")
    assert exc_info.value.status_code == 404
    await client.aclose()


@respx.mock
async def test_list_returns_volumes() -> None:
    respx.get(f"{BASE_URL}/network-volumes").mock(
        return_value=httpx.Response(
            200,
            json={
                "networkVolumes": [
                    {
                        "id": "vol-1",
                        "name": "volume-a",
                        "size": 50,
                        "dataCenter": "US-KS-2",
                    },
                    {
                        "id": "vol-2",
                        "name": "volume-b",
                        "size": 100,
                        "dataCenter": "EU-RO-1",
                    },
                ]
            },
        )
    )

    client = _client()
    volumes = await client.list()
    await client.aclose()

    assert len(volumes) == 2
    assert volumes[0].id == "vol-1"
    assert volumes[1].data_center_id == "EU-RO-1"


@respx.mock
async def test_list_returns_empty_list() -> None:
    respx.get(f"{BASE_URL}/network-volumes").mock(
        return_value=httpx.Response(200, json={"networkVolumes": []})
    )

    client = _client()
    volumes = await client.list()
    await client.aclose()

    assert volumes == []


@respx.mock
async def test_list_raises_on_non_list_response() -> None:
    respx.get(f"{BASE_URL}/network-volumes").mock(
        return_value=httpx.Response(200, json={"error": "unexpected"})
    )

    client = _client()
    with pytest.raises(RunPodError, match="networkVolumes envelope"):
        await client.list()
    await client.aclose()


@respx.mock
async def test_update_returns_network_volume() -> None:
    get_route = respx.get(f"{BASE_URL}/network-volumes/vol-1")
    get_route.mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "vol-1",
                "name": "my-volume",
                "size": 100,
                "dataCenter": "US-KS-2",
            },
        )
    )
    route = respx.patch(f"{BASE_URL}/network-volumes/vol-1")
    route.mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "vol-1",
                "name": "my-volume",
                "size": 200,
                "dataCenter": "US-KS-2",
            },
        )
    )

    client = _client()
    volume = await client.update("vol-1", size_gb=200)
    await client.aclose()

    assert volume.size == 200
    assert get_route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body == {"size": 200}


@respx.mock
async def test_delete_succeeds() -> None:
    route = respx.delete(f"{BASE_URL}/network-volumes/vol-1")
    route.mock(return_value=httpx.Response(204))

    client = _client()
    await client.delete("vol-1")
    await client.aclose()

    assert route.call_count == 1


@respx.mock
async def test_delete_is_idempotent_on_404() -> None:
    route = respx.delete(f"{BASE_URL}/network-volumes/vol-1")
    route.mock(return_value=httpx.Response(404, text="not found"))

    client = _client()
    await client.delete("vol-1")
    await client.aclose()

    assert route.call_count == 1


@respx.mock
async def test_delete_raises_on_non_404_error() -> None:
    respx.delete(f"{BASE_URL}/network-volumes/vol-1").mock(
        return_value=httpx.Response(500, text="internal error")
    )

    client = _client()
    with pytest.raises(RunPodRestError) as exc_info:
        await client.delete("vol-1")
    assert exc_info.value.status_code == 500
    await client.aclose()


@respx.mock
async def test_rest_error_includes_method_path_status_body() -> None:
    respx.get(f"{BASE_URL}/network-volumes/vol-1").mock(
        return_value=httpx.Response(418, text="i am a teapot")
    )

    client = _client()
    with pytest.raises(RunPodRestError) as exc_info:
        await client.get("vol-1")
    exc = exc_info.value
    assert exc.method == "GET"
    assert exc.path == "/network-volumes/vol-1"
    assert exc.status_code == 418
    assert exc.body == "i am a teapot"
    await client.aclose()


# --- S3 operations (monkeypatched boto3) -------------------------------------


def _fake_boto3_client(
    *,
    list_pages: list[dict[str, Any]] | None = None,
    get_body: bytes = b"",
) -> Any:
    """Return a MagicMock boto3 S3 client."""
    client = MagicMock()

    if list_pages is not None:
        paginator = MagicMock()
        paginator.paginate.return_value = list_pages
        client.get_paginator.return_value = paginator

    if get_body:
        body_mock = MagicMock()
        body_mock.read.return_value = get_body
        client.get_object.return_value = {"Body": body_mock}

    return client


def _client_error(code: str, status: int) -> Exception:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "GetObject",
    )


async def test_s3_list_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _fake_boto3_client(
        list_pages=[
            {
                "Contents": [
                    {"Key": "models/weights.bin", "Size": 1024, "LastModified": "2026-01-01"},
                    {"Key": "data/dataset.json", "Size": 512},
                ]
            }
        ]
    )
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    objects = await client.list_objects("vol-1", "US-KS-2", prefix="models/")
    await client.aclose()

    assert len(objects) == 2
    assert objects[0].key == "models/weights.bin"
    assert objects[0].size == 1024
    assert objects[0].last_modified == "2026-01-01"
    assert objects[1].key == "data/dataset.json"
    assert objects[1].size == 512

    fake_client.get_paginator.assert_called_once_with("list_objects_v2")
    paginator = fake_client.get_paginator.return_value
    paginator.paginate.assert_called_once_with(Bucket="vol-1", Prefix="models/")


async def test_s3_list_objects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _fake_boto3_client(list_pages=[{"Contents": None}])
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    objects = await client.list_objects("vol-1", "US-KS-2")
    await client.aclose()

    assert objects == []


async def test_s3_list_objects_page_is_bounded_and_does_not_paginate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_boto3_client()
    fake_client.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "a", "Size": 1},
            {"Key": "b", "Size": 2},
            {"Key": "c", "Size": 3},
        ],
        "IsTruncated": False,
    }
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    page = await client.list_objects_page("vol-1", "US-KS-2", prefix="models/", max_items=2)
    await client.aclose()

    assert [item.key for item in page.objects] == ["a", "b"]
    assert page.truncated
    fake_client.list_objects_v2.assert_called_once_with(
        Bucket="vol-1",
        Prefix="models/",
        MaxKeys=3,
    )
    fake_client.get_paginator.assert_not_called()


def _range_response(
    body: MagicMock, status: int, content_range: str | None = None
) -> dict[str, object]:
    response: dict[str, object] = {
        "Body": body,
        "ResponseMetadata": {"HTTPStatusCode": status},
    }
    if content_range is not None:
        response["ContentRange"] = content_range
    return response


async def test_s3_range_read_enforces_bound_and_closes_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_boto3_client()
    body = MagicMock()
    body.read.return_value = b"abc"
    fake_client.get_object.return_value = _range_response(body, 206, "bytes 2-4/100")
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    data = await client.get_object_range("vol-1", "US-KS-2", "file.txt", offset=2, max_bytes=3)
    await client.aclose()

    assert data == b"abc"
    fake_client.get_object.assert_called_once_with(
        Bucket="vol-1", Key="file.txt", Range="bytes=2-4"
    )
    body.read.assert_called_once_with(4)
    body.close.assert_called_once_with()


async def test_s3_range_read_rejects_provider_body_larger_than_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_boto3_client()
    body = MagicMock()
    body.read.return_value = b"abcd"
    fake_client.get_object.return_value = {"Body": body}
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    with pytest.raises(RunPodError, match="exceeded the requested bounded range"):
        await client.get_object_range("vol-1", "US-KS-2", "file.txt", offset=0, max_bytes=3)
    await client.aclose()


async def _range_read(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    *,
    offset: int,
    max_bytes: int,
) -> bytes:
    fake_client = _fake_boto3_client()
    fake_client.get_object.return_value = response
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)
    client = _client()
    try:
        return await client.get_object_range(
            "vol-1", "US-KS-2", "file.txt", offset=offset, max_bytes=max_bytes
        )
    finally:
        await client.aclose()


def _body(data: bytes) -> MagicMock:
    body = MagicMock()
    body.read.return_value = data
    return body


async def test_s3_range_read_rejects_a_server_that_ignored_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RFC 9110 14.2: a server may answer 200 with the whole object; that is not offset 5."""
    with pytest.raises(RunPodError, match="did not honor the requested range"):
        await _range_read(
            monkeypatch,
            _range_response(_body(b"abcdefghij"), 200),
            offset=5,
            max_bytes=20,
        )


async def test_s3_range_read_accepts_a_partial_response_starting_at_the_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = await _range_read(
        monkeypatch,
        _range_response(_body(b"fghij"), 206, "bytes 5-24/100"),
        offset=5,
        max_bytes=20,
    )

    assert data == b"fghij"


@pytest.mark.parametrize("content_range", ["bytes 0-19/100", "bytes 6-25/100", "garbage", None])
async def test_s3_range_read_rejects_a_partial_response_for_another_range(
    monkeypatch: pytest.MonkeyPatch, content_range: str | None
) -> None:
    with pytest.raises(RunPodError, match="did not honor the requested range"):
        await _range_read(
            monkeypatch,
            _range_response(_body(b"abcdefghij"), 206, content_range),
            offset=5,
            max_bytes=20,
        )


async def test_s3_range_read_at_offset_zero_accepts_a_whole_body_that_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = await _range_read(
        monkeypatch, _range_response(_body(b"abcdefghij"), 200), offset=0, max_bytes=20
    )

    assert data == b"abcdefghij"


async def test_s3_range_read_past_end_returns_empty_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_boto3_client()
    fake_client.get_object.side_effect = _client_error("InvalidRange", 416)
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)
    client = _client()

    data = await client.get_object_range("vol", "US-KS-2", "empty.txt", offset=0, max_bytes=16)
    await client.aclose()

    assert data == b""


async def test_s3_range_read_missing_key_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.runpod_client.mounts import S3ObjectNotFound

    fake_client = _fake_boto3_client()
    fake_client.get_object.side_effect = _client_error("NoSuchKey", 404)
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)
    client = _client()

    with pytest.raises(S3ObjectNotFound):
        await client.get_object_range("vol", "US-KS-2", "missing", offset=0, max_bytes=16)
    await client.aclose()


async def test_s3_put_object(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _fake_boto3_client()
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    await client.put_object("vol-1", "US-KS-2", "file.txt", b"hello")
    await client.aclose()

    fake_client.put_object.assert_called_once_with(Bucket="vol-1", Key="file.txt", Body=b"hello")


async def test_s3_create_only_put_uses_strict_if_none_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_boto3_client()
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    await client.put_object(
        "vol-1",
        "US-KS-2",
        "file.txt",
        b"hello",
        create_only=True,
    )
    await client.aclose()

    fake_client.put_object.assert_called_once_with(
        Bucket="vol-1",
        Key="file.txt",
        Body=b"hello",
        IfNoneMatch="*",
    )


async def test_s3_create_only_precondition_failure_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _fake_boto3_client()
    fake_client.put_object.side_effect = ClientError(
        {
            "Error": {"Code": "PreconditionFailed", "Message": "unsafe provider detail"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        },
        "PutObject",
    )
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    with pytest.raises(S3ObjectPreconditionFailed, match="conditional PutObject"):
        await client.put_object(
            "vol-1",
            "US-KS-2",
            "file.txt",
            b"hello",
            create_only=True,
        )
    await client.aclose()


async def test_s3_get_object(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _fake_boto3_client(get_body=b"file-contents")
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    data = await client.get_object("vol-1", "US-KS-2", "file.txt")
    await client.aclose()

    assert data == b"file-contents"
    fake_client.get_object.assert_called_once_with(Bucket="vol-1", Key="file.txt")


async def test_s3_delete_object(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _fake_boto3_client()
    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: fake_client)

    client = _client()
    await client.delete_object("vol-1", "US-KS-2", "file.txt")
    await client.aclose()

    fake_client.delete_object.assert_called_once_with(Bucket="vol-1", Key="file.txt")


async def test_s3_endpoint_uses_lowercase_dc(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def capture_client(*args: Any, **kwargs: Any) -> Any:
        captured["endpoint_url"] = kwargs.get("endpoint_url", "")
        return _fake_boto3_client()

    monkeypatch.setattr("boto3.client", capture_client)

    client = _client()
    await client.put_object("vol-1", "US-KS-2", "k", b"v")
    await client.aclose()

    assert captured["endpoint_url"] == "https://s3api-us-ks-2.runpod.io"


async def test_s3_rejects_hostile_datacenter_before_creating_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boto3_calls: list[dict[str, Any]] = []

    def capture_client(*args: Any, **kwargs: Any) -> Any:
        boto3_calls.append(kwargs)
        return _fake_boto3_client()

    monkeypatch.setattr("boto3.client", capture_client)

    client = _client()
    with pytest.raises(RunPodError, match="invalid RunPod data center ID"):
        await client.put_object("vol-1", "US-KS-2.evil.example", "k", b"v")
    await client.aclose()

    assert boto3_calls == []


@pytest.mark.parametrize("dc", ["US-KS-2/evil", "US-KS-2@evil", "US-KS-2:443"])
async def test_s3_rejects_datacenter_url_delimiters(dc: str) -> None:
    client = _client()
    with pytest.raises(RunPodError, match="invalid RunPod data center ID"):
        await client.list_objects("vol-1", dc)
    await client.aclose()


async def test_s3_missing_boto3_raises_runpod_error() -> None:
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"boto3": None}):
        client = _client()
        with pytest.raises(RunPodError, match="boto3 is required"):
            await client.list_objects("vol-1", "US-KS-2")
        await client.aclose()


# --- Credential resolution tests -------------------------------------------


def test_credentials_from_constructor() -> None:
    client = NetworkVolumeClient(
        api_key="ak",
        rest_base_url="https://custom.runpod.io/v2",
        s3_access_key="s3-ak",
        s3_secret_key="s3-sk",
    )
    assert client._api_key == "ak"
    assert client._rest_base_url == "https://custom.runpod.io/v2"
    assert client._s3_access_key == "s3-ak"
    assert client._s3_secret_key == "s3-sk"


def test_credentials_fallback_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "env-ak")
    monkeypatch.setenv("RUNPOD_S3_ACCESS_KEY", "env-s3-ak")
    monkeypatch.setenv("RUNPOD_S3_SECRET_KEY", "env-s3-sk")

    client = NetworkVolumeClient()
    assert client._api_key == "env-ak"
    assert client._s3_access_key == "env-s3-ak"
    assert client._s3_secret_key == "env-s3-sk"


def test_s3_credentials_fallback_to_aws_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNPOD_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("RUNPOD_S3_SECRET_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-ak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-sk")

    client = NetworkVolumeClient()
    assert client._s3_access_key == "aws-ak"
    assert client._s3_secret_key == "aws-sk"


def test_explicit_s3_only_client_does_not_fall_back_to_control_plane_or_aws_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "control-canary")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-access-canary")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-canary")

    client = NetworkVolumeClient(
        s3_access_key="s3-access",
        s3_secret_key="s3-sk",
        resolve_env_credentials=False,
    )

    assert client._api_key == ""
    assert client._s3_access_key == "s3-access"
    assert client._s3_secret_key == "s3-sk"


# --- Pydantic model tests --------------------------------------------------


def test_network_volume_parses_camel_case() -> None:
    vol = NetworkVolume.model_validate(
        {"id": "v1", "name": "test", "size": 50, "dataCenter": "US-KS-2"}
    )
    assert vol.data_center_id == "US-KS-2"


def test_network_volume_serializes_snake_case() -> None:
    vol = NetworkVolume(id="v1", name="test", size=50, dataCenter="US-KS-2")
    assert vol.model_dump(by_alias=True)["dataCenter"] == "US-KS-2"


def test_s3_object_defaults() -> None:
    obj = S3Object(key="a.txt", size=10)
    assert obj.last_modified is None


# --- Transport injection test ----------------------------------------------


async def test_transport_injection_via_mock_transport(
    mock_transport: Any,
) -> None:
    transport = mock_transport(
        json={
            "id": "vol-1",
            "name": "t",
            "size": 10,
            "dataCenter": "US-KS-2",
        }
    )
    client = NetworkVolumeClient(
        api_key="k",
        rest_base_url=BASE_URL,
        transport=transport,
    )
    volume = await client.get("vol-1")
    await client.aclose()

    assert volume.id == "vol-1"
    assert len(mock_transport.requests) == 1
    req = mock_transport.requests[0]
    assert req.headers["authorization"] == "Bearer k"
    assert str(req.url) == f"{BASE_URL}/network-volumes/vol-1"
