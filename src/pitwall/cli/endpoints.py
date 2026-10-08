"""``pitwall register-endpoint`` and ``pitwall set-provider-health``."""

from __future__ import annotations

import argparse
from typing import Any

from pitwall.cli.output import Output, add_json_argument, safe_json
from pitwall.cli.output import json_mode as _json_mode

_PROVIDER_HEALTH_STATUSES = ("unknown", "healthy", "unhealthy", "hibernated")


def _parse_register_endpoint_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall register-endpoint",
        description="Register a RunPod Serverless endpoint as a Pitwall provider.",
    )
    parser.add_argument(
        "--endpoint-id",
        required=True,
        help="RunPod endpoint ID (e.g., eptest00000000)",
    )
    parser.add_argument(
        "--provider-type",
        required=True,
        choices=["serverless_queue", "serverless_lb", "public_endpoint", "pod_lease"],
        help="RunPod provider surface type",
    )
    parser.add_argument(
        "--capability-id",
        required=True,
        help="Capability ID this endpoint fulfills (e.g., cap_llm_qwen3_32b)",
    )
    parser.add_argument(
        "--capability-name",
        help="Capability human-readable name (e.g., llm.qwen3-32b). If the capability does not exist, it will be upserted with this name.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Human-readable provider name",
    )
    parser.add_argument(
        "--region",
        help="RunPod region ID (e.g., US-KS-2)",
    )
    parser.add_argument(
        "--gpu-class",
        required=True,
        help="Canonical RunPod GPU type (e.g., NVIDIA H100 80GB HBM3)",
    )
    parser.add_argument(
        "--cost-mode",
        choices=["per_second", "per_request", "per_token"],
        help="Cost estimation mode",
    )
    parser.add_argument(
        "--per-second-active",
        type=float,
        help="Cost per active container-second (USD)",
    )
    parser.add_argument(
        "--per-request",
        type=float,
        help="Flat cost per request (USD)",
    )
    parser.add_argument(
        "--per-million-input-tokens",
        type=float,
        help="Cost per million input tokens (USD)",
    )
    parser.add_argument(
        "--per-million-output-tokens",
        type=float,
        help="Cost per million output tokens (USD)",
    )
    parser.add_argument(
        "--workers-min",
        type=int,
        default=0,
        help="Minimum always-on worker count (default: 0)",
    )
    parser.add_argument(
        "--workers-max",
        type=int,
        help="Maximum worker count for auto-scaling",
    )
    parser.add_argument(
        "--idle-timeout-minutes",
        type=int,
        default=0,
        help="Idle timeout before scale-to-zero in minutes (default: 0)",
    )
    parser.add_argument(
        "--flash-boot-verified",
        action="store_true",
        help="FlashBoot has been verified in RunPod console",
    )
    parser.add_argument(
        "--max-payload-mb",
        type=int,
        default=30,
        help="Maximum request payload size in MB (default: 30)",
    )
    parser.add_argument(
        "--request-timeout-s",
        type=int,
        default=330,
        help="Request timeout in seconds (default: 330)",
    )
    parser.add_argument(
        "--priority",
        type=int,
        default=0,
        help="Routing priority (lower = preferred, default: 0)",
    )
    parser.add_argument(
        "--health",
        choices=_PROVIDER_HEALTH_STATUSES,
        default="unknown",
        help="Initial provider health status (default: unknown). Use healthy to make the provider immediately routable.",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


def _missing_capability_message(capability_id: str) -> str:
    return (
        f"ERROR: capability '{capability_id}' does not exist; create it first "
        "with POST /v1/admin/capabilities or pass --capability-name to "
        "register-endpoint to create it by name."
    )


async def _register_endpoint_async(args: argparse.Namespace) -> int:

    from pitwall.core.enums import ProviderType
    from pitwall.core.models import validate_provider_storage_payload
    from pitwall.db import get_pool
    from pitwall.registry_admin import (
        CapabilityMissing,
        ProviderNameTaken,
        register_endpoint_provider,
    )
    from pitwall.runpod_client.gpu import validate_canonical_gpu_name

    out = Output(_json_mode(args))

    endpoint_id = args.endpoint_id.strip()
    provider_type = ProviderType(args.provider_type)
    capability_id = args.capability_id.strip()
    capability_name = args.capability_name.strip() if args.capability_name else None
    name = args.name.strip()
    region = args.region.strip() if args.region else None
    gpu_class = validate_canonical_gpu_name(args.gpu_class)

    cost_config: dict[str, Any] = {}
    if args.cost_mode:
        cost_config["mode"] = args.cost_mode
    if args.per_second_active is not None:
        cost_config["per_second_active"] = args.per_second_active
    if args.per_request is not None:
        cost_config["per_request"] = args.per_request
    if args.per_million_input_tokens is not None:
        cost_config["per_million_input_tokens"] = args.per_million_input_tokens
    if args.per_million_output_tokens is not None:
        cost_config["per_million_output_tokens"] = args.per_million_output_tokens

    workers_config: dict[str, Any] = {"workers_min": args.workers_min}
    if args.workers_max is not None:
        workers_config["workers_max"] = args.workers_max

    config: dict[str, Any] = {
        "gpu_class": gpu_class,
        "cost": cost_config,
        "workers": workers_config,
        "idle_timeout_minutes": args.idle_timeout_minutes,
        "flash_boot_verified": args.flash_boot_verified,
        "max_payload_mb": args.max_payload_mb,
        "request_timeout_s": args.request_timeout_s,
    }

    if provider_type == ProviderType.SERVERLESS_LB:
        config["lb_base_url"] = f"https://{endpoint_id}.api.runpod.ai"
    elif provider_type in (ProviderType.SERVERLESS_QUEUE, ProviderType.PUBLIC_ENDPOINT):
        config["openai_base_url"] = f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1"

    validate_provider_storage_payload(
        {
            "capability_id": capability_id,
            "capability_name": capability_name,
            "name": name,
            "provider_type": provider_type.value,
            "runpod_endpoint_id": endpoint_id,
            "region": region,
            "config": config,
        }
    )

    pool = await get_pool()
    try:
        result = await register_endpoint_provider(
            pool,
            name=name,
            capability_id=capability_id,
            capability_name=capability_name,
            provider_type=provider_type,
            endpoint_id=endpoint_id,
            region=region,
            config=config,
            priority=args.priority,
            health=args.health,
            cost_mode=args.cost_mode,
        )
    except ProviderNameTaken as taken:
        out.print_error(f"Provider with name {name!r} already exists (id={taken.existing.id})")
        out.emit()
        return 1
    except CapabilityMissing:
        out.print_error(_missing_capability_message(capability_id))
        out.emit()
        return 1
    out.print_success(
        f"Provider registered: {result.id}\n"
        f"  name: {result.name}\n"
        f"  capability_id: {result.capability_id}\n"
        f"  provider_type: {result.provider_type.value}\n"
        f"  runpod_endpoint_id: {result.runpod_endpoint_id}\n"
        f"  region: {result.region}\n"
        f"  gpu_class: {config.get('gpu_class')}\n"
        f"  priority: {result.priority}\n"
        f"  health_status: {result.health_status}"
    )
    out.add_json("provider", safe_json(result))
    out.emit()
    return 0


def cmd_register_endpoint(argv: list[str]) -> int:
    args = _parse_register_endpoint_args(argv)

    import asyncio

    out = Output(_json_mode(args))
    try:
        return asyncio.run(_register_endpoint_async(args))
    except (
        Exception
    ) as exc:  # reason: CLI boundary: report a fixed code and the class, never the text
        from pitwall.cli.runtime_errors import report_failure

        name = type(exc).__name__
        report_failure(
            out,
            "register_endpoint_failed",
            exc,
            extra={"exception": name},
            fallback=f"Error: register_endpoint_failed ({name})",
        )
        out.emit()
        return 1


def _parse_set_provider_health_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall set-provider-health",
        description="Set a provider health status used by routing.",
    )
    parser.add_argument("provider_id", help="Pitwall provider id")
    parser.add_argument("health", choices=_PROVIDER_HEALTH_STATUSES)
    add_json_argument(parser)
    return parser.parse_args(argv)


