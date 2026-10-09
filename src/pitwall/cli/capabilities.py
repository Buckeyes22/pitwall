"""``pitwall create-capability`` and ``pitwall seed``."""

from __future__ import annotations

import argparse
from typing import Any

from pitwall.cli.args import enum_values
from pitwall.cli.output import Output, add_json_argument, safe_json
from pitwall.cli.output import json_mode as _json_mode


def _parse_create_capability_args(argv: list[str]) -> argparse.Namespace:
    from pitwall.core.enums import CapabilityClass, CapabilityHint, CostMode

    parser = argparse.ArgumentParser(
        prog="pitwall create-capability",
        description="Create or update a Pitwall capability by flags or spec file.",
    )
    parser.add_argument(
        "--spec",
        help="YAML/JSON capability spec file. May contain one capability or a capabilities list.",
    )
    parser.add_argument("--name", help="Capability name, e.g. embedding.demo")
    parser.add_argument("--version", default="1.0.0", help="Capability version (default: 1.0.0)")
    parser.add_argument(
        "--class",
        dest="capability_class",
        choices=enum_values(CapabilityClass),
        help="Capability class",
    )
    parser.add_argument(
        "--cost-mode",
        choices=enum_values(CostMode),
        help="Cost estimator mode",
    )
    parser.add_argument("--description", help="Human-readable capability description")
    parser.add_argument(
        "--hint",
        dest="hints",
        action="append",
        choices=enum_values(CapabilityHint),
        default=[],
        help="Capability hint. May be repeated.",
    )
    parser.add_argument(
        "--openai-compatible",
        action="store_true",
        help="Mark the capability as OpenAI-compatible in registry config.",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


async def _create_capability_async(args: argparse.Namespace) -> int:
    from pitwall.core.enums import CapabilitySource
    from pitwall.db import get_pool
    from pitwall.registry_admin import upsert_capability
    from pitwall.seed import SeedValidationError, apply_capability_seed_files

    out = Output(_json_mode(args))

    if args.spec:
        if args.name or args.capability_class or args.cost_mode:
            out.print_error("use either --spec or --name/--class/--cost-mode flags")
            out.emit()
            return 1
        try:
            pool = await get_pool()
            capabilities = await apply_capability_seed_files(
                [args.spec],
                pool=pool,
                source=CapabilitySource.API,
            )
        except SeedValidationError as exc:
            out.print_error(str(exc))
            out.emit()
            return 1
        if not capabilities:
            out.print_error(f"no capabilities found in spec: {args.spec}")
            out.emit()
            return 1
        out.add_json("capabilities", [safe_json(c) for c in capabilities])
        for capability in capabilities:
            _print_capability_created(capability, out)
        out.emit()
        return 0

    missing = []
    if args.name is None:
        missing.append("--name")
    if args.capability_class is None:
        missing.append("--class")
    if args.cost_mode is None:
        missing.append("--cost-mode")
    if missing:
        out.print_error(f"missing required arguments: {', '.join(missing)}")
        out.emit()
        return 1

    name = args.name.strip()
    if not name:
        out.print_error("capability name cannot be empty")
        out.emit()
        return 1

    pool = await get_pool()
    capability = await upsert_capability(
        pool,
        name=name,
        class_=args.capability_class,
        cost_mode=args.cost_mode,
        version=args.version,
        description=args.description,
        hints_supported=args.hints,
        openai_compatible=args.openai_compatible,
    )
    out.add_json("capability", safe_json(capability))
    _print_capability_created(capability, out)
    out.emit()
    return 0


def _print_capability_created(capability: Any, out: Output) -> None:
    out.print_success(
        f"Capability created: {capability.id}\n"
        f"  name: {capability.name}\n"
        f"  class: {capability.class_.value}\n"
        f"  cost_mode: {capability.cost_mode.value}"
    )


def cmd_create_capability(argv: list[str]) -> int:
    args = _parse_create_capability_args(argv)

    import asyncio

    out = Output(_json_mode(args))
    try:
        return asyncio.run(_create_capability_async(args))
    except (
        Exception
    ) as exc:  # reason: CLI boundary: report a fixed code and the class, never the text
        from pitwall.cli.runtime_errors import report_failure

        name = type(exc).__name__
        report_failure(
            out,
            "create_capability_failed",
            exc,
            extra={"exception": name},
            fallback=f"Error: create_capability_failed ({name})",
        )
        out.emit()
        return 1


def _parse_seed_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall seed",
        description="Apply Pitwall capability/provider seed files.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="YAML/JSON seed file or directory containing seed files.",
    )
    parser.add_argument(
        "--mark-healthy",
        action="store_true",
        help="After applying providers, mark them healthy using the provider health path.",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


async def _seed_async(args: argparse.Namespace) -> int:
    from pitwall.core.enums import CapabilitySource
    from pitwall.db import get_pool
    from pitwall.registry_admin import apply_provider_health
    from pitwall.seed import SeedValidationError, apply_seed_files

    out = Output(_json_mode(args))

    try:
        pool = await get_pool()
        result = await apply_seed_files(args.paths, pool=pool, source=CapabilitySource.YAML)
        if args.mark_healthy:
            for provider in result.providers:
                healthy = await apply_provider_health(pool, provider.id, "healthy")
                if healthy is None:
                    out.print_error(f"Provider not found: {provider.id}")
                    out.emit()
                    return 1
    except SeedValidationError as exc:
        out.print_error(str(exc))
        out.emit()
        return 1

    cap_rows: list[list[Any]] = []
    for capability in result.capabilities:
        cap_rows.append([capability.id, capability.name])
    prov_rows: list[list[Any]] = []
    for provider in result.providers:
        health = "healthy" if args.mark_healthy else provider.health_status
        prov_rows.append([provider.id, provider.name, health])

    if cap_rows:
        out.print_table("Capabilities", ["id", "name"], cap_rows)
    if prov_rows:
        out.print_table("Providers", ["id", "name", "health"], prov_rows)

    out.add_json("capabilities", [safe_json(c) for c in result.capabilities])
    out.add_json("providers", [safe_json(p) for p in result.providers])
    out.emit()
    return 0


def cmd_seed(argv: list[str]) -> int:
    args = _parse_seed_args(argv)

    import asyncio

    out = Output(_json_mode(args))
    try:
        return asyncio.run(_seed_async(args))
    except (
        Exception
    ) as exc:  # reason: CLI boundary: report a fixed code and the class, never the text
        from pitwall.cli.runtime_errors import report_failure

        name = type(exc).__name__
        report_failure(
            out,
            "seed_failed",
            exc,
            extra={"exception": name},
            fallback=f"Error: seed_failed ({name})",
        )
        out.emit()
        return 1
