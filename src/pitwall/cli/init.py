"""``pitwall init``."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from typing import Any

from pitwall.cli.args import enum_values
from pitwall.cli.output import Output, add_json_argument, safe_json
from pitwall.cli.output import json_mode as _json_mode

_DEFAULT_INIT_SEED_PATH = "seed"
_DEFAULT_INIT_CAPABILITY_NAME = "embedding.demo"
_DEFAULT_INIT_PROVIDER_NAME = "demo-runpod-lb"
_DEFAULT_INIT_ENDPOINT_ID = "eptest00000000"
_DEFAULT_INIT_PROVIDER_TYPE = "serverless_lb"
_DEFAULT_INIT_REGION = "US-EXAMPLE-1"
_DEFAULT_INIT_GPU_CLASS = "NVIDIA L4"
_DEFAULT_INIT_PER_SECOND_ACTIVE = "0.001"


def _parse_init_args(argv: list[str]) -> argparse.Namespace:
    from pitwall.core.enums import CapabilityClass, CostMode, ProviderType

    parser = argparse.ArgumentParser(
        prog="pitwall init",
        description="Guided onboarding for a first capability and provider.",
    )
    parser.add_argument(
        "--from-seed",
        help=(
            "Seed file or directory to apply. Defaults to ./seed when it exists; "
            "use --manual to ignore it."
        ),
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Use manual/default values instead of the example seed directory.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt; use supplied flags and documented defaults.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Alias for --non-interactive for scripted setup.",
    )
    parser.add_argument("--capability-name")
    parser.add_argument("--capability-class", choices=enum_values(CapabilityClass))
    parser.add_argument("--cost-mode", choices=enum_values(CostMode))
    parser.add_argument("--provider-name")
    parser.add_argument("--endpoint-id")
    parser.add_argument("--provider-type", choices=enum_values(ProviderType))
    parser.add_argument("--region")
    parser.add_argument("--gpu-class")
    parser.add_argument("--per-second-active")
    parser.add_argument("--priority", type=int, default=1)
    parser.add_argument(
        "--smoke-base-url",
        default=None,
        help="Base URL used in the printed smoke command "
        "(default: PITWALL_API_URL, else http://127.0.0.1:$PITWALL_API_PORT, port 8080 if unset)",
    )
    parser.add_argument(
        "--smoke-text",
        default="hello",
        help="Text used in the printed dry-run inference smoke command.",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


async def _init_async(args: argparse.Namespace) -> int:
    from pitwall.core.enums import CapabilitySource
    from pitwall.db import get_pool
    from pitwall.registry_admin import apply_provider_health
    from pitwall.seed import SeedValidationError, apply_seed_data, apply_seed_files

    out = Output(_json_mode(args))

    try:
        pool = await get_pool()
        seed_path = _init_seed_path(args)
        if seed_path is not None:
            result = await apply_seed_files([seed_path], pool=pool, source=CapabilitySource.YAML)
        else:
            result = await apply_seed_data(
                _manual_init_seed_payload(args),
                pool=pool,
                source=CapabilitySource.API,
            )
    except SeedValidationError as exc:
        out.print_error(str(exc))
        out.emit()
        return 1

    if not result.capabilities:
        out.print_error("init did not create or update any capability")
        out.emit()
        return 1
    if not result.providers:
        out.print_error("init did not create or update any provider")
        out.emit()
        return 1

    # The seed directory also carries the free-tier gateway files, which sort before
    # providers.yaml, so pick the provider that serves the first capability rather than
    # whichever provider happened to be applied first.
    capability = result.capabilities[0]
    provider = next(
        (item for item in result.providers if item.capability_id == capability.id),
        result.providers[0],
    )
    healthy = await apply_provider_health(pool, provider.id, "healthy")
    if healthy is None:
        out.print_error(f"Provider not found: {provider.id}")
        out.emit()
        return 1

    out.print_success(
        f"Pitwall init complete\n"
        f"  capability: {capability.name} ({capability.id})\n"
        f"  provider: {provider.name} ({provider.id})\n"
        f"  health_status: healthy"
    )
    out.add_json("capability", safe_json(capability))
    out.add_json("provider", provider.model_dump(mode="json"))
    out.add_json("health_status", "healthy")
    if not out.json_mode:
        _print_smoke_inference_command(
            _resolve_smoke_base_url(args.smoke_base_url), capability.name, args.smoke_text
        )
    out.emit()
    return 0


def _init_seed_path(args: argparse.Namespace) -> str | None:
    if args.manual:
        return None
    from_seed: str | None = args.from_seed
    if from_seed:
        return from_seed
    if os.path.exists(_DEFAULT_INIT_SEED_PATH):
        return _DEFAULT_INIT_SEED_PATH
    return None


def _manual_init_seed_payload(args: argparse.Namespace) -> dict[str, Any]:
    interactive = not (args.non_interactive or args.yes) and sys.stdin.isatty()
    capability_name = _prompt_default(
        "Capability name",
        args.capability_name,
        _DEFAULT_INIT_CAPABILITY_NAME,
        interactive=interactive,
    )
    capability_class = _prompt_default(
        "Capability class",
        args.capability_class,
        "embedding",
        interactive=interactive,
    )
    cost_mode = _prompt_default(
        "Cost mode",
        args.cost_mode,
        "per_second",
        interactive=interactive,
    )
    provider_name = _prompt_default(
        "Provider name",
        args.provider_name,
        _DEFAULT_INIT_PROVIDER_NAME,
        interactive=interactive,
    )
    endpoint_id = _prompt_default(
        "RunPod endpoint id",
        args.endpoint_id,
        _DEFAULT_INIT_ENDPOINT_ID,
        interactive=interactive,
    )
    provider_type = _prompt_default(
        "Provider type",
        args.provider_type,
        _DEFAULT_INIT_PROVIDER_TYPE,
        interactive=interactive,
    )
    region = _prompt_default(
        "Region",
        args.region,
        _DEFAULT_INIT_REGION,
        interactive=interactive,
    )
    gpu_class = _prompt_default(
        "GPU class",
        args.gpu_class,
        _DEFAULT_INIT_GPU_CLASS,
        interactive=interactive,
    )
    per_second_active = _prompt_default(
        "Cost per active second",
        args.per_second_active,
        _DEFAULT_INIT_PER_SECOND_ACTIVE,
        interactive=interactive,
    )
    return {
        "capabilities": [
            {
                "name": capability_name,
                "version": "1.0.0",
                "class": capability_class,
                "description": "Local onboarding capability",
                "cost_mode": cost_mode,
            }
        ],
        "providers": [
            {
                "name": provider_name,
                "capability": capability_name,
                "endpoint_id": endpoint_id,
                "provider_type": provider_type,
                "region": region,
                "gpu_class": gpu_class,
                "priority": args.priority,
                "cost": {
                    "mode": cost_mode,
                    "per_second_active": per_second_active,
                },
            }
        ],
    }


def _prompt_default(
    label: str,
    supplied: str | None,
    default: str,
    *,
    interactive: bool,
) -> str:
    if supplied is not None:
        return supplied
    if not interactive:
        return default
    raw = input(f"{label} [{default}]: ").strip()
    return raw or default


def _print_smoke_inference_command(base_url: str, capability_name: str, text: str) -> None:
    payload = {"capability": capability_name, "texts": [text], "dry_run": True}
    endpoint = f"{base_url.rstrip('/')}/v1/inference"
    print("Next smoke command:")
    print(f"curl -s -X POST {shlex.quote(endpoint)} \\")
    # Reference the token by name so the secret itself is never printed.
    if os.environ.get("PITWALL_API_TOKEN"):
        print('  -H "Authorization: Bearer $PITWALL_API_TOKEN" \\')
    elif os.environ.get("PITWALL_API_SCOPED_TOKENS"):
        print("  -H 'Authorization: Bearer <api-token>' \\")
    print("  -H 'Content-Type: application/json' \\")
    print(f"  -d {shlex.quote(_json_dumps(payload))}")


def _resolve_smoke_base_url(value: str | None) -> str:
    from pitwall.cli.base_url import configured_base_url

    return configured_base_url(value or "")


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def cmd_init(argv: list[str]) -> int:
    args = _parse_init_args(argv)

    import asyncio

    out = Output(_json_mode(args))
    try:
        return asyncio.run(_init_async(args))
    except (
        Exception
    ) as exc:  # reason: CLI boundary: report a fixed code and the class, never the text
        from pitwall.cli.runtime_errors import report_failure

        name = type(exc).__name__
        report_failure(
            out,
            "init_failed",
            exc,
            extra={"exception": name},
            fallback=f"Error: init_failed ({name})",
        )
        out.emit()
        return 1
