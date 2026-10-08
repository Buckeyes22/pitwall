"""Feature-local CLI contracts for RunPod account resources."""

from __future__ import annotations

import argparse
import json
from typing import Any

from pitwall.cli import runpod_resources as cli_runpod_resources
from pitwall.cli.runpod_resources import (
    build_runpod_resources_parser,
    cmd_runpod_resources,
)
from pitwall.runpod_control_plane import (
    EndpointUpdateRequest,
    MutationResult,
    PodCreateRequest,
)


class StubService:
    def __init__(self) -> None:
        self.pod_requests: list[PodCreateRequest] = []
        self.endpoint_requests: list[EndpointUpdateRequest] = []

    async def create_pod(self, request: PodCreateRequest) -> MutationResult:
        self.pod_requests.append(request)
        return MutationResult(
            operation="pod.create",
            resource_type="pod",
            resource_id=None if request.dry_run else "pod_new",
            dry_run=request.dry_run,
            changed=not request.dry_run,
            effect="preview" if request.dry_run else "created",
            idempotency_key=request.idempotency_key,
        )

    async def update_endpoint(self, request: EndpointUpdateRequest) -> MutationResult:
        self.endpoint_requests.append(request)
        return MutationResult(
            operation="endpoint.update",
            resource_type="endpoint",
            resource_id=request.resource_id,
            dry_run=request.dry_run,
            changed=not request.dry_run,
            effect="replace workers/scaling/GPU pools",
            idempotency_key=request.idempotency_key,
        )


def _leaf_commands(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
) -> set[tuple[str, ...]]:
    actions = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    if not actions:
        return {prefix}
    result: set[tuple[str, ...]] = set()
    for name, child in actions[0].choices.items():
        result.update(_leaf_commands(child, (*prefix, name)))
    return result


def test_feature_parser_has_the_exact_complete_operation_tree() -> None:
    leaves = _leaf_commands(build_runpod_resources_parser())

    assert len(leaves) == 30
    assert {
        ("catalogue",),
        ("pods", "list"),
        ("pods", "get"),
        ("pods", "create"),
        ("pods", "update"),
        ("pods", "action"),
        ("pods", "terminate"),
        ("endpoints", "list"),
        ("endpoints", "get"),
        ("endpoints", "create"),
        ("endpoints", "update"),
        ("endpoints", "delete"),
        ("templates", "list"),
        ("templates", "get"),
        ("templates", "create"),
        ("templates", "update"),
        ("templates", "delete"),
        ("volumes", "list"),
        ("volumes", "get"),
        ("volumes", "create"),
        ("volumes", "grow"),
        ("volumes", "delete"),
        ("registry-auths", "list"),
        ("registry-auths", "get"),
        ("registry-auths", "create"),
        ("registry-auths", "replace"),
        ("registry-auths", "delete"),
        ("hub", "list"),
        ("hub", "get"),
        ("hub", "search"),
    } == leaves


def test_feature_parser_help_advertises_catalogue(capsys: Any) -> None:
    parser = build_runpod_resources_parser()

    try:
        parser.parse_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:  # pragma: no cover - argparse help always exits
        raise AssertionError("--help did not exit")

    assert "catalogue" in capsys.readouterr().out


def test_catalogue_delegates_to_market_command(monkeypatch: Any) -> None:
    seen: dict[str, object] = {}

    def factory() -> Any:
        raise AssertionError("delegated command must be replaced")

    def command(argv: list[str], *, service_factory: object) -> int:
        seen["argv"] = argv
        seen["factory"] = service_factory
        return 0

    monkeypatch.setattr(cli_runpod_resources, "cmd_runpod_catalogue", command)

    result = cmd_runpod_resources(
        ["catalogue", "--refresh", "--json"],
        market_service_factory=factory,
    )

    assert result == 0
    assert seen == {"argv": ["--refresh", "--json"], "factory": factory}


def test_dry_run_is_zero_write_preview_without_confirmation(capsys: Any) -> None:
    service = StubService()
    request = json.dumps(
        {
            "name": "preview-pod",
            "image": "example/image:1",
            "gpu_type_ids": ["NVIDIA L4"],
            "ttl_minutes": 60,
        }
    )

    result = cmd_runpod_resources(
        [
            "pods",
            "create",
            "--request-json",
            request,
            "--idempotency-key",
            "cli-preview-0001",
            "--dry-run",
            "--json",
        ],
        service=service,  # type: ignore[arg-type]  # reason: focused protocol fake
    )

    assert result == 0
    assert service.pod_requests[0].intent == "preview"
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["changed"] is False


