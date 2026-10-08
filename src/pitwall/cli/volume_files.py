"""Feature-local CLI adapter for bounded RunPod volume files and pod logs."""

from __future__ import annotations

import argparse
import asyncio
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.db import get_pool
from pitwall.runpod_files import (
    VolumeFileError,
    VolumeFileResult,
    VolumeFileService,
    build_configured_volume_file_service,
)

ServiceFactory = Callable[[], VolumeFileService | Awaitable[VolumeFileService]]


async def _configured_service() -> VolumeFileService:
    return build_configured_volume_file_service(
        audit_pool=await get_pool(),
        audit_actor="system",
    )


def _parse_volume_files_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall volume-files",
        description="Bounded RunPod network-volume S3 transfer and pod-log operations.",
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    listing = commands.add_parser("list", help="List one bounded page of volume objects.")
    _add_volume_context(listing)
    listing.add_argument("--prefix", default="", help="Relative object-key prefix.")
    listing.add_argument("--max-items", type=int, default=200, help="Bounded page size.")
    add_json_argument(listing)

    upload = commands.add_parser("upload", help="Upload one bounded local regular file.")
    _add_volume_context(upload, object_key=True)
    _add_local_path(upload)
    upload.add_argument(
        "--overwrite", action="store_true", help="Permit an existing object target."
    )
    upload.add_argument(
        "--confirm-overwrite",
        action="store_true",
        help="Explicitly confirm overwrite when the object already exists.",
    )
    upload.add_argument("--expected-sha256", help="Optional expected SHA-256 of the local file.")
    upload.add_argument(
        "--idempotency-key",
        help="Required for live upload; reuse it only for the exact same request.",
    )
    upload.add_argument("--dry-run", action="store_true", help="Validate with zero S3 writes.")
    add_json_argument(upload)

    download = commands.add_parser(
        "download", help="Download one bounded object to a safe local path."
    )
    _add_volume_context(download, object_key=True)
    _add_local_path(download)
    download.add_argument(
        "--overwrite", action="store_true", help="Permit an existing local target."
    )
    download.add_argument(
        "--confirm-overwrite",
        action="store_true",
        help="Explicitly confirm replacing an existing local target.",
    )
    download.add_argument("--expected-sha256", help="Optional expected SHA-256 of the object.")
    download.add_argument("--dry-run", action="store_true", help="Validate with zero local writes.")
    add_json_argument(download)

    delete = commands.add_parser("delete", help="Delete one object after explicit confirmation.")
    _add_volume_context(delete, object_key=True)
    delete.add_argument(
        "--confirm-delete", action="store_true", help="Explicitly confirm object deletion."
    )
    delete.add_argument(
        "--idempotency-key",
        help="Required for live delete; reuse it only for the exact same request.",
    )
    delete.add_argument("--dry-run", action="store_true", help="Validate with zero S3 writes.")
    add_json_argument(delete)

    logs = commands.add_parser("logs", help="Read bounded, redacted pod diagnostics.")
    logs.add_argument("pod_id", help="RunPod pod ID.")
    logs.add_argument("--max-lines", type=int, default=100, help="Maximum ordered log lines.")
    logs.add_argument("--max-bytes", type=int, default=64 * 1024, help="Maximum log bytes.")
    add_json_argument(logs)
    return parser.parse_args(argv)


def _add_volume_context(parser: argparse.ArgumentParser, *, object_key: bool = False) -> None:
    parser.add_argument("volume_id", help="RunPod network volume ID.")
    parser.add_argument("data_center_id", help="RunPod data-center ID for its S3 endpoint.")
    if object_key:
        parser.add_argument("object_key", help="Relative object key; traversal is rejected.")


def _add_local_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("local_path", help="Relative local file path below --root.")
    parser.add_argument(
        "--root",
        default=".",
        help="Existing non-symlink local root directory (default: current directory).",
    )


