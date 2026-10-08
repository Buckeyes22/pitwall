"""Hermetic feature-local MCP contracts for RP-04."""

from __future__ import annotations

import base64
import hashlib

import pytest
from mcp.shared.exceptions import MCPError

from pitwall.mcp import volume_file_specs
from pitwall.mcp.tools import volume_files
from pitwall.runpod_client.pod_logs import BoundedPodLogPayload
from pitwall.runpod_files import (
    VolumeFileConfigurationError,
    VolumeFileService,
    VolumeObject,
    VolumeObjectPage,
)
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
        return VolumeObjectPage(
            tuple(
                VolumeObject(key=key, size=len(value))
                for key, value in sorted(self.objects.items())
                if key.startswith(prefix)
            ),
            False,
        )

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
        return BoundedPodLogPayload(text="ready", bytes_read=5, truncated=False)


def _service() -> tuple[VolumeFileService, FakeStore]:
    store = FakeStore()
    return VolumeFileService(store, FakeLogs()), store


def _mutating_service() -> tuple[VolumeFileService, FakeStore]:
    store = FakeStore()
    journal = FakeVolumeFileMutationJournal()
    return VolumeFileService(store, FakeLogs(), mutation_journal=journal), store


async def test_mcp_read_tools_delegate_to_the_shared_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _service()
    monkeypatch.setattr(volume_files, "get_volume_file_service", lambda: service)

    listing = await volume_files.pitwall_volume_list_objects("vol-1", "US-KS-2")
    chunk = await volume_files.pitwall_volume_read_chunk(
        "vol-1", "US-KS-2", "models/one.bin", max_bytes=3
    )
    logs = await volume_files.pitwall_pod_logs("pod-1", max_lines=1, max_bytes=64)

    assert listing["objects"][0]["key"] == "models/one.bin"
    assert chunk["content_base64"] == "b25l"
    assert logs["logs"][0]["text"] == "ready"
    assert [call[0] for call in store.calls] == ["list", "get"]


async def test_mcp_mutations_require_intent_confirmation_and_stay_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _service()
    monkeypatch.setattr(volume_files, "get_volume_file_service", lambda: service)

    with pytest.raises(MCPError) as confirmation:
        await volume_files.pitwall_volume_delete_object(
            "vol-1",
            "US-KS-2",
            "models/one.bin",
            intent="delete",
            idempotency_key="request-1",
        )
    assert confirmation.value.error.data == {"error": "volume_file_confirmation_required"}

    planned = await volume_files.pitwall_volume_upload_object(
        "vol-1",
        "US-KS-2",
        "models/new.bin",
        base64.b64encode(b"new").decode(),
        intent="upload",
        idempotency_key="request-2",
        dry_run=True,
    )
    assert planned["status"] == "dry_run"
    assert "models/new.bin" not in store.objects
    assert not [call for call in store.calls if call[0] in {"put", "delete"}]

    with pytest.raises(MCPError) as oversized:
        await volume_files.pitwall_volume_upload_object(
            "vol-1",
            "US-KS-2",
            "models/new.bin",
            "A" * (128 * 1024 + 1),
            intent="upload",
            idempotency_key="request-3",
        )
    assert oversized.value.error.data == {"error": "invalid_volume_file_request"}


async def test_mcp_rejects_wrong_explicit_intent_before_provider_construction() -> None:
    with pytest.raises(MCPError) as error:
        await volume_files.pitwall_volume_delete_object(
            "vol-1",
            "US-KS-2",
            "models/one.bin",
            intent="upload",  # type: ignore[arg-type]  # reason: exercise hostile JSON-RPC input
            idempotency_key="request-4",
            confirm_delete=True,
        )

    assert error.value.error.data == {"error": "invalid_volume_file_request"}


async def test_mcp_adapts_safe_configuration_errors_before_any_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        volume_files,
        "get_volume_file_service",
        lambda: (_ for _ in ()).throw(VolumeFileConfigurationError("missing canary")),
    )

    with pytest.raises(MCPError) as error:
        await volume_files.pitwall_pod_logs("pod-1")

    assert error.value.error.data == {"error": "volume_file_not_configured"}
    assert "canary" not in error.value.error.message


