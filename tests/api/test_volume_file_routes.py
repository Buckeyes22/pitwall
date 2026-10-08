"""Hermetic feature-local REST contracts for RP-04."""

from __future__ import annotations

import base64

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.routes.volume_files import volume_file_router
from pitwall.runpod_client.pod_logs import BoundedPodLogPayload
from pitwall.runpod_files import VolumeFileService, VolumeObject, VolumeObjectPage
from tests.fakes.volume_files import FakeVolumeFileMutationJournal

pytestmark = pytest.mark.anyio


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {"models/one.bin": b"one"}
        self.calls: list[tuple[object, ...]] = []

    async def list_objects(
        self,
        volume_id: str,
        data_center_id: str,
        *,
        prefix: str,
        max_items: int,
    ) -> VolumeObjectPage:
        self.calls.append(("list", volume_id, data_center_id, prefix, max_items))
        entries = [
            VolumeObject(key=key, size=len(value))
            for key, value in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        return VolumeObjectPage(tuple(entries[:max_items]), len(entries) > max_items)

    async def put_object(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        body: bytes,
        *,
        create_only: bool,
    ) -> None:
        self.calls.append(("put", volume_id, data_center_id, key, body, create_only))
        self.objects[key] = body

    async def get_object_range(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        *,
        offset: int,
        max_bytes: int,
    ) -> bytes:
        self.calls.append(("get", volume_id, data_center_id, key, offset, max_bytes))
        return self.objects[key][offset : offset + max_bytes]

    async def delete_object(self, volume_id: str, data_center_id: str, key: str) -> None:
        self.calls.append(("delete", volume_id, data_center_id, key))
        self.objects.pop(key, None)


class FakeLogs:
    async def read(self, pod_id: str, *, max_lines: int, max_bytes: int) -> BoundedPodLogPayload:
        return BoundedPodLogPayload(
            text='[{"timestamp":"2026-09-01T00:00:00Z","message":"ready"}]',
            bytes_read=60,
            truncated=False,
        )


def _app() -> tuple[FastAPI, FakeStore]:
    store = FakeStore()
    app = FastAPI()
    app.state.volume_file_service = VolumeFileService(
        store,
        FakeLogs(),
        mutation_journal=FakeVolumeFileMutationJournal(),
    )
    app.include_router(volume_file_router)
    return app, store


async def test_unconfigured_service_returns_documented_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.api.routes.volume_files import install_volume_file_error_handler

    app = FastAPI()
    app.state.pool = object()  # a pool exists, but S3 credentials do not
    for name in ("RUNPOD_S3_ACCESS_KEY", "RUNPOD_S3_SECRET_KEY", "RUNPOD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    app.include_router(volume_file_router)
    install_volume_file_error_handler(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/v1/volumes/vol-1/objects", params={"data_center_id": "US-KS-2"}
        )

    assert response.status_code == 503
    assert response.json() == {"error": "volume_file_not_configured"}


async def test_rest_read_routes_share_the_bounded_service_result_schema() -> None:
    app, store = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        listing = await client.get("/v1/volumes/vol-1/objects?data_center_id=US-KS-2")
        chunk = await client.get(
            "/v1/volumes/vol-1/objects/models/one.bin?data_center_id=US-KS-2&max_bytes=3"
        )
        logs = await client.get("/v1/pods/pod-1/logs?max_lines=1&max_bytes=64")

    assert listing.status_code == 200
    assert listing.json()["objects"] == [
        {"key": "models/one.bin", "size": 3, "last_modified": None}
    ]
    assert chunk.json()["content_base64"] == "b25l"
    assert logs.json()["logs"][0]["timestamp"] == "2026-09-01T00:00:00Z"
    assert [call[0] for call in store.calls] == ["list", "get"]


async def test_rest_upload_requires_explicit_intent_idempotency_and_overwrite_confirmation() -> (
    None
):
    app, store = _app()
    body = {
        "data_center_id": "US-KS-2",
        "object_key": "models/one.bin",
        "content_base64": base64.b64encode(b"new").decode(),
        "intent": "upload",
        "idempotency_key": "request-1",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post("/v1/admin/volumes/vol-1/objects", json=body)
        planned = await client.post(
            "/v1/admin/volumes/vol-1/objects",
            json={**body, "confirm_overwrite": True, "dry_run": True},
        )
        missing_intent = await client.post(
            "/v1/admin/volumes/vol-1/objects",
            json={key: value for key, value in body.items() if key != "intent"},
        )

    assert denied.status_code == 409
    assert denied.json() == {"error": "volume_file_confirmation_required"}
    assert planned.status_code == 200
    assert planned.json()["status"] == "dry_run"
    assert missing_intent.status_code == 422
    assert store.objects["models/one.bin"] == b"one"
    assert not [call for call in store.calls if call[0] == "put"]


async def test_rest_delete_confirmation_and_invalid_base64_perform_zero_writes() -> None:
    app, store = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid_body = await client.post(
            "/v1/admin/volumes/vol-1/objects",
            json={
                "data_center_id": "US-KS-2",
                "object_key": "new.bin",
                "content_base64": "not base64!",
                "intent": "upload",
                "idempotency_key": "request-2",
            },
        )
        denied_delete = await client.request(
            "DELETE",
            "/v1/admin/volumes/vol-1/objects/models/one.bin",
            json={
                "data_center_id": "US-KS-2",
                "confirm_delete": False,
                "intent": "delete",
                "idempotency_key": "request-3",
            },
        )
        planned_delete = await client.request(
            "DELETE",
            "/v1/admin/volumes/vol-1/objects/models/one.bin",
            json={
                "data_center_id": "US-KS-2",
                "confirm_delete": True,
                "intent": "delete",
                "idempotency_key": "request-3",
                "dry_run": True,
            },
        )

    assert invalid_body.status_code == 422
    assert invalid_body.json() == {"error": "invalid_volume_file_request"}
    assert denied_delete.status_code == 422
    assert planned_delete.status_code == 200
    assert planned_delete.json()["status"] == "dry_run"
    assert store.objects["models/one.bin"] == b"one"
    assert not [call for call in store.calls if call[0] in {"put", "delete"}]


async def test_rest_chunk_and_list_query_bounds_fail_before_service_calls() -> None:
    app, store = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        too_many = await client.get(
            "/v1/volumes/vol-1/objects?data_center_id=US-KS-2&max_items=501"
        )
        too_large = await client.get(
            "/v1/volumes/vol-1/objects/models/one.bin?data_center_id=US-KS-2&max_bytes=131073"
        )

    assert too_many.status_code == 422
    assert too_large.status_code == 422
    assert store.calls == []


async def test_rest_upload_guardrail_rejects_before_provider_without_reflection() -> None:
    app, store = _app()
    secret = b"sk-1234567890abcdef1234567890abcdef"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/admin/volumes/vol-1/objects",
            json={
                "data_center_id": "US-KS-2",
                "object_key": "models/new.txt",
                "content_base64": base64.b64encode(secret).decode(),
                "intent": "upload",
                "idempotency_key": "rest-guard-0001",
            },
        )

    assert response.status_code == 422
    assert response.json() == {"error": "pre_spend_payload_rejected"}
    assert secret.decode() not in response.text
    assert store.calls == []


async def test_rest_validation_never_reflects_oversized_upload_input() -> None:
    app, store = _app()
    secret = "sk-1234567890abcdef1234567890abcdef"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/admin/volumes/vol-1/objects",
            json={
                "data_center_id": "US-KS-2",
                "object_key": "models/new.bin",
                "content_base64": f"{'A' * 200_000}{secret}",
                "intent": "upload",
                "idempotency_key": "rest-oversized-0001",
            },
        )

    assert response.status_code == 422
    assert response.json() == {"error": "invalid_volume_file_request"}
    assert secret not in response.text
    assert len(response.content) < 256
    assert store.calls == []
