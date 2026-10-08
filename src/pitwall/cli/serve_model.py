"""``pitwall serve --plan-only`` (registry serve) and route registration."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal

from pitwall.cli.args import normalize_model_id
from pitwall.cli.base_url import configured_base_url
from pitwall.cli.output import Output, add_json_argument
from pitwall.cli.output import json_mode as _json_mode
from pitwall.personal.routes import routing_command


@dataclass(frozen=True)
class RouteRegistration:
    ok: bool
    action: Literal["added", "refreshed", "failed"]
    stderr: str


def _run_routing_command(
    command: list[str],
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def register_route(
    *,
    routing_cli: str,
    route: str,
    capability: str,
    base_url: str,
    env: Mapping[str, str],
) -> RouteRegistration:
    try:
        add = _run_routing_command(
            [
                *routing_command(routing_cli, env),
                "profiles",
                "add",
                route,
                "--from-pitwall",
                capability,
                "--pitwall-url",
                base_url,
            ],
            env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RouteRegistration(ok=False, action="failed", stderr=str(exc))
    if add.returncode == 0:
        return RouteRegistration(ok=True, action="added", stderr="")
    stderr = add.stderr.strip()
    already_registered = add.returncode == 4 and "already registered from Pitwall" in stderr
    if not already_registered:
        return RouteRegistration(ok=False, action="failed", stderr=stderr)
    try:
        refresh = _run_routing_command(
            [
                *routing_command(routing_cli, env),
                "profiles",
                "refresh",
                route,
                "--pitwall-url",
                base_url,
            ],
            env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RouteRegistration(ok=False, action="failed", stderr=str(exc))
    if refresh.returncode == 0:
        return RouteRegistration(ok=True, action="refreshed", stderr="")
    return RouteRegistration(
        ok=False,
        action="failed",
        stderr=refresh.stderr.strip(),
    )


def _env_assignment(value: str) -> tuple[str, str]:
    key, separator, env_value = value.partition("=")
    if not separator or not key:
        raise argparse.ArgumentTypeError("--env must be KEY=VAL with a non-empty key")
    return key, env_value


def _parse_serve_model_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall serve",
        description=(
            "Launch or replay an OpenAI-compatible model-serving pod lease. Use canonical "
            "RunPod GPU names (for example, NVIDIA GeForce RTX 4090); legacy aliases "
            "normalize automatically. Catalogue model plus variant selects its image and engine; "
            "an explicit --image overrides the catalogue image, and a model without a dossier "
            "requires --image and cannot use --variant."
        ),
        epilog=(
            "Examples:\n"
            "  pitwall serve --plan-only --model Qwen/Qwen3.8-27B "
            "--gpu-class RTX_3090\n"
            "  pitwall serve --capability llm.qwen --model "
            "Qwen/Qwen3.8-27B --gpu-class 'NVIDIA GeForce RTX 4090' --variant "
            "gguf:UD-Q4_K_XL --dry-run\n"
            "  pitwall serve --capability llm.custom --model org/model "
            "--gpu-class 'NVIDIA GeForce RTX 4090' --image example/vllm-openai:latest "
            "--rate-per-second 0.004 --dry-run\n\n"
            "DATABASE_URL is required for live launches and --dry-run because they are "
            "registry-backed. --plan-only is catalogue-only and does not open a database pool."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--capability", help="Capability name to create or replay; omitted with --plan-only."
    )
    parser.add_argument("--model", help="Model ID; omitted restores capability serve history.")
    parser.add_argument(
        "--gpu-class",
        help="Canonical RunPod GPU name; omitted restores capability serve history.",
    )
    parser.add_argument("--gpu-count", type=int, default=None, metavar="N")
    parser.add_argument(
        "--engine",
        choices=("vllm", "llama.cpp", "sglang"),
        default=None,
        help="Override the catalogue engine; models without a dossier default to vllm.",
    )
    parser.add_argument(
        "--variant", help="Catalogue variant ID; not valid with a model lacking a dossier."
    )
    parser.add_argument("--template-id", help="Reuse an existing RunPod template ID.")
    parser.add_argument(
        "--ttl-minutes",
        "--ttl",
        dest="ttl_minutes",
        type=int,
        default=None,
        metavar="N",
        help="Lease TTL in minutes (default: 120; --ttl is an alias of --ttl-minutes).",
    )
    parser.add_argument(
        "--idle-timeout-min",
        type=int,
        help="Stop after N idle minutes; minimum 5 and default renewal becomes activity.",
    )
    parser.add_argument(
        "--max-usd-per-hour",
        type=Decimal,
        help="Refuse when the selected GPU's live hourly price exceeds this cap.",
    )
    parser.add_argument(
        "--renewal",
        choices=("manual", "activity"),
        help="Lease renewal policy; omitted defaults from --idle-timeout-min.",
    )
    parser.add_argument(
        "--route",
        help="Register or refresh this model-routing route after a live serve succeeds.",
    )
    parser.add_argument(
        "--image",
        help="Container image; overrides the catalogue image and is required without a dossier.",
    )
    parser.add_argument(
        "--served-model-name", help="OpenAI model name exposed by the launched server."
    )
    parser.add_argument("--datacenter", help="Optional RunPod datacenter constraint.")
    parser.add_argument("--container-disk-gb", type=int, help="Override container disk size in GB.")
    parser.add_argument(
        "--rate-per-second",
        type=Decimal,
        help="Required when GPU prices are unavailable; USD per second.",
    )
    parser.add_argument(
        "--gated",
        action="store_true",
        help="Use gated-model launch credentials from the configured environment.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        type=_env_assignment,
        metavar="KEY=VAL",
        help="Additional container environment variable; repeatable.",
    )
    parser.add_argument(
        "--start-arg",
        dest="start_args",
        action="append",
        default=[],
        metavar="ARG",
        help="Additional model-server argument; repeatable.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show the resolved launch plan without launching a pod.",
    )
    mode.add_argument(
        "--plan-only",
        action="store_true",
        help="Show a catalogue-only plan without DATABASE_URL or registry access.",
    )
    parser.add_argument("--idempotency-key", help="Stable key for safe launch retries.")
    add_json_argument(parser)
    args = parser.parse_args(argv)
    if args.plan_only and (args.model is None or args.gpu_class is None):
        parser.error("--plan-only requires --model and --gpu-class")
    if not args.plan_only and args.capability is None:
        parser.error("--capability is required unless --plan-only is used")
    return args


async def _serve_model_async(args: argparse.Namespace, out: Output) -> int:
    from pitwall.config import get_settings
    from pitwall.models import load_catalogue
    from pitwall.serve import ServePlanRequest, ServeRequest, plan_catalogue_model, serve_model

    settings = get_settings()
    catalogue = load_catalogue()
    if args.plan_only:
        plan = await plan_catalogue_model(
            ServePlanRequest(
                model=normalize_model_id(args.model),
                gpu_class=args.gpu_class,
                gpu_count=args.gpu_count or 1,
                engine=args.engine,
                variant=args.variant,
                ttl_minutes=args.ttl_minutes or 120,
                image=args.image,
                served_model_name=args.served_model_name,
                env=dict(args.env),
                start_args=args.start_args,
            ),
            settings=settings,
            catalogue=catalogue,
        )
        if out.json_mode:
            out.set_json(plan.to_dict())
        else:
            out.print_panel(
                "\n".join(
                    [
                        f"model_id: {plan.model_id}",
                        f"engine: {plan.engine}",
                        f"variant: {plan.variant or '(default)'}",
                        f"gpu: {plan.gpu_class} x {plan.gpu_count}",
                        f"image: {plan.image}",
                        f"argv: {json.dumps(plan.argv)}",
                        f"volume_cache_env: {json.dumps(plan.volume_cache_env, sort_keys=True)}",
                        f"fit: {plan.fit}",
                        f"startup_timeout_s: {plan.startup_timeout_s}",
                        f"cost_estimate_usd: {plan.cost_estimate_usd or '(unpriced)'}",
                        f"price_source: {plan.price_source}",
                    ]
                ),
                title="Serve Model Plan",
            )
        out.emit()
        return 0

    from pitwall.db import get_pool

    values: dict[str, Any] = {"capability_name": args.capability}
    if args.gated:
        values["gated"] = True
    if args.dry_run:
        values["dry_run"] = True
    optional_names = (
        ("model", "model"),
        ("gpu_class", "gpu_class"),
        ("gpu_count", "gpu_count"),
        ("engine", "engine"),
        ("variant", "variant"),
        ("template_id", "template_id"),
        ("ttl_minutes", "ttl_minutes"),
        ("idle_timeout_min", "idle_timeout_min"),
        ("max_usd_per_hour", "max_usd_per_hour"),
        ("renewal_policy", "renewal"),
        ("image", "image"),
        ("served_model_name", "served_model_name"),
        ("datacenter", "datacenter"),
        ("container_disk_gb", "container_disk_gb"),
        ("rate_per_second", "rate_per_second"),
        ("idempotency_key", "idempotency_key"),
    )
    for field_name, argument_name in optional_names:
        value = getattr(args, argument_name)
        if value is not None:
            values[field_name] = normalize_model_id(value) if field_name == "model" else value
    if args.env:
        values["env"] = dict(args.env)
    if args.start_args:
        values["start_args"] = args.start_args
    result = await serve_model(
        await get_pool(),
        ServeRequest.model_validate(values),
        base_url=configured_base_url(settings.pitwall_base_url),
        settings=settings,
        catalogue=catalogue,
    )
    registration: RouteRegistration | None = None
    if args.route is not None and not result.dry_run:
        registration = await asyncio.to_thread(
            register_route,
            routing_cli=settings.pitwall_routing_cli,
            route=args.route,
            capability=result.capability,
            base_url=configured_base_url(settings.pitwall_base_url),
            env=os.environ,
        )
    data = result.to_dict()
    if out.json_mode:
        out.set_json(data)
    else:
        out.print_panel(
            "\n".join(
                [
                    f"capability: {result.capability}",
                    f"model_id: {result.model_id}",
                    f"engine: {result.engine}",
                    f"variant: {result.variant or '(default)'}",
                    f"gpu_count: {result.gpu_count}",
                    f"lease_id: {result.lease_id or '(dry-run)'}",
                    f"workload_id: {result.workload_id or '(none)'}",
                    f"template_id: {result.template_id or '(none)'}",
                    f"proxy_base_url: {result.proxy_base_url}",
                    f"expires_at: {result.expires_at or '(dry-run)'}",
                    f"dry_run: {result.dry_run}",
                    f"cost_estimate_usd: {result.cost_estimate_usd or '(none)'}",
                ]
            ),
            title="Serve Model Dry Run" if result.dry_run else "Serve Model",
        )
    if registration is not None and not registration.ok:
        detail = f"serve ok; route registration failed: {registration.stderr}"
        remedy = (
            f"{settings.pitwall_routing_cli} profiles add {args.route} "
            f"--from-pitwall {result.capability} --pitwall-url "
            f"{configured_base_url(settings.pitwall_base_url)}"
        )
        if out.json_mode:
            data["route_registration"] = asdict(registration)
            data["route_registration_remedy"] = remedy
            out.set_json(data)
        else:
            out.print_error(detail)
            print(f"remedy: {remedy}", file=sys.stderr)
        out.emit()
        return 3
    out.emit()
    return 0


def _report_failure(out: Output, code: str, exc: BaseException) -> None:
    """Print a fixed error code and the exception class, never the exception text.

    Provider, database, and validation errors can echo credentials or input values.
    """
    out.set_json({"error": code, "exception": type(exc).__name__})
    if not out.json_mode:
        out.print_error(f"{code} ({type(exc).__name__})")
    out.emit()


def _report_invalid_request(out: Output, exc: Any) -> None:
    """Print the field locations and error types of a ``ValidationError``, never its input."""
    errors = [
        {"loc": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors(include_input=False, include_url=False)
    ]
    out.set_json({"error": "invalid_request", "errors": errors})
    if not out.json_mode:
        out.print_error(
            "invalid_request: " + ", ".join(f"{one['loc']} ({one['type']})" for one in errors)
        )
    out.emit()


def cmd_serve_model(argv: list[str]) -> int:
    import asyncio

    from pydantic import ValidationError

    from pitwall.api.admin.kill_switch import KillSwitchEngaged
    from pitwall.api.exceptions import (
        PitwallApiError,
        ServeBudgetExhausted,
        ServeKillSwitchEngaged,
        ServeNoServeHistory,
    )
    from pitwall.cost.budget_gate import BudgetRejected
    from pitwall.runpod_client.pods import RunPodError

    args = _parse_serve_model_args(argv)
    out = Output(_json_mode(args))
    if not args.plan_only and args.max_usd_per_hour is not None:
        from pitwall.serve import ServeRequest

        try:
            ServeRequest.model_validate(
                {"capability_name": args.capability, "max_usd_per_hour": args.max_usd_per_hour}
            )
        except ValidationError as exc:
            _report_invalid_request(out, exc)
            return 1
    message = (
        "serve needs DATABASE_URL (registry-backed dry run); run "
        "`pitwall init` or export DATABASE_URL"
    )
    if not args.plan_only and not os.environ.get("DATABASE_URL", "").strip():
        out.set_json({"error": "missing_database_url", "detail": message})
        if not out.json_mode:
            print(message, file=sys.stderr)
        out.emit()
        return 2
    try:
        return asyncio.run(_serve_model_async(args, out))
    except BudgetRejected as exc:
        mapped = ServeBudgetExhausted(
            reason=exc.reason,
            snapshot=exc.snapshot.to_serializable_dict(),
        )
        out.set_json(mapped.to_response_body())
        if not out.json_mode:
            out.print_error(f"budget exhausted: {exc.reason}")
        out.emit()
        return 2
    except KillSwitchEngaged:
        kill_mapped = ServeKillSwitchEngaged()
        out.set_json(kill_mapped.to_response_body())
        if not out.json_mode:
            out.print_error("kill switch engaged")
        out.emit()
        return 2
    except ServeNoServeHistory as exc:
        _report_failure(out, exc.error_code, exc)
        return 1
    except PitwallApiError as exc:
        _report_failure(out, exc.error_code, exc)
        return 2 if exc.status_code in {409, 422} else 1
    except ValidationError as exc:
        _report_invalid_request(out, exc)
        return 1
    except RunPodError as exc:
        _report_failure(out, "launch_failed", exc)
        return 1
    except Exception as exc:  # reason: CLI boundary converts any service failure to a stable exit
        if type(exc).__module__ == "runpod.error" and type(exc).__name__ == "QueryError":
            _report_failure(out, "launch_failed", exc)
            return 1
        _report_failure(out, "serve_failed", exc)
        return 1


def cmd_serve(argv: list[str]) -> int:
    """``pitwall serve``: the registry plan-only path, else the personal single-host path."""
    if "--plan-only" in argv:
        return cmd_serve_model(argv)

    from pitwall.cli.personal import cmd_serve as personal_serve

    return personal_serve(argv)
