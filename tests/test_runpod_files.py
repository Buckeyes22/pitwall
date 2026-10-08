"""Hermetic contracts for the shared RP-04 volume-file service."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from pitwall.runpod_client.pod_logs import BoundedPodLogPayload
from pitwall.runpod_files import (
    PodLogLine,
    S3CredentialReferences,
    VolumeFileAuditError,
    VolumeFileAuditUnavailable,
    VolumeFileChecksumMismatch,
    VolumeFileConfigurationError,
    VolumeFileConfirmationRequired,
    VolumeFileIdempotencyConflict,
    VolumeFileLimitExceeded,
    VolumeFileLimits,
    VolumeFileMutationAmbiguous,
    VolumeFileMutationAudit,
    VolumeFileMutationJournal,
    VolumeFileNotFound,
    VolumeFilePreconditionConflict,
    VolumeFilePreSpendRejected,
    VolumeFileProviderError,
    VolumeFileResult,
    VolumeFileService,
    VolumeFileTimeout,
    VolumeFileValidationError,
    VolumeObject,
    VolumeObjectPage,
    build_configured_volume_file_service,
)
from pitwall.security.pre_spend import PreSpendInspectionService
from tests.fakes.volume_files import FakeVolumeFileMutationJournal
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.anyio


class FakeStore:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.calls: list[tuple[object, ...]] = []
        self.fail_read: Exception | None = None
        self.short_read = False

    async def list_objects(
        self,
        volume_id: str,
        data_center_id: str,
        *,
        prefix: str,
        max_items: int,
    ) -> VolumeObjectPage:
        self.calls.append(("list", volume_id, data_center_id, prefix, max_items))
        matching = [
            VolumeObject(key=key, size=len(value), last_modified="2026-09-01T00:00:00Z")
            for key, value in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        return VolumeObjectPage(tuple(matching[:max_items]), len(matching) > max_items)

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
        if create_only and key in self.objects:
            raise VolumeFilePreconditionConflict("object already exists")
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
        if self.fail_read is not None:
            raise self.fail_read
        if key not in self.objects:
            from pitwall.runpod_client.mounts import S3ObjectNotFound

            raise S3ObjectNotFound("missing")
        value = self.objects[key]
        if self.short_read:
            return b""
        if offset >= len(value):
            return b""
        return value[offset : offset + max_bytes]

    async def delete_object(self, volume_id: str, data_center_id: str, key: str) -> None:
        self.calls.append(("delete", volume_id, data_center_id, key))
        self.objects.pop(key, None)


class FakeLogs:
    def __init__(self, payload: BoundedPodLogPayload | None = None) -> None:
        self.payload = payload or BoundedPodLogPayload(text="", bytes_read=0, truncated=False)
        self.calls: list[tuple[object, ...]] = []

    async def read(self, pod_id: str, *, max_lines: int, max_bytes: int) -> BoundedPodLogPayload:
        self.calls.append((pod_id, max_lines, max_bytes))
        return self.payload


def _service(
    store: FakeStore | None = None,
    logs: FakeLogs | None = None,
    *,
    limits: VolumeFileLimits | None = None,
    inspection_service: PreSpendInspectionService | None = None,
    mutation_journal: VolumeFileMutationJournal | None = None,
) -> tuple[VolumeFileService, FakeStore, FakeLogs]:
    resolved_store = store or FakeStore()
    resolved_logs = logs or FakeLogs()
    return (
        VolumeFileService(
            resolved_store,
            resolved_logs,
            limits=limits,
            inspection_service=inspection_service,
            mutation_journal=mutation_journal or FakeVolumeFileMutationJournal(),
        ),
        resolved_store,
        resolved_logs,
    )


async def test_list_and_chunk_use_a_stable_bounded_result_model() -> None:
    inspection_service = PreSpendInspectionService()
    service, _, _ = _service(
        FakeStore({"z.bin": b"z", "a.bin": b"abc"}),
        inspection_service=inspection_service,
    )

    listing = await service.list_objects(volume_id="vol-1", data_center_id="US-KS-2")
    chunk = await service.read_object_chunk(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="a.bin",
        max_bytes=3,
    )

    assert [item.key for item in listing.objects] == ["a.bin", "z.bin"]
    assert listing.to_dict()["progress"][0]["sequence"] == 1
    assert chunk.content_base64 == "YWJj"
    assert chunk.bytes_transferred == 3
    assert (
        chunk.checksum_sha256
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"  # pragma: allowlist secret
    )
    assert inspection_service.status().counters.allow == 2


@pytest.mark.parametrize(
    "body",
    [
        b"token sk-1234567890abcdef1234567890abcdef",
        b"contact jane.roe@example.com",
        b"\xff\xfe\x00\x01",
    ],
)
async def test_upload_guardrail_rejects_sensitive_or_opaque_content_before_provider(
    body: bytes,
) -> None:
    inspection_service = PreSpendInspectionService()
    service, store, _ = _service(inspection_service=inspection_service)

    with pytest.raises(VolumeFilePreSpendRejected) as exc_info:
        await service.upload_bytes(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="artifact.bin",
            body=body,
            idempotency_key="guard-upload-1",
        )

    assert exc_info.value.to_response_body() == {"error": "pre_spend_payload_rejected"}
    assert "sk-1234567890abcdef1234567890abcdef" not in str(exc_info.value)
    assert "jane.roe@example.com" not in str(exc_info.value)
    assert store.calls == []
    counters = inspection_service.status().counters
    assert counters.total == 1
    assert counters.allow == 0


async def test_upload_of_text_between_inspection_and_transfer_limits_succeeds() -> None:
    service, store, _ = _service()
    body = ("line of ordinary text\n" * 20000).encode("utf-8")  # about 440 KiB
    assert 262_144 < len(body) < 4 * 1024 * 1024

    result = await service.upload_bytes(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="notes.txt",
        body=body,
        idempotency_key="big-text-1",
    )

    assert result.status == "completed"
    assert store.objects["notes.txt"] == body


async def test_rejected_upload_preview_has_zero_provider_and_guardrail_writes() -> None:
    inspection_service = PreSpendInspectionService()
    service, store, _ = _service(inspection_service=inspection_service)

    with pytest.raises(VolumeFilePreSpendRejected):
        await service.upload_bytes(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="artifact.txt",
            body=b"token sk-1234567890abcdef1234567890abcdef",
            dry_run=True,
            idempotency_key="guard-preview-1",
        )

    assert store.calls == []
    assert inspection_service.status().counters.total == 0


@pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "preview"])
async def test_upload_preview_and_live_share_the_guardrail_input_bound(dry_run: bool) -> None:
    inspection_service = PreSpendInspectionService()
    service, store, _ = _service(inspection_service=inspection_service)

    with pytest.raises(VolumeFilePreSpendRejected):
        await service.upload_bytes(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="artifact.txt",
            body=b"a" * 262_145,
            dry_run=dry_run,
            idempotency_key="guard-limit-preview-live",
        )

    assert store.calls == []


async def test_log_request_guardrail_runs_before_provider_and_never_scans_response() -> None:
    inspection_service = PreSpendInspectionService()
    service, _, logs = _service(inspection_service=inspection_service)

    with pytest.raises(VolumeFilePreSpendRejected):
        await service.read_pod_logs(pod_id="rpa_1234567890abcdef1234567890abcdef")

    assert logs.calls == []
    assert inspection_service.status().counters.block == 1


async def test_upload_requires_overwrite_confirmation_and_dry_run_does_not_write() -> None:
    service, store, _ = _service(FakeStore({"weights.bin": b"old"}))

    with pytest.raises(VolumeFileConfirmationRequired):
        await service.upload_bytes(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="weights.bin",
            body=b"new",
            idempotency_key="overwrite-confirmation",
        )

    planned = await service.upload_bytes(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="weights.bin",
        body=b"new",
        overwrite=True,
        dry_run=True,
    )

    assert planned.status == "dry_run"
    assert store.objects["weights.bin"] == b"old"
    assert not [call for call in store.calls if call[0] == "put"]


async def test_delete_requires_confirmation_and_dry_run_makes_zero_writes() -> None:
    service, store, _ = _service(FakeStore({"weights.bin": b"old"}))

    with pytest.raises(VolumeFileConfirmationRequired):
        await service.delete_object(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="weights.bin",
        )

    planned = await service.delete_object(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="weights.bin",
        confirm_delete=True,
        dry_run=True,
    )

    assert planned.status == "dry_run"
    assert store.objects["weights.bin"] == b"old"
    assert not [call for call in store.calls if call[0] == "delete"]


async def test_upload_exact_replay_is_zero_provider_call_and_conflict_is_pre_provider() -> None:
    journal = FakeVolumeFileMutationJournal()
    service, store, _ = _service(mutation_journal=journal)

    first = await service.upload_bytes(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="models/one.bin",
        body=b"one",
        idempotency_key="upload-exact-replay",
    )
    calls_after_first = tuple(store.calls)
    replay = await service.upload_bytes(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="models/one.bin",
        body=b"one",
        idempotency_key="upload-exact-replay",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert tuple(store.calls) == calls_after_first
    with pytest.raises(VolumeFileIdempotencyConflict):
        await service.upload_bytes(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="models/two.bin",
            body=b"two",
            idempotency_key="upload-exact-replay",
        )
    assert tuple(store.calls) == calls_after_first


async def test_delete_exact_replay_is_zero_provider_call_and_conflict_is_pre_provider() -> None:
    journal = FakeVolumeFileMutationJournal()
    service, store, _ = _service(
        FakeStore({"models/one.bin": b"one", "models/two.bin": b"two"}),
        mutation_journal=journal,
    )

    await service.delete_object(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="models/one.bin",
        confirm_delete=True,
        idempotency_key="delete-exact-replay",
    )
    calls_after_first = tuple(store.calls)
    replay = await service.delete_object(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="models/one.bin",
        confirm_delete=True,
        idempotency_key="delete-exact-replay",
    )

    assert replay.replayed is True
    assert tuple(store.calls) == calls_after_first
    with pytest.raises(VolumeFileIdempotencyConflict):
        await service.delete_object(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="models/two.bin",
            confirm_delete=True,
            idempotency_key="delete-exact-replay",
        )
    assert tuple(store.calls) == calls_after_first


async def test_ambiguous_completion_audit_failure_retries_only_exact_put() -> None:
    class FailFirstCompletion(FakeVolumeFileMutationJournal):
        fail = True

        async def complete(
            self,
            audit: VolumeFileMutationAudit,
            result: VolumeFileResult,
        ) -> None:
            if self.fail:
                self.fail = False
                raise VolumeFileAuditError("audit failed")
            await super().complete(audit, result)

    journal = FailFirstCompletion()
    service, store, _ = _service(mutation_journal=journal)
    request = {
        "volume_id": "vol-1",
        "data_center_id": "US-KS-2",
        "object_key": "models/one.bin",
        "body": b"one",
        "idempotency_key": "ambiguous-put",
    }

    with pytest.raises(VolumeFileAuditError) as failed:
        await service.upload_bytes(**request)
    assert failed.value.changed is True
    assert len([call for call in store.calls if call[0] == "put"]) == 1

    recovered = await service.upload_bytes(**request)
    assert recovered.status == "completed"
    assert len([call for call in store.calls if call[0] == "put"]) == 2
    calls_after_recovery = tuple(store.calls)
    assert (await service.upload_bytes(**request)).replayed is True
    assert tuple(store.calls) == calls_after_recovery

    with pytest.raises(VolumeFileIdempotencyConflict):
        await service.upload_bytes(**{**request, "body": b"different"})
    assert tuple(store.calls) == calls_after_recovery


async def test_concurrent_same_key_uploads_repeat_only_the_same_safe_put() -> None:
    class ConcurrentStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.put_count = 0

        async def put_object(
            self,
            volume_id: str,
            data_center_id: str,
            key: str,
            body: bytes,
            *,
            create_only: bool,
        ) -> None:
            self.put_count += 1
            await asyncio.sleep(0.02)
            await super().put_object(
                volume_id,
                data_center_id,
                key,
                body,
                create_only=create_only,
            )

    store = ConcurrentStore()
    journal = FakeVolumeFileMutationJournal()
    service, _, _ = _service(store, mutation_journal=journal)
    request = {
        "volume_id": "vol-1",
        "data_center_id": "US-KS-2",
        "object_key": "models/one.bin",
        "body": b"same",
        "idempotency_key": "concurrent-exact-put",
    }

    first, second = await asyncio.gather(
        service.upload_bytes(**request),
        service.upload_bytes(**request),
    )

    assert first.status == second.status == "completed"
    assert {first.replayed, second.replayed} == {False, True}
    assert store.put_count == 1
    assert store.objects == {"models/one.bin": b"same"}
    assert (await service.upload_bytes(**request)).replayed is True
    assert store.put_count == 1


async def test_concurrent_distinct_keys_use_provider_conditional_create() -> None:
    class DistinctKeyRaceStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.lookup_count = 0
            self.both_looked_up = asyncio.Event()

        async def list_objects(
            self,
            volume_id: str,
            data_center_id: str,
            *,
            prefix: str,
            max_items: int,
        ) -> VolumeObjectPage:
            # Snapshot both absent lookup results before either conditional Put
            # reaches the provider boundary.
            matching = tuple(
                VolumeObject(key=key, size=len(value), last_modified=None)
                for key, value in sorted(self.objects.items())
                if key.startswith(prefix)
            )
            self.calls.append(("list", volume_id, data_center_id, prefix, max_items))
            if prefix == "configs/shared.json" and self.lookup_count < 2:
                self.lookup_count += 1
                if self.lookup_count == 2:
                    self.both_looked_up.set()
                await self.both_looked_up.wait()
            return VolumeObjectPage(matching[:max_items], len(matching) > max_items)

    store = DistinctKeyRaceStore()
    service, _, _ = _service(store, mutation_journal=FakeVolumeFileMutationJournal())
    request = {
        "volume_id": "vol-1",
        "data_center_id": "US-KS-2",
        "object_key": "configs/shared.json",
    }
    first_body = b'{"owner":"first"}'
    second_body = b'{"owner":"second"}'

    outcomes = await asyncio.gather(
        service.upload_bytes(
            **request,
            body=first_body,
            idempotency_key="distinct-create-a",
        ),
        service.upload_bytes(
            **request,
            body=second_body,
            idempotency_key="distinct-create-b",
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(outcome, VolumeFileResult) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, VolumeFilePreconditionConflict) for outcome in outcomes) == 1
    puts = [call for call in store.calls if call[0] == "put"]
    assert len(puts) == 2
    assert all(call[-1] is True for call in puts)
    winner_index = next(
        index for index, outcome in enumerate(outcomes) if isinstance(outcome, VolumeFileResult)
    )
    winner_body = (first_body, second_body)[winner_index]
    assert store.objects == {"configs/shared.json": winner_body}


async def test_ambiguous_create_retry_never_overwrites_intervening_bytes() -> None:
    class FailFirstCompletion(FakeVolumeFileMutationJournal):
        fail = True

        async def complete(
            self,
            audit: VolumeFileMutationAudit,
            result: VolumeFileResult,
        ) -> None:
            if self.fail:
                self.fail = False
                raise VolumeFileAuditError("audit failed")
            await super().complete(audit, result)

    journal = FailFirstCompletion()
    service, store, _ = _service(mutation_journal=journal)
    request = {
        "volume_id": "vol-1",
        "data_center_id": "US-KS-2",
        "object_key": "configs/owned.json",
        "body": b'{"owner":"pitwall"}',
        "idempotency_key": "intervening-external-write",
    }

    with pytest.raises(VolumeFileAuditError):
        await service.upload_bytes(**request)
    store.objects["configs/owned.json"] = b'{"owner":"external"}'

    with pytest.raises(VolumeFilePreconditionConflict):
        await service.upload_bytes(**request)

    assert store.objects["configs/owned.json"] == b'{"owner":"external"}'
    assert len([call for call in store.calls if call[0] == "put"]) == 2
    assert [call for call in store.calls if call[0] == "get"]
    # The durable conflict is stable and performs no further provider I/O.
    calls_after_conflict = tuple(store.calls)
    with pytest.raises(VolumeFilePreconditionConflict):
        await service.upload_bytes(**request)
    assert tuple(store.calls) == calls_after_conflict


async def test_ambiguous_overwrite_retry_never_replaces_intervening_generation() -> None:
    class FailFirstCompletion(FakeVolumeFileMutationJournal):
        async def complete(
            self,
            audit: VolumeFileMutationAudit,
            result: VolumeFileResult,
        ) -> None:
            del audit, result
            raise VolumeFileAuditError("audit failed")

    journal = FailFirstCompletion()
    object_key = "configs/overwrite.json"
    service, store, _ = _service(
        FakeStore({object_key: b'{"generation":"old"}'}),
        mutation_journal=journal,
    )
    request = {
        "volume_id": "vol-1",
        "data_center_id": "US-KS-2",
        "object_key": object_key,
        "body": b'{"generation":"first"}',
        "overwrite": True,
        "idempotency_key": "ambiguous-overwrite-generation",
    }

    with pytest.raises(VolumeFileAuditError):
        await service.upload_bytes(**request)
    store.objects[object_key] = b'{"generation":"external"}'
    calls_before_retry = tuple(store.calls)

    with pytest.raises(VolumeFileMutationAmbiguous):
        await service.upload_bytes(**request)

    assert tuple(store.calls) == calls_before_retry
    assert store.objects[object_key] == b'{"generation":"external"}'


async def test_ambiguous_delete_retry_never_removes_recreated_generation() -> None:
    class FailFirstCompletion(FakeVolumeFileMutationJournal):
        async def complete(
            self,
            audit: VolumeFileMutationAudit,
            result: VolumeFileResult,
        ) -> None:
            del audit, result
            raise VolumeFileAuditError("audit failed")

    journal = FailFirstCompletion()
    object_key = "configs/delete.json"
    service, store, _ = _service(
        FakeStore({object_key: b'{"generation":"first"}'}),
        mutation_journal=journal,
    )
    request = {
        "volume_id": "vol-1",
        "data_center_id": "US-KS-2",
        "object_key": object_key,
        "confirm_delete": True,
        "idempotency_key": "ambiguous-delete-generation",
    }

    with pytest.raises(VolumeFileAuditError):
        await service.delete_object(**request)
    store.objects[object_key] = b'{"generation":"external"}'
    calls_before_retry = tuple(store.calls)

    with pytest.raises(VolumeFileMutationAmbiguous):
        await service.delete_object(**request)

    assert tuple(store.calls) == calls_before_retry
    assert store.objects[object_key] == b'{"generation":"external"}'


async def test_cancel_after_put_dispatch_keeps_started_audit_for_exact_safe_retry() -> None:
    class AmbiguousStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.put_count = 0
            self.first_changed = asyncio.Event()

        async def put_object(
            self,
            volume_id: str,
            data_center_id: str,
            key: str,
            body: bytes,
            *,
            create_only: bool,
        ) -> None:
            self.put_count += 1
            assert create_only is True
            if key in self.objects:
                raise VolumeFilePreconditionConflict("object already exists")
            self.objects[key] = body
            self.calls.append(("put", volume_id, data_center_id, key, body, create_only))
            if self.put_count == 1:
                self.first_changed.set()
                await asyncio.sleep(60)

    store = AmbiguousStore()
    journal = FakeVolumeFileMutationJournal()
    service, _, _ = _service(store, mutation_journal=journal)
    request = {
        "volume_id": "vol-1",
        "data_center_id": "US-KS-2",
        "object_key": "models/one.bin",
        "body": b"same",
        "idempotency_key": "cancelled-exact-put",
    }

    pending = asyncio.create_task(service.upload_bytes(**request))
    await asyncio.wait_for(store.first_changed.wait(), timeout=HANG_GUARD_SECS)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert "cancelled-exact-put" in journal.started
    assert journal.records == []
    recovered = await service.upload_bytes(**request)
    assert recovered.status == "completed"
    assert store.put_count == 2
    assert store.objects["models/one.bin"] == b"same"


async def test_mutations_require_a_service_owned_durable_audit_sink() -> None:
    store = FakeStore()
    service = VolumeFileService(store, FakeLogs())

    with pytest.raises(VolumeFileAuditUnavailable):
        await service.upload_bytes(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="models/one.bin",
            body=b"one",
            idempotency_key="audit-required",
        )

    assert store.calls == []


async def test_local_download_is_non_replayable_and_reports_post_write_audit_failure(
    tmp_path: Path,
) -> None:
    class FailRecord(FakeVolumeFileMutationJournal):
        async def record(
            self,
            audit: VolumeFileMutationAudit,
            result: VolumeFileResult,
        ) -> None:
            del audit, result
            raise VolumeFileAuditError("audit failed")

    destination = tmp_path / "output.bin"
    destination.write_bytes(b"old")
    service, store, _ = _service(
        FakeStore({"models/one.bin": b"new"}),
        mutation_journal=FailRecord(),
    )

    with pytest.raises(VolumeFileAuditError) as failed:
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="models/one.bin",
            local_root=tmp_path,
            local_path="output.bin",
            overwrite=True,
        )

    assert failed.value.to_response_body() == {
        "error": "volume_file_audit_failed_after_change",
        "changed": True,
    }
    assert destination.read_bytes() == b"new"
    assert len([call for call in store.calls if call[0] == "get"]) == 1


async def test_durable_audit_metadata_contains_no_object_bytes_or_credentials() -> None:
    journal = FakeVolumeFileMutationJournal()
    service, _, _ = _service(mutation_journal=journal)
    canary = b"audit-content-canary"

    await service.upload_bytes(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="models/one.bin",
        body=canary,
        idempotency_key="content-free-audit",
    )

    rendered = repr(journal.started) + repr(journal.records)
    assert canary.decode() not in rendered
    assert "RUNPOD_S3_SECRET_KEY" not in rendered


async def test_download_checksum_mismatch_cleans_the_partial_file(tmp_path: Path) -> None:
    service, _, _ = _service(FakeStore({"data/model.bin": b"payload"}))

    with pytest.raises(VolumeFileChecksumMismatch):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="data/model.bin",
            local_root=tmp_path,
            local_path="model.bin",
            expected_sha256="0" * 64,
        )

    assert not (tmp_path / "model.bin").exists()
    assert not list(tmp_path.glob(".pitwall-volume-*.part"))


async def test_download_of_missing_key_with_many_prefixed_siblings_is_not_found(
    tmp_path: Path,
) -> None:
    store = FakeStore()
    store.objects = {
        "ckpt/step1": b"a",
        "ckpt/step2": b"b",
        "ckpt/step3": b"c",
    }
    service, _, _ = _service(store)

    with pytest.raises(VolumeFileNotFound):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="ckpt/step",
            local_path="step.bin",
            local_root=tmp_path,
        )


async def test_partial_download_cleans_temp_and_preserves_existing_destination(
    tmp_path: Path,
) -> None:
    store = FakeStore({"data/model.bin": b"payload"})
    store.short_read = True
    target = tmp_path / "model.bin"
    target.write_bytes(b"keep")
    service, _, _ = _service(store)

    with pytest.raises(VolumeFileConfirmationRequired):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="data/model.bin",
            local_root=tmp_path,
            local_path="model.bin",
        )

    with pytest.raises(VolumeFileProviderError):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="data/model.bin",
            local_root=tmp_path,
            local_path="model.bin",
            overwrite=True,
        )

    assert target.read_bytes() == b"keep"
    assert not list(tmp_path.glob(".pitwall-volume-*.part"))


async def test_download_destination_race_never_overwrites_a_new_local_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "model.bin"

    class RacingStore(FakeStore):
        async def get_object_range(
            self,
            volume_id: str,
            data_center_id: str,
            key: str,
            *,
            offset: int,
            max_bytes: int,
        ) -> bytes:
            data = await super().get_object_range(
                volume_id,
                data_center_id,
                key,
                offset=offset,
                max_bytes=max_bytes,
            )
            if not target.exists():
                target.write_bytes(b"raced")
            return data

    service, _, _ = _service(RacingStore({"data/model.bin": b"payload"}))

    with pytest.raises(VolumeFileConfirmationRequired):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="data/model.bin",
            local_root=tmp_path,
            local_path="model.bin",
        )

    assert target.read_bytes() == b"raced"
    assert not list(tmp_path.glob(".pitwall-volume-*.part"))


async def test_download_publishes_without_hard_link_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    def no_link(*args: object, **kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "linkat not permitted")

    monkeypatch.setattr(os, "link", no_link)
    store = FakeStore()
    store.objects = {"model.bin": b"weights"}
    service, _, _ = _service(store)

    result = await service.download_to_path(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="model.bin",
        local_path="model.bin",
        local_root=tmp_path,
    )

    assert result.status == "completed"
    assert (tmp_path / "model.bin").read_bytes() == b"weights"


async def test_download_without_hard_links_still_requires_overwrite_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    monkeypatch.setattr(
        os, "link", lambda *a, **k: (_ for _ in ()).throw(PermissionError(errno.EPERM, "no"))
    )
    (tmp_path / "model.bin").write_bytes(b"old")
    store = FakeStore()
    store.objects = {"model.bin": b"weights"}
    service, _, _ = _service(store)

    with pytest.raises(VolumeFileConfirmationRequired):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="model.bin",
            local_path="model.bin",
            local_root=tmp_path,
        )
    assert (tmp_path / "model.bin").read_bytes() == b"old"


@pytest.mark.parametrize("unsafe", ["../escape", "/tmp/escape", "nested/../../escape", "bad\\path"])
async def test_local_path_traversal_is_rejected_before_object_write(
    tmp_path: Path,
    unsafe: str,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"safe")
    service, store, _ = _service()

    with pytest.raises(VolumeFileValidationError):
        await service.upload_file(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="safe.bin",
            local_root=tmp_path,
            local_path=unsafe,
        )

    assert not [call for call in store.calls if call[0] == "put"]


async def test_symlink_and_special_files_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"safe")
    link = tmp_path / "linked.bin"
    link.symlink_to(source)
    fifo = tmp_path / "special.pipe"
    os.mkfifo(fifo)
    service, store, _ = _service()

    for unsafe in ("linked.bin", "special.pipe"):
        with pytest.raises(VolumeFileValidationError):
            await service.upload_file(
                volume_id="vol-1",
                data_center_id="US-KS-2",
                object_key="safe.bin",
                local_root=tmp_path,
                local_path=unsafe,
            )

    assert stat.S_ISFIFO(fifo.lstat().st_mode)
    assert not [call for call in store.calls if call[0] == "put"]


async def test_download_rejects_a_symlinked_parent_before_reading_an_object(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    service, store, _ = _service(FakeStore({"safe.bin": b"safe"}))

    with pytest.raises(VolumeFileValidationError):
        await service.download_to_path(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="safe.bin",
            local_root=tmp_path,
            local_path="linked/output.bin",
        )

    assert not (outside / "output.bin").exists()
    assert not [call for call in store.calls if call[0] == "get"]


async def test_cancelled_upload_does_not_dispatch_a_provider_write() -> None:
    service, store, _ = _service()
    cancelled = asyncio.Event()
    cancelled.set()

    result = await service.upload_bytes(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="safe.bin",
        body=b"safe",
        cancel_event=cancelled,
    )

    assert result.status == "cancelled"
    assert not [call for call in store.calls if call[0] == "put"]


async def test_interrupted_local_file_upload_stops_before_the_provider_write(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abcd")
    limits = VolumeFileLimits(max_transfer_bytes=8, max_chunk_bytes=1)
    service, store, _ = _service(limits=limits)
    cancelled = asyncio.Event()

    async def interrupt_after_first_chunk(_event: object) -> None:
        cancelled.set()

    result = await service.upload_file(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="safe.bin",
        local_root=tmp_path,
        local_path="source.bin",
        cancel_event=cancelled,
        progress=interrupt_after_first_chunk,
    )

    assert result.status == "cancelled"
    assert not [call for call in store.calls if call[0] == "put"]


async def test_cancelled_download_removes_its_private_partial_file(tmp_path: Path) -> None:
    limits = VolumeFileLimits(max_transfer_bytes=8, max_chunk_bytes=1)
    service, _, _ = _service(FakeStore({"safe.bin": b"abcd"}), limits=limits)
    cancelled = asyncio.Event()

    async def interrupt_after_first_write(_event: object) -> None:
        cancelled.set()

    result = await service.download_to_path(
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="safe.bin",
        local_root=tmp_path,
        local_path="output.bin",
        cancel_event=cancelled,
        progress=interrupt_after_first_write,
    )

    assert result.status == "cancelled"
    assert not (tmp_path / "output.bin").exists()
    assert not list(tmp_path.glob(".pitwall-volume-*.part"))


async def test_oversize_and_chunk_limit_fail_before_provider_calls() -> None:
    limits = VolumeFileLimits(max_transfer_bytes=4, max_chunk_bytes=2)
    service, store, _ = _service(limits=limits)

    with pytest.raises(VolumeFileLimitExceeded):
        await service.upload_bytes(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="safe.bin",
            body=b"12345",
        )
    with pytest.raises(VolumeFileLimitExceeded):
        await service.read_object_chunk(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="safe.bin",
            max_bytes=3,
        )

    assert store.calls == []


async def test_read_chunk_of_missing_key_is_not_found() -> None:
    service, _, _ = _service()

    with pytest.raises(VolumeFileNotFound):
        await service.read_object_chunk(
            volume_id="vol-1", data_center_id="US-KS-2", object_key="absent.bin"
        )


async def test_read_chunk_past_end_is_an_empty_completed_chunk() -> None:
    store = FakeStore()
    store.objects = {"tiny.bin": b"abc"}
    service, _, _ = _service(store)

    result = await service.read_object_chunk(
        volume_id="vol-1", data_center_id="US-KS-2", object_key="tiny.bin", offset=3
    )

    assert result.status == "completed"
    assert result.bytes_transferred == 0


async def test_provider_error_and_timeout_are_safe_and_typed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "Bearer rp-secret-canary"
    store = FakeStore({"safe.bin": b"safe"})
    store.fail_read = RuntimeError(canary)
    service, _, _ = _service(store)

    with pytest.raises(VolumeFileProviderError):
        await service.read_object_chunk(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="safe.bin",
        )

    assert canary not in caplog.text

    class SlowStore(FakeStore):
        async def get_object_range(self, *args: object, **kwargs: object) -> bytes:
            await asyncio.sleep(0.05)
            return b"late"

    timed, _, _ = _service(SlowStore(), limits=VolumeFileLimits(timeout_s=0.01))
    with pytest.raises(VolumeFileTimeout):
        await timed.read_object_chunk(
            volume_id="vol-1",
            data_center_id="US-KS-2",
            object_key="safe.bin",
        )


async def test_logs_preserve_order_timestamps_and_redact_known_secret() -> None:
    raw = (
        '[{"timestamp":"2026-09-01T00:00:00Z","message":"first"},'
        '{"timestamp":"2026-09-01T00:00:01Z","message":"Authorization: Bearer secret"}]'
    )
    logs = FakeLogs(BoundedPodLogPayload(text=raw, bytes_read=len(raw), truncated=False))
    service, _, _ = _service(logs=logs)

    result = await service.read_pod_logs(pod_id="pod-1", max_lines=2, max_bytes=1024)

    assert result.logs == (
        PodLogLine(sequence=1, timestamp="2026-09-01T00:00:00Z", text="first"),
        PodLogLine(sequence=2, timestamp="2026-09-01T00:00:01Z", text="Authorization: [REDACTED]"),
    )
    assert "secret" not in str(result.to_dict())
    assert logs.calls == [("pod-1", 2, 1024)]


def test_parse_log_lines_keeps_blank_message_and_empty_logs_list() -> None:
    from pitwall.runpod_files import _parse_log_lines

    blank, _ = _parse_log_lines(
        '[{"timestamp": "2026-09-01T00:00:00Z", "message": ""}]', max_lines=10
    )
    assert blank[0].text == ""
    assert blank[0].timestamp == "2026-09-01T00:00:00Z"

    empty, truncated = _parse_log_lines('{"logs": []}', max_lines=10)
    assert empty == ()
    assert truncated is False


async def test_log_metadata_is_redacted_as_well_as_message_text() -> None:
    raw = '[{"timestamp":"Bearer timestamp-secret","message":"ready"}]'
    logs = FakeLogs(BoundedPodLogPayload(text=raw, bytes_read=len(raw), truncated=False))
    service, _, _ = _service(logs=logs)

    result = await service.read_pod_logs(pod_id="pod-1", max_lines=1, max_bytes=1024)

    assert result.logs[0].timestamp == "Bearer [REDACTED]"
    assert "timestamp-secret" not in str(result.to_dict())


async def test_configured_service_uses_distinct_s3_references_without_serializing_values() -> None:
    references = S3CredentialReferences(
        access_key_env="RP_S3_ACCESS",
        secret_key_env="RP_S3_SECRET",  # pragma: allowlist secret
    )
    service = build_configured_volume_file_service(
        environ={
            "RP_S3_ACCESS": "access-canary",
            "RP_S3_SECRET": "secret-canary",  # pragma: allowlist secret
            "RUNPOD_API_KEY": "control-canary",  # pragma: allowlist secret
        },
        s3_credentials=references,
    )
    try:
        rendered = str(references.to_dict()) + str(service.limits.to_dict())
        assert "access-canary" not in rendered
        assert "secret-canary" not in rendered
        assert "control-canary" not in rendered
    finally:
        await service.aclose()


def test_s3_references_reject_the_actual_control_plane_reference() -> None:
    with pytest.raises(ValueError, match="control-plane token"):
        S3CredentialReferences(access_key_env="RUNPOD_API_KEY")


def test_configured_service_rejects_reused_control_plane_credential_value() -> None:
    with pytest.raises(VolumeFileConfigurationError) as rejected:
        build_configured_volume_file_service(
            environ={
                "RUNPOD_S3_ACCESS_KEY": "reused-canary",
                "RUNPOD_S3_SECRET_KEY": "secret-canary",  # pragma: allowlist secret
                "RUNPOD_API_KEY": "reused-canary",  # pragma: allowlist secret
            }
        )
    assert rejected.value.to_response_body() == {"error": "volume_file_not_configured"}
    assert "reused-canary" not in str(rejected.value.args)


async def test_pod_logs_need_only_the_runpod_key_and_object_calls_refuse_before_journaling(
    tmp_path: Path,
) -> None:
    journal = FakeVolumeFileMutationJournal()
    service = build_configured_volume_file_service(
        environ={"RUNPOD_API_KEY": "control-canary"},  # pragma: allowlist secret
        mutation_journal=journal,
    )
    try:
        refusals = [
            service.list_objects(volume_id="vol-1", data_center_id="US-KS-2"),
            service.read_object_chunk(
                volume_id="vol-1", data_center_id="US-KS-2", object_key="a.txt"
            ),
            service.upload_bytes(
                volume_id="vol-1",
                data_center_id="US-KS-2",
                object_key="a.txt",
                body=b"hello",
                idempotency_key="upload-unconfigured",
            ),
            service.upload_file(
                volume_id="vol-1",
                data_center_id="US-KS-2",
                object_key="a.txt",
                local_root=tmp_path,
                local_path="missing.txt",
                idempotency_key="upload-file-unconfigured",
            ),
            service.download_to_path(
                volume_id="vol-1",
                data_center_id="US-KS-2",
                object_key="a.txt",
                local_root=tmp_path,
                local_path="out.txt",
            ),
            service.delete_object(
                volume_id="vol-1",
                data_center_id="US-KS-2",
                object_key="a.txt",
                confirm_delete=True,
                idempotency_key="delete-unconfigured",
            ),
        ]
        for call in refusals:
            with pytest.raises(VolumeFileConfigurationError):
                await call
        assert journal.started == {} and journal.records == []
    finally:
        await service.aclose()


def test_volume_file_service_classes_stay_under_400_lines() -> None:
    import ast
    import inspect

    from pitwall import runpod_files

    tree = ast.parse(inspect.getsource(runpod_files))
    sizes = {
        node.name: node.end_lineno - node.lineno + 1
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and (node.name.startswith("_Volume") or node.name == "VolumeFileService")
    }
    assert "VolumeFileService" in sizes
    assert {name: size for name, size in sizes.items() if size > 400} == {}