async def _set_provider_health_async(args: argparse.Namespace) -> int:
    from pitwall.db import get_pool
    from pitwall.registry_admin import apply_provider_health

    out = Output(_json_mode(args))
    provider_id = args.provider_id.strip()
    health_status = args.health.strip()

    pool = await get_pool()
    result = await apply_provider_health(pool, provider_id, health_status)
    if result is None:
        out.print_error(f"Provider not found: {provider_id}")
        out.emit()
        return 1

    out.print_success(
        f"Provider health updated: {result.id}\n"
        f"  name: {result.name}\n"
        f"  health_status: {result.health_status}"
    )
    out.add_json("provider", safe_json(result))
    out.emit()
    return 0


def cmd_set_provider_health(argv: list[str]) -> int:
    args = _parse_set_provider_health_args(argv)

    import asyncio

    out = Output(_json_mode(args))
    try:
        return asyncio.run(_set_provider_health_async(args))
    except (
        Exception
    ) as exc:  # reason: CLI boundary: report a fixed code and the class, never the text
        from pitwall.cli.runtime_errors import report_failure

        name = type(exc).__name__
        report_failure(
            out,
            "set_provider_health_failed",
            exc,
            extra={"exception": name},
            fallback=f"Error: set_provider_health_failed ({name})",
        )
        out.emit()
        return 1
