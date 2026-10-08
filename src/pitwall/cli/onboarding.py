"""Feature-local CLI adapter for the shared RunPod onboarding service."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import ValidationError

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.db import get_pool
from pitwall.onboarding import (
    OnboardingAction,
    OnboardingCommand,
    OnboardingError,
    OnboardingResult,
    RunPodOnboardingRequest,
    RunPodOnboardingService,
    create_runpod_onboarding_service,
)

_MAX_REQUEST_BYTES = 262_144
ServiceFactory = Callable[[], Awaitable[RunPodOnboardingService]]


def _parse_onboarding_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall runpod-onboard",
        description=(
            "Plan by default, then explicitly apply/resume a confirmed RunPod onboarding file."
        ),
    )
    parser.add_argument("request", type=Path, help="Secret-free JSON topology request file.")
    parser.add_argument(
        "--action",
        choices=[action.value for action in OnboardingAction],
        default=OnboardingAction.PLAN.value,
    )
    parser.add_argument(
        "--confirmed-plan-id",
        help="Exact plan_id required only by apply and resume.",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


async def get_runpod_onboarding_service() -> RunPodOnboardingService:
    return create_runpod_onboarding_service(await get_pool(), actor="system")


def cmd_runpod_onboard(
    argv: list[str],
    *,
    service_factory: ServiceFactory = get_runpod_onboarding_service,
) -> int:
    args = _parse_onboarding_args(argv)
    output = Output(json_mode(args))
    try:
        request = _load_request(args.request)
        command = OnboardingCommand(
            action=args.action,
            request=request,
            confirmed_plan_id=args.confirmed_plan_id,
        )
        result = asyncio.run(_execute(command, service_factory))
    except OSError, json.JSONDecodeError, UnicodeError, ValidationError, ValueError:
        output.set_json({"error": "runpod_onboarding_invalid_request"})
        if not output.json_mode:
            output.print_error("RunPod onboarding request is invalid.")
        output.emit()
        return 2
    except OnboardingError as exc:
        output.set_json(exc.to_dict())
        if not output.json_mode:
            output.print_error(f"RunPod onboarding failed safely: {exc.code}")
            if exc.result is not None:
                _render_human(exc.result, output)
        output.emit()
        return 2 if exc.code == "plan_confirmation_mismatch" else 1
    except Exception:  # reason: provider/persistence detail must not cross the CLI
        output.set_json({"error": "runpod_onboarding_unavailable"})
        if not output.json_mode:
            output.print_error("RunPod onboarding is unavailable.")
        output.emit()
        return 1

    if output.json_mode:
        output.set_json(result.model_dump(mode="json"))
    else:
        _render_human(result, output)
    output.emit()
    return 0


async def _execute(
    command: OnboardingCommand,
    service_factory: ServiceFactory,
) -> OnboardingResult:
    service = await service_factory()
    try:
        return await service.execute(command)
    finally:
        await service.aclose()


def _load_request(path: Path) -> RunPodOnboardingRequest:
    stat = path.stat()
    if not path.is_file() or stat.st_size > _MAX_REQUEST_BYTES:
        raise ValueError("onboarding request must be a bounded regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("onboarding request root must be an object")
    return RunPodOnboardingRequest.model_validate(payload)


def _render_human(result: OnboardingResult, output: Output) -> None:
    output.print_panel(
        "\n".join(
            (
                f"Action: {result.action.value}",
                f"Plan: {result.plan_id}",
                f"Status: {result.status.value}",
                f"Topology: {result.topology.value}",
                f"Zero write: {'yes' if result.zero_write else 'no'}",
                f"Estimated ceiling: {result.estimated_ceiling_usd} USD",
                "Endpoint hourly range: "
                f"{result.cost_impact.endpoint_minimum_hourly_usd}-"
                f"{result.cost_impact.endpoint_maximum_hourly_usd} USD",
                f"Network-volume pricing: {result.cost_impact.network_volume_pricing}",
                "Provider resources: " + _inventory(result.provider_resources),
                "Database mutations: " + _inventory(result.database_mutations),
                "Existing resource reuse: " + _inventory(result.existing_resource_reuse),
                f"Next step: {result.next_step or 'none'}",
            )
        ),
        title="RunPod onboarding",
    )
    output.print_table(
        "Steps",
        ["Order", "Step", "State", "Resource", "Effect", "Writes", "Reused", "Rollback"],
        [
            [
                step.order,
                step.key,
                step.state.value,
                (
                    f"{step.resource_type}:{step.resource_id}"
                    if step.resource_type and step.resource_id
                    else step.resource_type or "—"
                ),
                step.effect,
                _inventory(step.writes),
                "yes" if step.reused else "no",
                step.rollback,
            ]
            for step in result.steps
        ],
    )
    if result.rollback_guidance:
        output.print_panel("\n".join(result.rollback_guidance), title="Rollback guidance")


def _inventory(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


__all__ = ["cmd_runpod_onboard", "get_runpod_onboarding_service"]
