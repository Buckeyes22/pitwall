"""Cohesive CLI command tree for raw RunPod account resources."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from pydantic import BaseModel, ValidationError

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.cli.runpod_market import (
    MarketServiceFactory,
    add_runpod_catalogue_parser,
    cmd_runpod_catalogue,
)
from pitwall.db import get_pool
from pitwall.runpod_control_plane import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    IdentifiedMutationRequest,
    PodActionRequest,
    PodCreateRequest,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthReplaceRequest,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
)
from pitwall.runpod_market import build_configured_runpod_market_service

_MAX_REQUEST_JSON_BYTES = 65_536
_MUTATION_COMMANDS = frozenset(
    {
        ("pods", "create"),
        ("pods", "update"),
        ("pods", "action"),
        ("pods", "terminate"),
        ("endpoints", "create"),
        ("endpoints", "update"),
        ("endpoints", "delete"),
        ("templates", "create"),
        ("templates", "update"),
        ("templates", "delete"),
        ("volumes", "create"),
        ("volumes", "grow"),
        ("volumes", "delete"),
        ("registry-auths", "create"),
        ("registry-auths", "replace"),
        ("registry-auths", "delete"),
    }
)


def build_runpod_resources_parser() -> argparse.ArgumentParser:
    """Build the feature parser for serialized top-level CLI integration."""
    parser = argparse.ArgumentParser(
        prog="pitwall runpod",
        description=(
            "Inspect or administer raw RunPod resources. Broker leases and "
            "`pitwall serve` remain preferred for paid compute."
        ),
    )
    resources = parser.add_subparsers(dest="resource", required=True)
    add_runpod_catalogue_parser(resources)
    _add_pod_parser(resources)
    _add_endpoint_parser(resources)
    _add_template_parser(resources)
    _add_volume_parser(resources)
    _add_registry_parser(resources)
    _add_hub_parser(resources)
    return parser


def _add_pod_parser(resources: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    pods = resources.add_parser("pods", help="Raw pod list/get/create/update/actions/terminate.")
    commands = pods.add_subparsers(dest="command", required=True)
    _read_commands(commands)
    _payload_mutation(commands, "create", "Create one raw pod.")
    _identified_payload_mutation(commands, "update", "Update mutable pod fields.")
    action = commands.add_parser("action", help="Start, stop, restart, or reset a pod.")
    action.add_argument("resource_id")
    action.add_argument("action", choices=("start", "stop", "restart", "reset"))
    _mutation_flags(action)
    terminate = commands.add_parser("terminate", help="Permanently terminate a raw pod.")
    terminate.add_argument("resource_id")
    _mutation_flags(terminate)


def _add_endpoint_parser(
    resources: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    endpoints = resources.add_parser(
        "endpoints", help="Serverless endpoints with nested workers/scaling/GPU pools."
    )
    commands = endpoints.add_subparsers(dest="command", required=True)
    _read_commands(commands)
    _payload_mutation(commands, "create", "Create one endpoint.")
    _identified_payload_mutation(commands, "update", "Replace nested endpoint controls.")
    _identified_delete(commands)


def _add_template_parser(
    resources: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    templates = resources.add_parser(
        "templates", help="Mutable account templates (separate from read-only Hub)."
    )
    commands = templates.add_subparsers(dest="command", required=True)
    _read_commands(commands)
    _payload_mutation(commands, "create", "Create an account template.")
    _identified_payload_mutation(commands, "update", "Update an account template.")
    _identified_delete(commands)


def _add_volume_parser(
    resources: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    volumes = resources.add_parser("volumes", help="Network volumes; resize is grow-only.")
    commands = volumes.add_subparsers(dest="command", required=True)
    _read_commands(commands)
    _payload_mutation(commands, "create", "Create a network volume.")
    _identified_payload_mutation(commands, "grow", "Grow a network volume.")
    _identified_delete(commands)


def _add_registry_parser(
    resources: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    registry = resources.add_parser(
        "registry-auths", help="Registry auth; replacement is explicit delete/recreate."
    )
    commands = registry.add_subparsers(dest="command", required=True)
    _read_commands(commands)
    _payload_mutation(commands, "create", "Create registry auth from password_env.")
    _identified_payload_mutation(
        commands,
        "replace",
        "Delete and recreate registry auth; the identifier changes.",
    )
    _identified_delete(commands)


def _add_hub_parser(resources: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    hub = resources.add_parser("hub", help="Read-only RunPod Hub template catalogue.")
    commands = hub.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)
    add_json_argument(listing)
    get = commands.add_parser("get")
    get.add_argument("resource_id")
    add_json_argument(get)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=50)
    add_json_argument(search)


def _read_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    listing = commands.add_parser("list")
    add_json_argument(listing)
    get = commands.add_parser("get")
    get.add_argument("resource_id")
    add_json_argument(get)


def _payload_mutation(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> None:
    command = commands.add_parser(name, help=help_text)
    command.add_argument(
        "--request-json",
        required=True,
        help="Strict JSON object containing resource-specific request fields.",
    )
    _mutation_flags(command)


def _identified_payload_mutation(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> None:
    command = commands.add_parser(name, help=help_text)
    command.add_argument("resource_id")
    command.add_argument(
        "--request-json",
        required=True,
        help="Strict JSON object containing mutable resource fields.",
    )
    _mutation_flags(command)


def _identified_delete(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = commands.add_parser("delete", help="Permanently delete one resource.")
    command.add_argument("resource_id")
    _mutation_flags(command)


def _mutation_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview; perform no provider mutation or audit write.",
    )
    parser.add_argument(
        "--confirm",
        help="For a live mutation, type the resource id (or create name) exactly.",
    )
    add_json_argument(parser)


def cmd_runpod_resources(
    argv: list[str],
    *,
    service: RunPodControlPlaneService | None = None,
    market_service_factory: MarketServiceFactory = build_configured_runpod_market_service,
) -> int:
    """Run the feature-local RunPod resource command tree."""
    args = build_runpod_resources_parser().parse_args(argv)
    if args.resource == "catalogue":
        return cmd_runpod_catalogue(argv[1:], service_factory=market_service_factory)
    output = Output(json_mode(args))
    try:
        result = asyncio.run(_run(args, service=service))
    except RunPodControlPlaneError as exc:
        _error(output, exc.to_dict())
        return 1
    except ValidationError as exc:
        _error(
            output,
            {"error": "invalid_request", "detail": _safe_validation_detail(exc)},
        )
        return 2
    except (ValueError, json.JSONDecodeError) as exc:
        _error(output, {"error": "invalid_request", "detail": str(exc)[:512]})
        return 2

    rendered = _jsonable(result)
    if output.json_mode:
        output.set_json(rendered if isinstance(rendered, dict) else {"result": rendered})
    else:
        output.print_panel(
            json.dumps(rendered, indent=2, sort_keys=True),
            title="RunPod resources",
        )
    output.emit()
    return 0


async def _run(
    args: argparse.Namespace,
    *,
    service: RunPodControlPlaneService | None,
) -> object:
    resource = str(args.resource)
    command = str(args.command)
    control_plane = service

    async def get_control_plane() -> RunPodControlPlaneService:
        nonlocal control_plane
        if control_plane is None:
            needs_audit = (resource, command) in _MUTATION_COMMANDS and not args.dry_run
            control_plane = RunPodControlPlaneService(
                audit_pool=await get_pool() if needs_audit else None,
                actor="system",
            )
        return control_plane

    if resource == "pods":
        if command == "list":
            return {"pods": await (await get_control_plane()).list_pods()}
        if command == "get":
            return await (await get_control_plane()).get_pod(args.resource_id)
        if command == "create":
            pod_create = PodCreateRequest.model_validate(_request_data(args))
            _require_confirmation(args, pod_create.name)
            return await (await get_control_plane()).create_pod(pod_create)
        if command == "update":
            pod_update = PodUpdateRequest.model_validate(_request_data(args, identified=True))
            _require_confirmation(args, pod_update.resource_id)
            return await (await get_control_plane()).update_pod(pod_update)
        if command == "action":
            pod_action = PodActionRequest.model_validate(
                _simple_mutation_data(args, action=args.action)
            )
            _require_confirmation(args, pod_action.resource_id)
            return await (await get_control_plane()).action_pod(pod_action)
        pod_terminate = IdentifiedMutationRequest.model_validate(_simple_mutation_data(args))
        _require_confirmation(args, pod_terminate.resource_id)
        return await (await get_control_plane()).terminate_pod(pod_terminate)

    if resource == "endpoints":
        if command == "list":
            return {"endpoints": await (await get_control_plane()).list_endpoints()}
        if command == "get":
            return await (await get_control_plane()).get_endpoint(args.resource_id)
        if command == "create":
            endpoint_create = EndpointCreateRequest.model_validate(_request_data(args))
            _require_confirmation(args, endpoint_create.name)
            return await (await get_control_plane()).create_endpoint(endpoint_create)
        if command == "update":
            endpoint_update = EndpointUpdateRequest.model_validate(
                _request_data(args, identified=True)
            )
            _require_confirmation(args, endpoint_update.resource_id)
            return await (await get_control_plane()).update_endpoint(endpoint_update)
        endpoint_delete = IdentifiedMutationRequest.model_validate(_simple_mutation_data(args))
        _require_confirmation(args, endpoint_delete.resource_id)
        return await (await get_control_plane()).delete_endpoint(endpoint_delete)

    if resource == "templates":
        if command == "list":
            return {"templates": await (await get_control_plane()).list_templates()}
        if command == "get":
            return await (await get_control_plane()).get_template(args.resource_id)
        if command == "create":
            template_create = TemplateCreateRequest.model_validate(_request_data(args))
            _require_confirmation(args, template_create.name)
            return await (await get_control_plane()).create_template(template_create)
        if command == "update":
            template_update = TemplateUpdateRequest.model_validate(
                _request_data(args, identified=True)
            )
            _require_confirmation(args, template_update.resource_id)
            return await (await get_control_plane()).update_template(template_update)
        template_delete = IdentifiedMutationRequest.model_validate(_simple_mutation_data(args))
        _require_confirmation(args, template_delete.resource_id)
        return await (await get_control_plane()).delete_template(template_delete)

    if resource == "volumes":
        if command == "list":
            return {"volumes": await (await get_control_plane()).list_volumes()}
        if command == "get":
            return await (await get_control_plane()).get_volume(args.resource_id)
        if command == "create":
            volume_create = VolumeCreateRequest.model_validate(_request_data(args))
            _require_confirmation(args, volume_create.name)
            return await (await get_control_plane()).create_volume(volume_create)
        if command == "grow":
            volume_grow = VolumeGrowRequest.model_validate(_request_data(args, identified=True))
            _require_confirmation(args, volume_grow.resource_id)
            return await (await get_control_plane()).grow_volume(volume_grow)
        volume_delete = IdentifiedMutationRequest.model_validate(_simple_mutation_data(args))
        _require_confirmation(args, volume_delete.resource_id)
        return await (await get_control_plane()).delete_volume(volume_delete)

    if resource == "registry-auths":
        if command == "list":
            return {"registry_auths": await (await get_control_plane()).list_registry_auths()}
        if command == "get":
            return await (await get_control_plane()).get_registry_auth(args.resource_id)
        if command == "create":
            registry_create = RegistryAuthCreateRequest.model_validate(_request_data(args))
            _require_confirmation(args, registry_create.name)
            return await (await get_control_plane()).create_registry_auth(registry_create)
        if command == "replace":
            registry_replace = RegistryAuthReplaceRequest.model_validate(
                _request_data(args, identified=True)
            )
            _require_confirmation(args, registry_replace.resource_id)
            return await (await get_control_plane()).replace_registry_auth(registry_replace)
        registry_delete = IdentifiedMutationRequest.model_validate(_simple_mutation_data(args))
        _require_confirmation(args, registry_delete.resource_id)
        return await (await get_control_plane()).delete_registry_auth(registry_delete)

    if command == "list":
        return {
            "hub_templates": await (await get_control_plane()).list_hub_templates(
                limit=args.limit,
                offset=args.offset,
            )
        }
    if command == "get":
        return await (await get_control_plane()).get_hub_template(args.resource_id)
    return {
        "hub_templates": await (await get_control_plane()).search_hub_templates(
            args.query,
            limit=args.limit,
        )
    }


def _request_data(args: argparse.Namespace, *, identified: bool = False) -> dict[str, Any]:
    payload = _load_request_json(args.request_json)
    if "intent" in payload or "idempotency_key" in payload:
        raise ValueError("intent and idempotency_key must use their dedicated CLI flags")
    payload.update(_control_data(args))
    if identified:
        payload["resource_id"] = args.resource_id
    return payload


def _simple_mutation_data(args: argparse.Namespace, **extra: object) -> dict[str, object]:
    return {"resource_id": args.resource_id, **_control_data(args), **extra}


def _control_data(args: argparse.Namespace) -> dict[str, str]:
    return {
        "intent": "preview" if args.dry_run else "apply",
        "idempotency_key": args.idempotency_key,
    }


def _load_request_json(value: str) -> dict[str, Any]:
    if len(value.encode("utf-8")) > _MAX_REQUEST_JSON_BYTES:
        raise ValueError("request JSON exceeds 65536 bytes")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("request JSON must be an object")
    return parsed


def _require_confirmation(args: argparse.Namespace, expected: str) -> None:
    if args.dry_run:
        return
    if args.confirm != expected:
        raise ValueError("live mutation requires an exact --confirm value")


def _safe_validation_detail(exc: ValidationError) -> str:
    errors = []
    for error in exc.errors(include_input=False, include_url=False):
        path = ".".join(str(item) for item in error["loc"]) or "request"
        errors.append(f"{path}: {error['msg']}")
    return "; ".join(errors)[:1024]


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def _error(output: Output, body: dict[str, object]) -> None:
    if output.json_mode:
        output.set_json(body)
    else:
        output.print_error(str(body.get("detail") or body.get("error") or "request failed"))
    output.emit()


__all__ = ["build_runpod_resources_parser", "cmd_runpod_resources"]
