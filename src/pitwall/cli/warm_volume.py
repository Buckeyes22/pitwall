"""``pitwall warm-volume``."""

from __future__ import annotations

import argparse
import asyncio
import os
from decimal import Decimal
from typing import Literal

from pitwall.cli.args import guard_cli_pre_spend, normalize_model_id
from pitwall.cli.base_url import configured_base_url
from pitwall.cli.output import Output, add_json_argument
from pitwall.cli.output import json_mode as _json_mode
from pitwall.models.catalogue import load_catalogue
from pitwall.models.errors import CatalogueError
from pitwall.models.fit import fit_options
from pitwall.models.prices import load_gpu_price_snapshot


def _parse_warm_volume_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall warm-volume",
        description=(
            "Warm a network volume by launching the catalogue serve command, verifying "
            "its model inventory, then immediately tearing the lease down."
        ),
    )
    parser.add_argument("--model", required=True, help="Catalogue model ID.")
    parser.add_argument("--variant", help="Catalogue variant ID.")
    parser.add_argument("--volume-id", required=True, help="RunPod network volume ID.")
    parser.add_argument("--datacenter", help="Optional RunPod datacenter constraint.")
    parser.add_argument("--gpu-class", help="Canonical GPU class; defaults to the cheapest fit.")
    parser.add_argument("--gated", action="store_true", help="Inject configured HF credentials.")
    parser.add_argument("--dry-run", action="store_true", help="Show the serve launch plan only.")
    add_json_argument(parser)
    return parser.parse_args(argv)


_WARM_TTL_MARGIN_MIN = 5


async def _warm_volume_async(args: argparse.Namespace, out: Output) -> int:
    from pitwall.config import get_settings
    from pitwall.db import get_pool
    from pitwall.serve import ServeRequest, serve_model

    guard_cli_pre_spend(
        {
            "model": args.model,
            "variant": args.variant,
            "volume_id": args.volume_id,
            "datacenter": args.datacenter,
            "gpu_class": args.gpu_class,
            "gated": args.gated,
        },
        preview=args.dry_run,
    )
    settings = get_settings()
    catalogue = load_catalogue()
    model = normalize_model_id(args.model)
    dossier = catalogue.get(model)
    if dossier is None:
        raise CatalogueError(f"unknown model: {model}")
    variant = dossier.resolve_variant(args.variant)
    if variant is None:
        raise CatalogueError(f"unknown variant {args.variant!r} for model {model}")

    startup_minutes = variant.startup_min if isinstance(variant.startup_min, int) else 30
    # The lease must outlast startup, or serve refuses it (ttl_below_startup); the warm
    # tears the pod down as soon as the model answers.
    warm_ttl_minutes = max(15, startup_minutes + _WARM_TTL_MARGIN_MIN)
    gpu_class = args.gpu_class
    rate_per_second = Decimal("0")
    price_source: Literal["live", "fallback"] | None = None
    if gpu_class is None:
        snapshot = await load_gpu_price_snapshot(cloud="secure", settings=settings)
        price_source = snapshot.source
        options = fit_options(
            variant,
            gpu_types=snapshot.gpu_types,
            ttl_minutes=warm_ttl_minutes,
            cloud="secure",
        )
        fitted = [option for option in options if option.fit == "fits"]
        if not fitted:
            from pitwall.api.exceptions import ServeInvalidGpuClass

            detail = f"no single-GPU fit for {variant.id}; pass --gpu-class"
            error = ServeInvalidGpuClass(variant.id, ())
            error.detail = detail
            error.args = (detail,)
            raise error
        if snapshot.source == "fallback":
            selected = fitted[0]
        else:
            selected = min(
                fitted,
                key=lambda option: (
                    option.price_per_hour is None,
                    option.price_per_hour or Decimal("0"),
                    -(option.headroom_gb or 0),
                    option.gpu_class,
                ),
            )
        gpu_class = selected.gpu_class
        if selected.price_per_hour is not None:
            rate_per_second = selected.price_per_hour / Decimal(3600)

    result = await serve_model(
        await get_pool(),
        ServeRequest(
            capability_name=dossier.pitwall.capability_name,
            model=model,
            gpu_class=gpu_class,
            variant=variant.id,
            ttl_minutes=warm_ttl_minutes,
            datacenter=args.datacenter,
            network_volume_id=args.volume_id,
            rate_per_second=rate_per_second,
            price_source=price_source,
            gated=args.gated,
            dry_run=args.dry_run,
        ),
        base_url=configured_base_url(settings.pitwall_base_url),
        settings=settings,
        catalogue=catalogue,
        warm_only=True,
    )
    data = result.to_dict()
    if out.json_mode:
        out.set_json(data)
    else:
        out.print_panel(
            "\n".join(f"{key}: {value}" for key, value in data.items()),
            title="Warm Volume Dry Run" if args.dry_run else "Warm Volume",
        )
    out.emit()
    return 0


def cmd_warm_volume(argv: list[str]) -> int:
    from pitwall.api.exceptions import PitwallApiError
    from pitwall.cost.budget_gate import BudgetRejected

    args = _parse_warm_volume_args(argv)
    out = Output(_json_mode(args))
    if not os.environ.get("DATABASE_URL", "").strip():
        message = "warm-volume needs DATABASE_URL (registry-backed launch planning)"
        out.set_json({"error": "missing_database_url", "detail": message})
        if not out.json_mode:
            out.print_error(message)
        out.emit()
        return 2
    try:
        return asyncio.run(_warm_volume_async(args, out))
    except BudgetRejected as exc:
        out.set_json(exc.to_response_body())
        if not out.json_mode:
            out.print_error(f"budget rejected: {exc.reason}")
        out.emit()
        return 2
    except PitwallApiError as exc:
        body = exc.to_response_body()
        out.set_json(body)
        if not out.json_mode:
            out.print_error(str(body.get("error", "invalid_request")))
        out.emit()
        return 2
    except CatalogueError as exc:
        detail = str(exc)
        out.set_json({"error": "invalid_request", "detail": detail})
        if not out.json_mode:
            out.print_error(detail)
        out.emit()
        return 2
    except Exception:  # reason: CLI boundary converts launch failures to a stable exit
        out.set_json({"error": "warm_failed"})
        if not out.json_mode:
            out.print_error("warm-volume failed")
        out.emit()
        return 1