def test_default_dry_run_does_not_open_the_audit_pool(
    capsys: Any,
    monkeypatch: Any,
) -> None:
    async def forbidden_pool() -> object:
        raise AssertionError("dry-run must not open the audit pool")

    monkeypatch.setattr(cli_runpod_resources, "get_pool", forbidden_pool)

    result = cmd_runpod_resources(
        [
            "pods",
            "create",
            "--request-json",
            '{"name":"preview-pod","image":"example/image:1","gpu_type_ids":["NVIDIA L4"],"ttl_minutes":60}',
            "--idempotency-key",
            "cli-no-pool-0001",
            "--dry-run",
            "--json",
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_wrong_confirmation_stops_before_shared_service(capsys: Any) -> None:
    service = StubService()

    result = cmd_runpod_resources(
        [
            "pods",
            "create",
            "--request-json",
            '{"name":"new-pod","image":"example/image:1","gpu_type_ids":["NVIDIA L4"],"ttl_minutes":60}',
            "--idempotency-key",
            "cli-confirm-0001",
            "--confirm",
            "wrong",
            "--json",
        ],
        service=service,  # type: ignore[arg-type]  # reason: focused protocol fake
    )

    assert result == 2
    assert service.pod_requests == []
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_request"


def test_wrong_confirmation_opens_no_pool_and_never_reflects_expected_value(
    capsys: Any,
    monkeypatch: Any,
) -> None:
    canary = "sk-1234567890abcdef1234567890abcdef"

    async def forbidden_pool() -> object:
        raise AssertionError("confirmation must precede the audit pool")

    monkeypatch.setattr(cli_runpod_resources, "get_pool", forbidden_pool)

    result = cmd_runpod_resources(
        [
            "pods",
            "create",
            "--request-json",
            json.dumps(
                {
                    "name": canary,
                    "image": "example/image:1",
                    "gpu_type_ids": ["NVIDIA L4"],
                    "ttl_minutes": 60,
                }
            ),
            "--idempotency-key",
            "cli-confirm-safe-0001",
            "--confirm",
            "wrong",
            "--json",
        ]
    )

    rendered = capsys.readouterr().out
    assert result == 2
    assert canary not in rendered
    assert json.loads(rendered) == {
        "error": "invalid_request",
        "detail": "live mutation requires an exact --confirm value",
    }


def test_exact_confirmation_preserves_nested_endpoint_controls(capsys: Any) -> None:
    service = StubService()
    request = json.dumps(
        {
            "workers": {
                "minimum": 2,
                "maximum": 8,
                "idle_timeout_seconds": 90,
            },
            "scaling": {"type": "REQUEST_COUNT", "value": 3},
            "gpu": {
                "pools": ["ADA_24", "AMPERE_48"],
                "excluded_type_ids": ["GPU-X"],
            },
            "flashboot": True,
        }
    )

    result = cmd_runpod_resources(
        [
            "endpoints",
            "update",
            "ep_one",
            "--request-json",
            request,
            "--idempotency-key",
            "cli-endpoint-0001",
            "--confirm",
            "ep_one",
            "--json",
        ],
        service=service,  # type: ignore[arg-type]  # reason: focused protocol fake
    )

    assert result == 0
    parsed = service.endpoint_requests[0]
    assert parsed.idempotency_key == "cli-endpoint-0001"
    assert parsed.intent == "apply"
    assert (parsed.workers.minimum, parsed.workers.maximum) == (2, 8)
    assert parsed.scaling.type == "REQUEST_COUNT"
    assert parsed.gpu is not None
    assert parsed.gpu.pools == ["ADA_24", "AMPERE_48"]
    assert parsed.gpu.excluded_type_ids == ["GPU-X"]
    assert json.loads(capsys.readouterr().out)["changed"] is True


def test_validation_errors_omit_secret_input(capsys: Any) -> None:
    service = StubService()
    secret = "registry-password-that-must-not-leak"

    result = cmd_runpod_resources(
        [
            "pods",
            "create",
            "--request-json",
            json.dumps(
                {
                    "name": "new-pod",
                    "image": "example/image:1",
                    "gpuTypeIds": ["NVIDIA L4"],
                    "ttl_minutes": 60,
                    "env": {"API_KEY": secret},
                }
            ),
            "--idempotency-key",
            "cli-invalid-0001",
            "--dry-run",
            "--json",
        ],
        service=service,  # type: ignore[arg-type]  # reason: focused protocol fake
    )

    captured = capsys.readouterr()
    assert result == 2
    assert secret not in captured.out
    assert "gpuTypeIds" in captured.out
    assert service.pod_requests == []
