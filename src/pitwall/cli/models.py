"""``pitwall models``."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pitwall.cli.args import normalize_model_id, positive_context_length, positive_minutes
from pitwall.cli.output import Output, add_json_argument
from pitwall.cli.output import json_mode as _json_mode
from pitwall.models.catalogue import load_catalogue, reload, write_evidence
from pitwall.models.errors import CatalogueError
from pitwall.models.fit import CacheState, FitOption, fit_options, fit_options_local
from pitwall.models.inventory import load_inventory
from pitwall.models.prices import load_gpu_price_snapshot

_MODELS_CACHE_LOOKUP_TIMEOUT_S = 5.0
_MODELS_CACHE_CLOSE_TIMEOUT_S = 2.0


def _parse_models_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall models",
        description="Inspect and price the packaged model catalogue.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    list_parser = subcommands.add_parser("list", help="List catalogue models.")
    add_json_argument(list_parser)

    show_parser = subcommands.add_parser("show", help="Show a catalogue dossier.")
    show_parser.add_argument("model", help="Model ID as org/model or dossier-style org--model.")
    add_json_argument(show_parser)

    fit_parser = subcommands.add_parser("fit", help="Show hardware fit and cost.")
    fit_parser.add_argument("model", help="Model ID as org/model or dossier-style org--model.")
    fit_parser.add_argument(
        "--variant", help="Dossier variant ID; defaults to the published default."
    )
    fit_parser.add_argument(
        "--ttl-minutes",
        type=positive_minutes,
        default=120,
        help="Lease duration for cost estimates (default: 120).",
    )
    fit_parser.add_argument(
        "--cloud",
        choices=("secure", "community"),
        default="secure",
        help="RunPod cloud price class (default: secure).",
    )
    fit_parser.add_argument("--inventory", type=Path)
    fit_parser.add_argument("--context", type=positive_context_length)
    add_json_argument(fit_parser)

    evidence_parser = subcommands.add_parser(
        "evidence", help="Record a measured model-variant observation."
    )
    evidence_parser.add_argument("model", help="Model ID as org/model or dossier-style org--model.")
    evidence_parser.add_argument("--variant", required=True, help="Dossier variant ID.")
    evidence_parser.add_argument("--gpu-class", required=True, help="Canonical RunPod GPU class.")
    evidence_parser.add_argument("--observed-vram-gb", required=True, type=float)
    evidence_parser.add_argument("--observed-startup-s", required=True, type=float)
    return parser.parse_args(argv)


def cmd_models(argv: list[str]) -> int:
    args = _parse_models_args(argv)
    if hasattr(args, "model"):
        args.model = normalize_model_id(args.model)
    out = Output(_json_mode(args))
    try:
        if args.command == "show":
            return _models_show(args, out)
        if args.command == "list":
            return asyncio.run(_models_list(args, out))
        if args.command == "evidence":
            return _models_evidence(args, out)
        return asyncio.run(_models_fit(args, out))
    except (CatalogueError, ValueError) as exc:
        out.print_error(f"Error: {exc}")
        out.emit()
        return 1


def _models_show(args: argparse.Namespace, out: Output) -> int:
    dossier = load_catalogue().get(args.model)
    if dossier is None:
        raise CatalogueError(f"unknown model: {args.model}")
    if out.json_mode:
        data = dossier.model_dump(mode="json")
        data["body"] = dossier.body
        out.set_json(data)
        out.emit()
        return 0
    out.print_panel(
        f"Model: {dossier.model_id}\nVendor: {dossier.vendor}\nFamily: {dossier.family}\n"
        f"Release date: {dossier.release_date}\nLicense: {dossier.license.name}",
        title="Model",
    )
    out.print_table(
        "Variants",
        ["ID", "Engine", "Format", "Min VRAM", "Confidence"],
        [[v.id, v.engine, v.format, v.min_vram_gb, v.confidence] for v in dossier.variants],
        keep_whole=("ID",),
    )
    out.print(dossier.body)
    return 0


def _models_evidence(args: argparse.Namespace, out: Output) -> int:
    variant = write_evidence(
        model_id=args.model,
        variant_id=args.variant,
        gpu_class=args.gpu_class,
        observed_vram_gb=args.observed_vram_gb,
        observed_startup_s=args.observed_startup_s,
        date=datetime.now(UTC).date().isoformat(),
    )
    assert variant.evidence is not None
    out.print(
        f"Recorded measured evidence for {args.model} variant {variant.id} "
        f"on {variant.evidence.gpu_class}."
    )
    reload()
    return 0


async def _models_list(args: argparse.Namespace, out: Output) -> int:
    snapshot = await load_gpu_price_snapshot(cloud="secure")
    rows: list[dict[str, object]] = []
    for dossier in load_catalogue().models():
        candidates = [
            option
            for variant in dossier.variants
            if variant.default
            for option in fit_options(
                variant, gpu_types=snapshot.gpu_types, ttl_minutes=60, cloud="secure"
            )
            if option.fit == "fits" and option.gpu_count == 1
        ]
        best = candidates[0] if candidates else None
        rows.append(
            {
                "model": dossier.model_id,
                "vendor": dossier.vendor,
                "variants": len(dossier.variants),
                "best_single_gpu": best.gpu_class if best else None,
                "price_per_hour": str(best.price_per_hour)
                if best and best.price_per_hour is not None
                else None,
            }
        )
    if out.json_mode:
        out.set_json({"models": rows})
        out.emit()
    else:
        out.print_table(
            "Models",
            ["Model", "Vendor", "Variants", "Best single-GPU", "$/hr"],
            [
                [
                    row["model"],
                    row["vendor"],
                    row["variants"],
                    row["best_single_gpu"],
                    row["price_per_hour"] if row["price_per_hour"] is not None else "unpriced",
                ]
                for row in rows
            ],
            keep_whole=("Model",),
        )
    return 0


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _print_fit_table(out: Output, options: list[FitOption], confidence: str) -> None:
    out.print_table(
        "Hardware fit",
        ["GPU", "Count", "VRAM", "Headroom", "Fit", "$/hr", "TTL cost", "Confidence"],
        [
            [
                option.gpu_class,
                option.gpu_count,
                option.vram_gb,
                option.headroom_gb,
                option.fit,
                option.price_per_hour if option.price_per_hour is not None else "unpriced",
                option.cost_for_ttl if option.cost_for_ttl is not None else "unpriced",
                confidence,
            ]
            for option in options
        ],
    )


async def _models_fit(args: argparse.Namespace, out: Output) -> int:
    catalogue = load_catalogue()
    dossier = catalogue.get(args.model)
    if dossier is None:
        raise CatalogueError(f"unknown model: {args.model}")
    variant = catalogue.dossier_variant(args.model, args.variant)
    if args.inventory is not None:
        inventory = load_inventory(args.inventory)
        context_length = args.context
        if context_length is None:
            context_length = variant.context
        if not isinstance(context_length, int):
            raise ValueError("--context is required when variant context is unverified")
        options = fit_options_local(
            variant,
            inventory=inventory,
            context_length=context_length,
        )
        if out.json_mode:
            out.set_json(
                {
                    "model": args.model,
                    "variant": variant.id,
                    "inventory": str(args.inventory),
                    "context_length": context_length,
                    "options": [_json_safe(item.model_dump(mode="json")) for item in options],
                    "source": "local",
                }
            )
            out.emit()
        else:
            out.print(
                f"Local inventory: {args.inventory}, context {context_length}", soft_wrap=True
            )
            _print_fit_table(out, options, variant.confidence)
        return 0
    snapshot = await load_gpu_price_snapshot(cloud=args.cloud)
    from pitwall.config import load_settings_from_env
    from pitwall.models import gpu_price_freshness

    settings = load_settings_from_env()
    freshness = gpu_price_freshness(snapshot, max_age_s=settings.pitwall_price_max_age_s)
    cache_state = await _models_cache_state(
        database_url=settings.database_url,
        capability_name=dossier.pitwall.capability_name,
        variant_id=variant.id,
    )
    options = fit_options(
        variant,
        gpu_types=snapshot.gpu_types,
        ttl_minutes=args.ttl_minutes,
        cloud=args.cloud,
        warm_cache=cache_state == "warm",
        cache_state=cache_state,
    )
    if out.json_mode:
        serialized = [_json_safe(option.model_dump(mode="json")) for option in options]
        for item in serialized:
            assert isinstance(item, dict)
            item["confidence"] = variant.confidence
        out.set_json(
            {
                "model": args.model,
                "variant": variant.id,
                "options": serialized,
                "source": snapshot.source,
                "age_seconds": freshness.age_seconds,
                "stale": freshness.stale,
            }
        )
        out.emit()
    else:
        out.print(
            f"Pricing: source={freshness.source}, age={freshness.age_seconds}s, "
            f"stale={'yes' if freshness.stale else 'no'}"
        )
        if snapshot.source == "fallback":
            out.print("RunPod pricing unavailable; showing fallback GPU data.")
        out.print(f"Cache: {cache_state.replace('_', ' ')}")
        _print_fit_table(out, options, variant.confidence)
    return 0


async def _models_cache_state(
    *, database_url: str, capability_name: str, variant_id: str
) -> CacheState:
    """Check the registry cache record when a database is configured."""
    if not database_url.strip():
        return "not_checked"
    pool: Any = None

    async def lookup() -> CacheState:
        nonlocal pool
        from pitwall.db import get_pool
        from pitwall.registry_admin import serve_cache_state

        pool = await get_pool(database_url, min_size=1, max_size=1)
        state: CacheState = (
            "warm"
            if await serve_cache_state(pool, capability_name, variant_id) == "warm"
            else "cold"
        )
        return state

    try:
        return await asyncio.wait_for(lookup(), timeout=_MODELS_CACHE_LOOKUP_TIMEOUT_S)
    except Exception:  # reason: an unreachable optional registry is an explicit unknown state
        return "not_checked"
    finally:
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), timeout=_MODELS_CACHE_CLOSE_TIMEOUT_S)
            except Exception:  # reason: pool cleanup must not leave optional CLI resources open
                pool.terminate()
