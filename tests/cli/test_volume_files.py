"""Hermetic CLI contracts for the isolated RP-04 command group."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pitwall.cli.volume_files import cmd_volume_files
from pitwall.runpod_client.pod_logs import BoundedPodLogPayload
from pitwall.runpod_files import VolumeFileService, VolumeObject, VolumeObjectPage
from tests.fakes.volume_files import FakeVolumeFileMutationJournal


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


def _factory() -> tuple[callable, FakeStore]:
    store = FakeStore()
    service = VolumeFileService(
        store,
        FakeLogs(),
        mutation_journal=FakeVolumeFileMutationJournal(),
    )
    return lambda: service, store


def test_cli_list_json_is_the_shared_machine_stable_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory, store = _factory()

    assert cmd_volume_files(["list", "vol-1", "US-KS-2", "--json"], service_factory=factory) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["operation"] == "list"
    assert result["objects"] == [{"key": "models/one.bin", "size": 3, "last_modified": None}]
    assert result["progress"][0]["sequence"] == 1
    assert store.calls[0][0:3] == ("list", "vol-1", "US-KS-2")


def test_cli_upload_and_delete_dry_run_have_zero_provider_writes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")
    factory, store = _factory()

    assert (
        cmd_volume_files(
            [
                "upload",
                "vol-1",
                "US-KS-2",
                "models/new.bin",
                "source.bin",
                "--root",
                str(tmp_path),
                "--dry-run",
                "--json",
            ],
            service_factory=factory,
        )
        == 0
    )
    upload = json.loads(capsys.readouterr().out)
    assert upload["status"] == "dry_run"
    assert upload["object_key"] == "models/new.bin"
    assert (
        cmd_volume_files(
            [
                "delete",
                "vol-1",
                "US-KS-2",
                "models/one.bin",
                "--confirm-delete",
                "--dry-run",
                "--json",
            ],
            service_factory=factory,
        )
        == 0
    )
    delete = json.loads(capsys.readouterr().out)
    assert delete["status"] == "dry_run"
    assert delete["object_key"] == "models/one.bin"
    assert not [call for call in store.calls if call[0] in {"put", "delete"}]


def test_cli_requires_explicit_overwrite_confirmation_and_redacts_provider_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")
    factory, store = _factory()

    assert (
        cmd_volume_files(
            [
                "upload",
                "vol-1",
                "US-KS-2",
                "models/one.bin",
                "source.bin",
                "--root",
                str(tmp_path),
                "--idempotency-key",
                "existing-upload",
                "--json",
            ],
            service_factory=factory,
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out) == {"error": "volume_file_confirmation_required"}
    assert not [call for call in store.calls if call[0] == "put"]

    def broken_factory() -> VolumeFileService:
        raise RuntimeError("Authorization: Bearer cli-secret-canary")

    assert cmd_volume_files(["logs", "pod-1", "--json"], service_factory=broken_factory) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"error": "volume_file_provider_error"}
    assert "cli-secret-canary" not in output


def test_cli_help_and_path_validation_are_local_and_safe(
    tmp_path: Path,
) -> None:
    factory, store = _factory()

    with pytest.raises(SystemExit) as help_exit:
        cmd_volume_files(["logs", "--help"], service_factory=factory)
    assert help_exit.value.code == 0

    assert (
        cmd_volume_files(
            [
                "download",
                "vol-1",
                "US-KS-2",
                "models/one.bin",
                "../escape.bin",
                "--root",
                str(tmp_path),
                "--json",
            ],
            service_factory=factory,
        )
        == 2
    )
    assert not [call for call in store.calls if call[0] == "get"]


def test_cli_upload_guardrail_rejects_before_provider_without_reflection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sk-1234567890abcdef1234567890abcdef"
    (tmp_path / "source.txt").write_text(secret)
    factory, store = _factory()

    result = cmd_volume_files(
        [
            "upload",
            "vol-1",
            "US-KS-2",
            "models/new.txt",
            "source.txt",
            "--root",
            str(tmp_path),
            "--json",
        ],
        service_factory=factory,
    )

    output = capsys.readouterr().out
    assert result == 2
    assert json.loads(output) == {"error": "pre_spend_payload_rejected"}
    assert secret not in output
    assert store.calls == []