async def test_mcp_upload_guardrail_is_structured_pre_provider_and_non_disclosing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _service()
    monkeypatch.setattr(volume_files, "get_volume_file_service", lambda: service)
    secret = "sk-1234567890abcdef1234567890abcdef"

    with pytest.raises(MCPError) as error:
        await volume_files.pitwall_volume_upload_object(
            "vol-1",
            "US-KS-2",
            "models/new.txt",
            base64.b64encode(secret.encode()).decode(),
            intent="upload",
            idempotency_key="mcp-guard-0001",
        )

    assert error.value.error.data == {"error": "pre_spend_payload_rejected"}
    assert secret not in repr(error.value.error)
    assert store.calls == []


def test_feature_local_specs_cover_every_rp04_mcp_operation_without_registering_globally() -> None:
    assert [spec.name for spec in volume_file_specs.VOLUME_FILE_TOOL_SPECS] == [
        "pitwall_volume_list_objects",
        "pitwall_volume_read_chunk",
        "pitwall_volume_upload_object",
        "pitwall_volume_delete_object",
        "pitwall_pod_logs",
    ]


async def test_mcp_upload_apply_writes_verified_bytes_and_replays_without_duplicate_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _mutating_service()
    monkeypatch.setattr(volume_files, "get_volume_file_service", lambda: service)
    body = b"changed-one"
    digest = hashlib.sha256(body).hexdigest()

    applied = await volume_files.pitwall_volume_upload_object(
        "vol-1",
        "US-KS-2",
        "models/one.bin",
        base64.b64encode(body).decode(),
        intent="upload",
        idempotency_key="mcp-upload-0001",
        confirm_overwrite=True,
        expected_sha256=digest,
    )

    assert store.objects["models/one.bin"] == body
    assert applied["status"] == "completed"
    assert applied["operation"] == "upload"
    assert applied["object_key"] == "models/one.bin"
    assert applied["bytes_transferred"] == len(body)
    assert applied["checksum_sha256"] == digest
    assert applied["replayed"] is False
    assert applied["irreversible"] is True
    puts = [call for call in store.calls if call[0] == "put"]
    assert puts == [("put", "vol-1", "US-KS-2", "models/one.bin", body, False)]

    replayed = await volume_files.pitwall_volume_upload_object(
        "vol-1",
        "US-KS-2",
        "models/one.bin",
        base64.b64encode(body).decode(),
        intent="upload",
        idempotency_key="mcp-upload-0001",
        confirm_overwrite=True,
        expected_sha256=digest,
    )

    assert replayed["status"] == "completed"
    assert replayed["replayed"] is True
    assert replayed["checksum_sha256"] == digest
    assert len([call for call in store.calls if call[0] == "put"]) == 1


async def test_mcp_upload_checksum_mismatch_is_stable_and_never_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _mutating_service()
    monkeypatch.setattr(volume_files, "get_volume_file_service", lambda: service)
    original = store.objects["models/one.bin"]
    wrong = hashlib.sha256(b"expected-content").hexdigest()

    with pytest.raises(MCPError) as error:
        await volume_files.pitwall_volume_upload_object(
            "vol-1",
            "US-KS-2",
            "models/one.bin",
            base64.b64encode(b"different-content").decode(),
            intent="upload",
            idempotency_key="mcp-upload-0002",
            confirm_overwrite=True,
            expected_sha256=wrong,
        )

    assert error.value.error.data == {"error": "volume_file_checksum_mismatch"}
    assert error.value.error.code == -31003
    assert store.objects["models/one.bin"] == original
    assert [call for call in store.calls if call[0] == "put"] == []


async def test_mcp_delete_apply_removes_object_and_replays_without_duplicate_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, store = _mutating_service()
    monkeypatch.setattr(volume_files, "get_volume_file_service", lambda: service)

    deleted = await volume_files.pitwall_volume_delete_object(
        "vol-1",
        "US-KS-2",
        "models/one.bin",
        intent="delete",
        idempotency_key="mcp-delete-0001",
        confirm_delete=True,
    )

    assert "models/one.bin" not in store.objects
    assert deleted["status"] == "completed"
    assert deleted["operation"] == "delete"
    assert deleted["object_key"] == "models/one.bin"
    assert deleted["irreversible"] is True
    deletes = [call for call in store.calls if call[0] == "delete"]
    assert deletes == [("delete", "vol-1", "US-KS-2", "models/one.bin")]

    replayed = await volume_files.pitwall_volume_delete_object(
        "vol-1",
        "US-KS-2",
        "models/one.bin",
        intent="delete",
        idempotency_key="mcp-delete-0001",
        confirm_delete=True,
    )

    assert replayed["status"] == "completed"
    assert replayed["replayed"] is True
    assert len([call for call in store.calls if call[0] == "delete"]) == 1