def cmd_volume_files(
    argv: list[str],
    *,
    service_factory: ServiceFactory = _configured_service,
) -> int:
    """Run a volume-files command group with stable JSON and bounded failure output.

    The top-level dispatcher owns command registration; it passes the group
    arguments to this isolated handler during serialized integration.
    """
    args = _parse_volume_files_args(argv)
    output = Output(json_mode(args))
    try:
        result = asyncio.run(_run(args, service_factory))
    except KeyboardInterrupt:
        output.set_json({"error": "volume_file_cancelled"})
        if not output.json_mode:
            output.print_warning("volume-file operation cancelled")
        output.emit()
        return 130
    except VolumeFileError as exc:
        output.set_json(exc.to_response_body())
        if not output.json_mode:
            output.print_error(exc.error_code)
        output.emit()
        return 2 if exc.status_code in {409, 413, 422} else 1
    except Exception as exc:  # reason: CLI boundary must never reflect provider detail or secrets
        del exc
        output.set_json({"error": "volume_file_provider_error"})
        if not output.json_mode:
            output.print_error("volume_file_provider_error")
        output.emit()
        return 1

    if output.json_mode:
        output.set_json(result.to_dict())
    else:
        _render_human(result, output)
    output.emit()
    return 0 if result.status != "cancelled" else 130


async def _run(args: argparse.Namespace, service_factory: ServiceFactory) -> VolumeFileResult:
    pending_service = service_factory()
    service = await pending_service if inspect.isawaitable(pending_service) else pending_service
    try:
        if args.operation == "list":
            return await service.list_objects(
                volume_id=args.volume_id,
                data_center_id=args.data_center_id,
                prefix=args.prefix,
                max_items=args.max_items,
            )
        if args.operation == "upload":
            return await service.upload_file(
                volume_id=args.volume_id,
                data_center_id=args.data_center_id,
                object_key=args.object_key,
                local_root=Path(args.root),
                local_path=args.local_path,
                overwrite=args.overwrite and args.confirm_overwrite,
                expected_sha256=args.expected_sha256,
                dry_run=args.dry_run,
                idempotency_key=args.idempotency_key,
            )
        if args.operation == "download":
            return await service.download_to_path(
                volume_id=args.volume_id,
                data_center_id=args.data_center_id,
                object_key=args.object_key,
                local_root=Path(args.root),
                local_path=args.local_path,
                overwrite=args.overwrite and args.confirm_overwrite,
                expected_sha256=args.expected_sha256,
                dry_run=args.dry_run,
            )
        if args.operation == "delete":
            return await service.delete_object(
                volume_id=args.volume_id,
                data_center_id=args.data_center_id,
                object_key=args.object_key,
                confirm_delete=args.confirm_delete,
                dry_run=args.dry_run,
                idempotency_key=args.idempotency_key,
            )
        if args.operation == "logs":
            return await service.read_pod_logs(
                pod_id=args.pod_id,
                max_lines=args.max_lines,
                max_bytes=args.max_bytes,
            )
    finally:
        await service.aclose()
    raise AssertionError(f"unexpected volume-files operation: {args.operation!r}")


def _render_human(result: VolumeFileResult, output: Output) -> None:
    output.print_panel(
        "\n".join(
            (
                f"Operation: {result.operation}",
                f"Status: {result.status}",
                f"Bytes: {result.bytes_transferred}",
                f"Checksum: {result.checksum_sha256 or 'not available'}",
                f"Truncated: {'yes' if result.truncated else 'no'}",
            )
        ),
        title="RunPod volume files",
        border_style="yellow" if result.status == "dry_run" else "green",
    )
    if result.objects:
        output.print_table(
            "Volume objects",
            ["Key", "Bytes", "Last modified"],
            [[item.key, item.size, item.last_modified or ""] for item in result.objects],
        )
    if result.logs:
        output.print_table(
            "Pod logs",
            ["#", "Timestamp", "Message"],
            [[line.sequence, line.timestamp or "", line.text] for line in result.logs],
        )


__all__ = ["cmd_volume_files"]
