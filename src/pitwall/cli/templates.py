"""``pitwall register-template``."""

from __future__ import annotations

import argparse
import hashlib
import os

from pitwall.cli.args import guard_cli_pre_spend
from pitwall.cli.output import Output, add_json_argument
from pitwall.cli.output import json_mode as _json_mode
from pitwall.runpod_client import templates
from pitwall.runpod_client.templates import (
    TEMPLATE_ENV_KEYS,
    ensure_template,
    get_registry_auth_id_from_env,
)
from pitwall.runpod_credentials import MISSING_CREDENTIAL_MESSAGE, resolve_runpod_api_key


def _parse_register_template_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall register-template",
        description="Register a RunPod template and cache its ID.",
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Full image reference (e.g., ghcr.io/org/worker:v1 or ghcr.io/org/worker@sha256:...)",
    )
    parser.add_argument(
        "--template-name",
        default="pitwall-cloud-worker",
        help="Base template name (default: pitwall-cloud-worker)",
    )
    parser.add_argument(
        "--container-disk-gb",
        type=int,
        default=50,
        help="Container disk size in GB (default: 50)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate image parsing, registry auth selection, template defaults, and env schema without network calls.",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


async def _register_template_async(args: argparse.Namespace) -> int:
    from pitwall.db import get_pool

    out = Output(_json_mode(args))
    image_ref = args.image
    template_name = args.template_name
    container_disk_gb = args.container_disk_gb

    guard_cli_pre_spend(
        {
            "image": image_ref,
            "template_name": template_name,
            "container_disk_gb": container_disk_gb,
        },
        preview=False,
    )

    api_key, _ = resolve_runpod_api_key(os.environ)
    if not api_key:
        out.print_error(MISSING_CREDENTIAL_MESSAGE)
        out.emit()
        return 1

    registry_auth_id = get_registry_auth_id_from_env(image_ref)
    template_id = await ensure_template(
        await get_pool(),
        image_ref,
        template_name=template_name,
        registry_auth_id=registry_auth_id,
        container_disk_gb=container_disk_gb,
    )
    out.print_success(f"Template registered: {template_id}")
    out.add_json("template_id", template_id)
    out.emit()
    return 0


def _dry_run_validate(args: argparse.Namespace) -> int:
    image_ref = args.image
    template_name = args.template_name
    container_disk_gb = args.container_disk_gb

    guard_cli_pre_spend(
        {
            "image": image_ref,
            "template_name": template_name,
            "container_disk_gb": container_disk_gb,
        },
        preview=True,
    )

    out = Output(_json_mode(args))
    image_parsed = templates.image_sha(image_ref)
    normalized_name = templates.normalize_template_name(template_name)
    registry_auth_id = get_registry_auth_id_from_env(image_ref)
    # The same inputs ensure_template names the template from, so the preview is the real name.
    display_name = templates.template_display_name(
        normalized_name,
        image_ref,
        env_keys=templates.non_secret_env_keys(TEMPLATE_ENV_KEYS),
        container_disk_gb=container_disk_gb,
        registry_auth_id=registry_auth_id,
    )

    lines = [
        f"[dry-run] image: {image_ref}",
        f"[dry-run] image_sha: {image_parsed}",
        f"[dry-run] template_name: {template_name} -> {normalized_name}",
        f"[dry-run] template_display_name: {display_name}",
        f"[dry-run] container_disk_gb: {container_disk_gb}",
        f"[dry-run] registry_auth_id: {registry_auth_id}",
        f"[dry-run] env_keys ({len(TEMPLATE_ENV_KEYS)}): {TEMPLATE_ENV_KEYS}",
    ]
    out.print_panel("\n".join(lines), title="Dry Run", border_style="blue")
    out.print_success(f"Template previewed: {display_name}")
    out.set_json(
        {
            "provider": "runpod",
            "operation": "template.create",
            "resource_type": "template",
            "resource_id": None,
            "dry_run": True,
            "changed": False,
            "already_absent": False,
            "effect": f"create account template {display_name!r}; no Hub publication",
            "estimated_ceiling": None,
            "irreversible": False,
            "idempotency_key": (
                "legacy-template:"
                + hashlib.sha256(
                    f"{display_name}\0{image_ref}\0{container_disk_gb}".encode()
                ).hexdigest()[:32]
            ),
            "resource": None,
            "image": image_ref,
            "image_sha": image_parsed,
            "template_name": template_name,
            "normalized_name": normalized_name,
            "template_display_name": display_name,
            "container_disk_gb": container_disk_gb,
            "registry_auth_id": registry_auth_id,
            "env_keys": list(TEMPLATE_ENV_KEYS),
        }
    )
    out.emit()
    return 0


def cmd_register_template(argv: list[str]) -> int:
    args = _parse_register_template_args(argv)

    import asyncio

    out = Output(_json_mode(args))
    try:
        if args.dry_run:
            return _dry_run_validate(args)
        return asyncio.run(_register_template_async(args))
    except Exception:  # reason: CLI boundary emits one stable non-reflecting failure
        out.set_json({"error": "template_operation_failed"})
        if not out.json_mode:
            out.print_error("Error: template operation failed")
        out.emit()
        return 1
